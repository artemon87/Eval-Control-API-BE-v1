from copy import deepcopy
from typing import Any

import pytest

from app.services import build_unit_comparison


def run(version: str, pass_rate: float, duration: float, run_id: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "eval_type": "unit",
        "execution_status": "completed",
        "verdict": "passed" if pass_rate >= 90 else "failed",
        "skill": "feedback-skill",
        "environment": "staging",
        "unit_config": {
            "skill_ids": ["feedback-skill"],
            "bsa_environment": "staging",
            "bsa_version": "1.4.0",
            "skill_version": version,
        },
        "duration_ms": duration,
        "summary": {"total": 10, "passed": 9, "failed": 1, "pass_rate_pct": pass_rate},
    }


class FakeRepository:
    def __init__(self) -> None:
        self.runs = [run("1.3.0", 90, 1200, "new"), run("1.2.0", 80, 1500, "old")]

    async def latest_runs_by_version(
        self, skill: str, environment: str | None, limit: int = 20
    ) -> list[dict[str, Any]]:
        return deepcopy(self.runs)

    async def latest_run_for_version(
        self, skill: str, version: str, environment: str | None
    ) -> dict[str, Any] | None:
        return deepcopy(
            next(item for item in self.runs if item["unit_config"]["skill_version"] == version)
        )

    async def cases_for_runs(self, run_ids: list[str]) -> list[dict[str, Any]]:
        return [
            {"run_id": "new", "scores": {"GENERAL_QUALITY": 5, "tool_use_quality": 4}},
            {"run_id": "old", "scores": {"GENERAL_QUALITY": 4, "tool_use_quality": 3}},
        ]


@pytest.mark.asyncio
async def test_default_comparison_is_latest_vs_previous() -> None:
    result = await build_unit_comparison(  # type: ignore[arg-type]
        FakeRepository(),
        skill="feedback-skill",
        environment="staging",
        baseline_version=None,
        candidate_version=None,
    )
    assert result.candidate_version == "1.3.0"
    assert result.baseline_version == "1.2.0"
    assert result.pass_rate_delta == 10
    assert result.duration_delta_ms == -300
    assert {metric.metric: metric.delta for metric in result.metrics} == {
        "general_quality": 1,
        "tool_use_quality": 1,
    }
