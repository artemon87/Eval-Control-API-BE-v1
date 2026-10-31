from datetime import UTC, datetime, timedelta

import pytest

from src.models.common import TimeWindow
from src.repositories.analytics import build_overview, number, resolve_window, scope

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)


def run(
    index, score=0.8, *, target="east", at=None, status="completed", result="passed", kind="unit"
):
    return {
        "_id": str(index),
        "run_id": str(index),
        "eval_type": kind,
        "github_repository": "org/repo",
        "skill": "skill",
        "environment": "runner",
        "stage": "stage",
        "target": "target",
        "unit_config": {"stage": "stage", "target_environment": target},
        "started_at": at or NOW + timedelta(seconds=index),
        "execution_status": status,
        "verdict": result,
        "duration_ms": index,
        "summary": {"mean_score": score, "total": 2, "passed": 2},
    }


@pytest.mark.parametrize("days", [7, 30, 90, 92])
def test_fixed_windows(days):
    actual = resolve_window(TimeWindow(started_from=NOW - timedelta(days=days), started_before=NOW))
    assert actual.started_before - actual.started_from == timedelta(days=days)


@pytest.mark.parametrize("days", [0, -1, 93])
def test_invalid_windows(days):
    with pytest.raises(ValueError):
        resolve_window(TimeWindow(started_from=NOW - timedelta(days=days), started_before=NOW))


def test_default_window():
    assert resolve_window(None, NOW).started_from == NOW - timedelta(days=7)


def test_complete_totals_and_capped_inbox():
    result = build_overview([run(i, result="failed") for i in range(501)], [])
    assert result["totals"]["unit"]["runs"] == 501
    assert result["totals"]["all"]["attention"] == 501
    assert len(result["attention_runs"]) == 20
    assert result["attention_runs"][0]["run"]["run_id"] == "500"
    assert result["totals"]["unit"]["p95_duration_ms"] == 475


def test_pre_window_and_same_timestamp_predecessors():
    result = build_overview(
        [run(1, 0.5), run(2, 0.6, at=NOW + timedelta(seconds=1))], [run(0, 0.8)]
    )
    assert result["totals"]["unit"]["degraded"] == 2
    assert all(e["previous_mean_score"] == 0.8 for e in result["attention_runs"])


def test_scopes_isolate_targets_environments_and_repositories():
    a = run(0)
    assert scope(a) != scope(run(1, target="west"))
    assert scope(a) != scope({**a, "environment": "other"})
    assert scope(a) != scope({**a, "github_repository": "org/other"})
    assert build_overview([run(1, 0.1, target="west")], [a])["totals"]["unit"]["degraded"] == 0


def test_missing_repository_and_noncompleted_runs_do_not_become_baselines():
    a = {**run(0), "github_repository": None}
    assert scope(a) is None
    result = build_overview([run(2, 0.1)], [a, run(1, 0.9, status="running")])
    assert result["totals"]["unit"]["degraded"] == 0


def test_errors_blocked_and_zero_duration():
    result = build_overview([run(0), run(1, status="error"), run(2, status="queued")], [])
    counts = result["totals"]["unit"]
    assert counts["blocked"] == 1
    assert counts["attention"] == 0
    assert counts["queued"] == 1
    assert counts["timed_completed"] == 1
    assert counts["p95_duration_ms"] == 0


@pytest.mark.parametrize("value", [True, False, None, "nan", "inf"])
def test_invalid_numbers(value):
    assert number(value) is None


def test_partial_utc_day_included():
    result = build_overview([run(0, at=NOW - timedelta(days=7)), run(1)], [])
    assert {r["date"] for r in result["days"]} == {"2026-10-24", "2026-10-31"}


@pytest.mark.asyncio
async def test_repository_applies_bounds_filters_batches_and_rehydrates():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from src.models.common import GitFilters, GroupingFilters
    from src.repositories.analytics import AnalyticsRepository

    class Cursor:
        def __init__(self, items):
            self.items = items

        def sort(self, _):
            return self

        async def to_list(self, length=None):
            return self.items

    class Collection:
        def __init__(self, items):
            self.items, self.queries, self.pipelines = items, [], []

        def find(self, query, projection=None):
            self.queries.append(query)
            return Cursor(self.items)

        async def aggregate(self, pipeline):
            self.pipelines.append(pipeline)
            return Cursor([])

    repository = AnalyticsRepository.__new__(AnalyticsRepository)
    unit = Collection([run(i) for i in range(601)])
    e2e = Collection([])
    repository.collections = {"unit": unit, "e2e": e2e}
    repository.unit = SimpleNamespace(_with_mean_scores=AsyncMock(side_effect=lambda items: items))
    result = await repository.overview(
        time_window=TimeWindow(
            started_from=NOW - timedelta(days=7), started_before=NOW + timedelta(days=1)
        ),
        git_filters=GitFilters(github_repository="org/repo"),
        grouping_filters=GroupingFilters(stage=["stage"], target_environment="east"),
        environment="runner",
        e2e_stage="stage2",
        e2e_target="target2",
    )
    assert result["totals"]["unit"]["runs"] == 601
    assert repository.unit._with_mean_scores.await_count == 2
    assert unit.queries[0]["started_at"] == {
        "$gte": NOW - timedelta(days=7),
        "$lt": NOW + timedelta(days=1),
    }
    assert unit.queries[0]["environment"] == "runner"
    assert unit.queries[0]["unit_config.target_environment"] == "east"
    assert e2e.queries[0]["stage"] == "stage2"
    assert e2e.queries[0]["target"] == "target2"
    assert unit.pipelines[0][0]["$match"]["started_at"] == {"$lt": NOW - timedelta(days=7)}


@pytest.mark.asyncio
async def test_null_metric_mean_does_not_crash_overview_enrichment():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from src.repositories.unit import UnitRepository

    repo = UnitRepository.__new__(UnitRepository)
    cursor = SimpleNamespace(
        to_list=AsyncMock(
            return_value=[
                {
                    "_id": "1",
                    "mean_score": 0.5,
                    "mean_scores": {"present": 0.5, "empty": None, "zero": 0},
                }
            ]
        )
    )
    repo.cases = SimpleNamespace(
        collection=SimpleNamespace(aggregate=AsyncMock(return_value=cursor))
    )
    enriched = await repo._with_mean_scores([run(1)])
    assert enriched[0]["summary"]["mean_scores"] == {"present": 0.5, "zero": 0}
