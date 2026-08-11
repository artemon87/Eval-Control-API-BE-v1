from typing import Any

import pytest

from app.api.routes.e2e import list_e2e_cases
from app.api.routes.unit import list_unit_cases


class CaseRepository:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items
        self.requested_run_id: str | None = None

    async def list_cases(self, run_id: str, **_: Any) -> tuple[list[dict[str, Any]], None]:
        self.requested_run_id = run_id
        return self.items, None


@pytest.mark.asyncio
async def test_e2e_case_list_queries_cases_without_second_run_lookup() -> None:
    repository = CaseRepository(
        [
            {
                "case_id": "navigation::one",
                "run_id": "e2e-1",
                "suite": "navigation",
                "verdict": "passed",
            }
        ]
    )

    page = await list_e2e_cases("e2e-1", repository, limit=100)

    assert repository.requested_run_id == "e2e-1"
    assert [item.case_id for item in page.items] == ["navigation::one"]


@pytest.mark.asyncio
async def test_unit_case_list_queries_cases_without_second_run_lookup() -> None:
    repository = CaseRepository(
        [{
            "case_id": "feedback-001",
            "run_id": "unit-1",
            "skill": "feedback-skill",
            "test_name": "Positive feedback",
            "test_type": "single-turn",
            "verdict": "passed",
            "skill_version": "1.3.0",
            "bsa_version": "1.4.0",
        }]
    )

    page = await list_unit_cases("unit-1", repository, limit=100)

    assert repository.requested_run_id == "unit-1"
    assert [item.case_id for item in page.items] == ["feedback-001"]
