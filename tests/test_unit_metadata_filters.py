from typing import Any, cast

import pytest

from src.api.routes.unit import list_unit_runs
from src.models import GitFilters
from src.repositories.unit import UnitRepository


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
                        "stage": "5-prod-sales",
                        "target_environment": "us-east4-prod-sales",
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


class AggregateCursor:
    async def to_list(self, *, length: int | None) -> list[dict[str, Any]]:
        assert length is None
        return [
            {
                "_id": "unit-1",
                "mean_score": 4.775,
                "mean_scores": {
                    "correctness": 4.75,
                    "tool_use_quality": 4.8,
                },
            }
        ]


class CaseCollection:
    def __init__(self) -> None:
        self.pipeline: list[dict[str, Any]] | None = None

    async def aggregate(self, pipeline: list[dict[str, Any]]) -> AggregateCursor:
        self.pipeline = pipeline
        return AggregateCursor()


class CasesRepository:
    def __init__(self) -> None:
        self.collection = CaseCollection()


@pytest.mark.asyncio
async def test_unit_run_route_forwards_configuration_filters() -> None:
    repository = RouteRepository()

    git_filters = GitFilters(
        github_repository="ukg/eval-control",
        git_ref="feature/grouping",
        git_sha="abc123",
    )
    page = await list_unit_runs(
        cast(UnitRepository, repository),
        git_filters,
        environment="staging",
        stage=["5-prod-sales"],
        target_environment="us-east4-prod-sales",
        category="domain-skills",
        agent_name="oneview-agent",
        limit=25,
    )

    assert page.items[0].unit_config.category == "domain-skills"
    assert page.items[0].unit_config.stage == "5-prod-sales"
    assert repository.request == {
        "skill": None,
        "environment": "staging",
        "stage": ["5-prod-sales"],
        "target_environment": "us-east4-prod-sales",
        "category": "domain-skills",
        "agent_name": "oneview-agent",
        "skill_version": None,
        "verdict": None,
        "git_filters": git_filters,
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
        stage=["5-prod-sales", "4-prod"],
        target_environment="us-east4-prod-sales",
        category="domain-skills",
        agent_name="oneview-agent",
        skill_version=None,
        verdict=None,
        git_filters=GitFilters(
            github_repository="ukg/eval-control",
            git_ref="feature/grouping",
            git_sha="abc123",
        ),
        limit=50,
        cursor=None,
    )

    assert runs.query == {
        "eval_type": "unit",
        "environment": "staging",
        "unit_config.stage": {"$in": ["5-prod-sales", "4-prod"]},
        "unit_config.target_environment": "us-east4-prod-sales",
        "unit_config.category": "domain-skills",
        "unit_config.agent_name": "oneview-agent",
        "github_repository": "ukg/eval-control",
        "git_ref": "feature/grouping",
        "git_sha": "abc123",
    }


@pytest.mark.asyncio
async def test_unit_repository_aggregates_mean_score_from_case_metrics() -> None:
    repository = UnitRepository.__new__(UnitRepository)
    cases = CasesRepository()
    repository.cases = cast(Any, cases)
    runs = [
        {
            "run_id": "unit-1",
            "summary": {
                "total": 1,
                "passed": 1,
                "failed": 0,
                "pass_rate_pct": 100,
            },
        },
        {
            "run_id": "unit-without-scores",
            "summary": {
                "total": 1,
                "passed": 0,
                "failed": 1,
                "pass_rate_pct": 0,
            },
        },
    ]

    enriched = await repository._with_mean_scores(runs)

    assert enriched[0]["summary"]["mean_score"] == pytest.approx(4.775)
    assert enriched[0]["summary"]["mean_scores"] == {
        "correctness": pytest.approx(4.75),
        "tool_use_quality": pytest.approx(4.8),
    }
    assert enriched[1]["summary"]["mean_score"] is None
    assert enriched[1]["summary"]["mean_scores"] == {}
    assert cases.collection.pipeline == [
        {
            "$match": {
                "eval_type": "unit",
                "run_id": {"$in": ["unit-1", "unit-without-scores"]},
            }
        },
        {
            "$project": {
                "run_id": 1,
                "scores": {
                    "$objectToArray": {"$ifNull": ["$scores", {}]},
                },
            }
        },
        {"$unwind": "$scores"},
        {
            "$group": {
                "_id": {
                    "run_id": "$run_id",
                    "metric": "$scores.k",
                },
                "mean_score": {"$avg": "$scores.v"},
                "score_total": {"$sum": "$scores.v"},
                "score_count": {"$sum": 1},
            }
        },
        {
            "$group": {
                "_id": "$_id.run_id",
                "score_total": {"$sum": "$score_total"},
                "score_count": {"$sum": "$score_count"},
                "mean_scores": {
                    "$push": {
                        "k": "$_id.metric",
                        "v": "$mean_score",
                    }
                },
            }
        },
        {
            "$project": {
                "mean_score": {
                    "$divide": ["$score_total", "$score_count"],
                },
                "mean_scores": {"$arrayToObject": "$mean_scores"},
            }
        },
    ]
