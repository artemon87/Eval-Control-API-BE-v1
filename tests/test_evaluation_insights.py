from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from src.models.evaluation_insights import EvaluationInsights, InsightsQuery
from src.repositories import evaluation_insights as repository
from src.services.evaluation_insights import build_response, cell_key, select_runs

END = datetime(2026, 10, 31, tzinfo=UTC)


def run(identity: str, branch: str = "main", rate: int = 100, **extra: Any) -> dict[str, Any]:
    return {
        "run_id": identity,
        "eval_type": "unit",
        "skill": "payroll",
        "environment": "dev",
        "execution_status": "completed",
        "verdict": "passed" if rate == 100 else "failed",
        "started_at": END - timedelta(days=1),
        "git_ref": f"refs/heads/{branch}",
        "summary": {"total": 1, "passed": int(rate == 100), "pass_rate_pct": rate},
        "unit_config": {"category": "HR", "target_environment": "east", "stage": "dev"},
        **extra,
    }


def case(identity: str, verdict: str = "passed") -> dict[str, Any]:
    return {
        "run_id": identity,
        "eval_type": "unit",
        "case_id": "tax",
        "test_name": "tax",
        "skill": "payroll",
        "verdict": verdict,
        "scores": {"accuracy": 4},
    }


def analyze(
    runs: list[dict[str, Any]], cases: list[dict[str, Any]], **query: Any
) -> dict[str, Any]:
    return build_response(select_runs(runs, InsightsQuery(**query)), cases)


def test_server_window_boundaries_and_validation() -> None:
    for period in ("7d", "30d", "90d"):
        query = InsightsQuery(range=period, window_end=END)
        window = repository.insights_window(query, END + timedelta(hours=1))
        assert window == repository.insights_window(query, END + timedelta(hours=2))
        assert window.started_before == END
        assert window.started_from == END - timedelta(days=int(period[:-1]))
    with pytest.raises(ValidationError):
        InsightsQuery(window_end="2026-10-31T00:00:00")
    with pytest.raises(ValidationError):
        InsightsQuery(range="365d")
    with pytest.raises(ValueError, match="future"):
        repository.insights_window(InsightsQuery(window_end=END + timedelta(days=1)), END)


def test_server_filters_and_e2e_stage_default() -> None:
    rows = [run("a", github_repository="org/a"), run("b", github_repository="org/b")]
    selection = select_runs(rows, InsightsQuery(repository="org/a", scope="payroll"))
    assert len(selection["runs"]) == 1
    assert selection["repositories"] == ["org/a", "org/b"]
    assert selection["activeCategory"] == "HR"
    assert selection["targetOptions"] == ["east"]
    e2e = [
        run(str(i), eval_type="e2e", target="agent", stage="prod" if i else "dev") for i in range(3)
    ]
    assert select_runs(e2e, InsightsQuery(eval_type="e2e"))["activeStage"] == "prod"


def test_server_classifies_regression_fix_and_flakiness() -> None:
    rows = [
        run("a"),
        run("b", "bad", 0),
        run("c", "flaky"),
        run("d", "flaky", 0),
        run("e", "flaky"),
    ]
    for i, row in enumerate(rows):
        row["started_at"] = END - timedelta(hours=10 - i)
    result = analyze(
        rows,
        [case("a"), case("b", "failed"), case("c"), case("d", "failed"), case("e")],
        scope="payroll",
    )
    assert result["landscape"]["cells"][cell_key("payroll", "bad")]["deltaVsBaseline"] == -100
    cells = result["matrix"]["cells"]
    assert cells[cell_key("payroll::tax", "bad")]["state"] == "regression"
    assert cells[cell_key("payroll::tax", "flaky")]["state"] == "flaky"
    assert result["counts"]["branchRegressions"] == 1
    assert result["counts"]["flakyTests"] == 1
    fixed = analyze(
        [run("a", rate=0), run("b", "fix")], [case("a", "failed"), case("b")], scope="payroll"
    )
    assert fixed["matrix"]["cells"][cell_key("payroll::tax", "fix")]["state"] == "fixed"
    EvaluationInsights.model_validate(
        {**result, "window": {"startedFrom": END - timedelta(days=30), "startedBefore": END}}
    )


def test_environment_split_not_mistaken_for_flakiness() -> None:
    rows = [run(str(i), rate=0 if i % 2 else 100) for i in range(1, 5)]
    for i, row in enumerate(rows):
        row["started_at"] = END - timedelta(hours=10 - i)
        row["unit_config"]["stage"] = "dev" if i % 2 == 0 else "prod"
    result = analyze(
        rows, [case(str(i), "failed" if i % 2 else "passed") for i in range(1, 5)], scope="payroll"
    )
    row = result["matrix"]["tests"][0]
    assert not row["flaky"] and not row["bottleneck"]
    assert row["environmentSplit"]["failing"] == ["dev · east"]
    assert result["landscape"]["insights"][0]["kind"] == "environment-gap"


