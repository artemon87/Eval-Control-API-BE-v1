from typing import Any

import pytest

from app.api.routes.unit import list_unit_runs
from app.repositories.unit import UnitRepository


class RouteRepository:
    def __init__(self) -> None:
        self.request: dict[str, Any] | None = None

    async def list_runs(self, **kwargs: Any) -> tuple[list[dict[str, Any]], None]:
        self.request = kwargs
        return (
            [
                {
                    "run_id": "unit-1",
                    "eval_type": "unit",
                    "execution_status": "completed",
                    "verdict": "passed",
                    "skill": "feedback-skill",
                    "environment": "staging",
                    "unit_config": {
                        "skill_ids": ["feedback-skill"],
                        "bsa_environment": "staging",
                        "skill_version": "1.0.0",
                        "category": "domain-skills",
                        "target_environment": "5-prod-sales",
                        "agent_name": "oneview-agent",
                    },
                    "summary": {
                        "total": 1,
                        "passed": 1,
                        "failed": 0,
                        "pass_rate_pct": 100,
                    },
                }
            ],
            None,
        )


class RunsCollection:
    def __init__(self) -> None:
        self.query: dict[str, Any] | None = None

    async def list_by_time(
        self, query: dict[str, Any], *, limit: int, cursor: str | None
    ) -> tuple[list[dict[str, Any]], None]:
        self.query = query
        return [], None


@pytest.mark.asyncio
async def test_unit_run_route_forwards_configuration_filters() -> None:
    repository = RouteRepository()

    page = await list_unit_runs(
        repository,
        environment="staging",
        target_environment="5-prod-sales",
        category="domain-skills",
        agent_name="oneview-agent",
        limit=25,
    )

    assert page.items[0].unit_config.category == "domain-skills"
    assert repository.request == {
        "skill": None,
        "environment": "staging",
        "target_environment": "5-prod-sales",
        "category": "domain-skills",
        "agent_name": "oneview-agent",
        "skill_version": None,
        "verdict": None,
        "limit": 25,
        "cursor": None,
    }


@pytest.mark.asyncio
async def test_unit_repository_filters_nested_configuration_fields() -> None:
    repository = UnitRepository.__new__(UnitRepository)
    runs = RunsCollection()
    repository.runs = runs  # type: ignore[assignment]

    await repository.list_runs(
        skill=None,
        environment="staging",
        target_environment="5-prod-sales",
        category="domain-skills",
        agent_name="oneview-agent",
        skill_version=None,
        verdict=None,
        limit=50,
        cursor=None,
    )

    assert runs.query == {
        "eval_type": "unit",
        "environment": "staging",
        "unit_config.target_environment": "5-prod-sales",
        "unit_config.category": "domain-skills",
        "unit_config.agent_name": "oneview-agent",
    }
