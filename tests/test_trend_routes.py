from typing import Any

import pytest

from app.api.routes.e2e import e2e_case_trend
from app.api.routes.unit import unit_skill_trend


class TrendRepository:
    def __init__(self) -> None:
        self.request: dict[str, Any] | None = None

    async def trend(self, **kwargs: Any) -> tuple[list[dict[str, Any]], str]:
        self.request = kwargs
        return ([{
            "run_id": "e2e-1" if kwargs.get("scope") == "case" else "unit-1",
            "started_at": "2026-08-14T12:00:00Z",
            "verdict": "xpassed",
            "score": 5,
            "threshold": 3,
            "pass_rate_pct": 100,
            "total_cases": 1,
        }], "next-page")


@pytest.mark.asyncio
async def test_e2e_case_trend_is_scoped_and_cursor_paginated() -> None:
    repository = TrendRepository()
    page = await e2e_case_trend(
        "navigation::timecard",
        repository,
        stage="1-dev-staging",
        target="us-east4-dev-staging",
        suite="navigation",
        limit=30,
        cursor="cursor-1",
    )

    assert page.next_cursor == "next-page"
    assert page.items[0].verdict == "xpassed"
    assert repository.request == {
        "scope": "case",
        "value": "navigation::timecard",
        "stage": "1-dev-staging",
        "target": "us-east4-dev-staging",
        "suite": "navigation",
        "limit": 30,
        "cursor": "cursor-1",
    }


@pytest.mark.asyncio
async def test_unit_skill_trend_is_scoped_and_cursor_paginated() -> None:
    repository = TrendRepository()
    page = await unit_skill_trend(
        "feedback-skill",
        repository,
        environment="staging",
        limit=25,
        cursor=None,
    )

    assert page.next_cursor == "next-page"
    assert repository.request == {
        "scope": "skill",
        "skill": "feedback-skill",
        "case_id": None,
        "environment": "staging",
        "limit": 25,
        "cursor": None,
    }
