from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from src.models.unit import UnitSkillOption
from src.repositories.unit import UnitRepository


@pytest.mark.asyncio
async def test_catalog_is_not_limited_to_a_run_page_and_preserves_repository_scope() -> None:
    options = [
        {"skill": f"skill-{index}", "github_repository": "org/a"}
        for index in range(240)
    ] + [
        {"skill": "skill-0", "github_repository": "org/b"},
        {"skill": "legacy-skill", "github_repository": None},
    ]
    cursor = AsyncMock()
    cursor.to_list.return_value = options
    collection = AsyncMock()
    collection.aggregate.return_value = cursor
    repository = object.__new__(UnitRepository)
    repository.runs = cast(Any, type("Runs", (), {"collection": collection})())

    result = await repository.list_skills()

    assert len(result) == 242
    assert [UnitSkillOption.model_validate(item).model_dump() for item in result] == options
    cursor.to_list.assert_awaited_once_with(length=None)
    pipeline = collection.aggregate.call_args.args[0]
    assert pipeline[0]["$match"]["eval_type"] == "unit"
    assert "execution_status" not in pipeline[0]["$match"]
    assert not any("$limit" in step or "$skip" in step for step in pipeline)
    assert pipeline[1]["$group"]["_id"] == {
        "skill": "$skill",
        "github_repository": {"$ifNull": ["$github_repository", None]},
    }
    assert pipeline[-1] == {"$sort": {"skill": 1, "github_repository": 1}}