def test_full_window_replaces_both_browser_sampling_caps() -> None:
    result = analyze(
        [run(str(i), f"branch-{i % 15}") for i in range(1050)],
        [case(str(i)) for i in range(1050)],
        scope="payroll",
    )
    assert result["counts"]["runCount"] == 1050
    assert result["counts"]["caseRunCount"] == 1050
    assert sum(c["runsLoaded"] for c in result["matrix"]["branches"]) == 1050
    assert len(result["matrix"]["tests"][0]["history"]) == 1050


class Collection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.queries: list[dict[str, Any]] = []
        self.projections: list[dict[str, int]] = []

    def find(self, query: dict[str, Any], projection: dict[str, int]) -> Any:
        self.queries.append(query)
        self.projections.append(projection)
        rows = [r for r in self.rows if r.get("eval_type") == query["eval_type"]]
        if "run_id" in query:
            rows = [r for r in rows if r["run_id"] in query["run_id"]["$in"]]
        if "started_at" in query:
            bounds = query["started_at"]
            rows = [r for r in rows if bounds["$gte"] <= r["started_at"] < bounds["$lt"]]
        return Cursor(rows)


class Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.maximum = len(rows)

    def sort(self, _: Any) -> "Cursor":
        return self

    def limit(self, maximum: int) -> "Cursor":
        self.maximum = maximum
        return self

    async def to_list(self, *, length: int) -> list[dict[str, Any]]:
        return self.rows[: min(length, self.maximum)]


@pytest.mark.asyncio
async def test_database_half_open_query_and_capacity_errors(monkeypatch: Any) -> None:
    start = END - timedelta(days=7)
    collection = Collection(
        [
            run("before", started_at=start - timedelta(seconds=1)),
            run("start", started_at=start),
            run("end", started_at=END),
        ]
    )
    window = repository.insights_window(InsightsQuery(range="7d", window_end=END), END)
    rows = await repository.load_runs(collection, "unit", window)
    assert [r["run_id"] for r in rows] == ["start"]
    monkeypatch.setattr(repository, "RUN_LIMIT", 0)
    with pytest.raises(repository.InsightsCapacityError):
        await repository.load_runs(collection, "unit", window)
    cases = Collection([case("a"), case("b")])
    monkeypatch.setattr(repository, "CASE_LIMIT", 1)
    with pytest.raises(repository.InsightsCapacityError):
        await repository.load_cases(cases, "unit", ["a", "b"])
    assert not {"prompt", "response", "tool_calls"} & cases.projections[0].keys()


@pytest.mark.asyncio
async def test_chunked_case_queries_do_not_truncate() -> None:
    collection = Collection([case(str(i)) for i in range(1001)])
    result = await repository.load_cases(collection, "unit", [str(i) for i in range(1001)])
    assert len(result) == 1001 and len(collection.queries) == 3


@pytest.mark.asyncio
async def test_router_query_validation_and_response_contract(monkeypatch: Any) -> None:
    import importlib.util
    import sys
    from pathlib import Path
    from types import ModuleType, SimpleNamespace
    from typing import Annotated

    from fastapi import Depends, FastAPI
    from httpx import ASGITransport, AsyncClient

    # The repository snapshot has an unrelated missing src.models.auth module.
    # Isolate the router's repository dependencies; execute the real route and models.
    runs = Collection([run("a"), run("b", "feature", 0)])
    cases = Collection([case("a"), case("b", "failed")])
    repo = SimpleNamespace(
        runs=SimpleNamespace(collection=runs), cases=SimpleNamespace(collection=cases)
    )
    deps = ModuleType("src.api.dependencies")
    dependency = Annotated[Any, Depends(lambda: repo)]
    deps.UnitRepositoryDependency = dependency
    deps.E2ERepositoryDependency = dependency
    with monkeypatch.context() as patch:
        patch.setitem(sys.modules, "src.api.dependencies", deps)
        spec = importlib.util.spec_from_file_location(
            "isolated_insights_route",
            Path(__file__).parents[1] / "src/api/routes/evaluation_insights.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    app = FastAPI()
    app.include_router(module.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/history/insights",
            params={
                "eval_type": "unit",
                "range": "30d",
                "scope": "payroll",
                "window_end": END.isoformat(),
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["counts"]["runCount"] == 2
        assert payload["matrix"]["tests"][0]["regressions"] == 1
        EvaluationInsights.model_validate(payload)
        for params in (
            {"range": "365d"},
            {"eval_type": "bad"},
            {"window_end": "2026-10-31T00:00:00"},
        ):
            assert (await client.get("/history/insights", params=params)).status_code == 422
        assert (
            await client.get("/history/insights?window_end=2100-01-01T00:00:00Z")
        ).status_code == 400
        e2e = await client.get("/history/insights?eval_type=e2e")
        assert e2e.status_code == 200 and e2e.json()["counts"]["runCount"] == 0
        monkeypatch.setattr(repository, "RUN_LIMIT", 0)
        assert (
            await client.get("/history/insights", params={"window_end": END.isoformat()})
        ).status_code == 413


def test_recorded_verdict_aliases_match_existing_api_normalization() -> None:
    from src.services.evaluation_insights import outcome

    assert outcome(" PASS ") == "pass"
    assert outcome("unexpected_pass") == "pass"
    assert outcome(" FAIL ") == "fail"
    assert outcome("error") == "error"
    assert outcome("skipped") == "skip"
