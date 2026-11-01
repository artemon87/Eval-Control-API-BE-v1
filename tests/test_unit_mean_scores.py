from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from src.models import UnitRun
from src.repositories.unit import UnitRepository


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["list", "detail"])
@pytest.mark.parametrize(
    ("mean_scores", "mean_score", "expected_scores"),
    [
        (
            {"correctness": 4.5, "unscored": None, "safety": 0},
            1.5,
            {"correctness": 4.5, "safety": 0.0},
        ),
        ({"correctness": None, "safety": None}, 0.0, {}),
        ({"correctness": None}, None, {}),
    ],
    ids=["mixed-null-numeric-and-zero", "all-null-metrics", "null-overall-mean"],
)
async def test_unit_runs_omit_null_metric_means(
    operation: str,
    mean_scores: dict[str, float | None],
    mean_score: float | None,
    expected_scores: dict[str, float],
) -> None:
    run: dict[str, Any] = {
        "run_id": "unit-1",
        "execution_status": "completed",
        "verdict": "passed",
        "skill": "feedback-skill",
        "environment": "staging",
        "unit_config": {
            "skill_ids": ["feedback-skill"],
            "bsa_environment": "staging",
            "skill_version": "1.0.0",
        },
        "summary": {"total": 1, "passed": 1, "pass_rate_pct": 100},
    }
    runs = [run, {**run, "run_id": "healthy-run"}, {**run, "run_id": "no-scores"}]
    run_repository = AsyncMock()
    run_repository.list_by_time.return_value = (runs, "older-runs")
    run_repository.get_by_run_id.return_value = run
    case_repository = AsyncMock()
    # MongoDB's $avg returns null when a metric has no numeric scores.
    case_repository.collection.aggregate.return_value.to_list.return_value = [
        {"_id": "unit-1", "mean_score": mean_score, "mean_scores": mean_scores},
        {"_id": "healthy-run", "mean_score": 5.0, "mean_scores": {"correctness": 5.0}},
    ]
    repository = UnitRepository.__new__(UnitRepository)
    repository.runs = cast(Any, run_repository)
    repository.cases = cast(Any, case_repository)

    if operation == "list":
        items, cursor = await repository.list_runs(
            skill=None,
            environment=None,
            grouping_filters=None,
            agent_name=None,
            skill_version=None,
            verdict=None,
            limit=200,
            cursor=None,
        )
        assert cursor == "older-runs"
        assert [item["run_id"] for item in items] == ["unit-1", "healthy-run", "no-scores"]
        assert items[1]["summary"]["mean_scores"] == {"correctness": 5.0}
        assert items[1]["summary"]["mean_score"] == 5.0
        assert items[2]["summary"]["mean_scores"] == {}
        assert items[2]["summary"]["mean_score"] is None
        result = items[0]
    else:
        detail = await repository.get_run("unit-1")
        assert detail is not None
        result = detail

    # The same response validation used by the list and detail endpoints must succeed.
    summary = UnitRun.model_validate(result).summary
    assert summary.mean_scores == expected_scores
    assert summary.mean_score == mean_score
    assert "mean_scores" not in run["summary"]
