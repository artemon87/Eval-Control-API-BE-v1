from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models import UnitRun


def unit_run_document() -> dict[str, object]:
    return {
        "run_id": "unit-1",
        "eval_type": "unit",
        "execution_status": "completed",
        "verdict": "failed",
        "skill": "feedback-skill",
        "environment": "staging",
        "unit_config": {
            "skill_ids": ["feedback-skill"],
            "bsa_environment": "staging",
            "bsa_version": "1.4.0",
            "skill_version": "1.3.0",
        },
        "started_at": datetime(2026, 8, 9, tzinfo=UTC),
        "summary": {"total": 3, "passed": 0, "failed": 3, "pass_rate_pct": 0},
    }


def test_unit_run_accepts_exactly_one_matching_skill() -> None:
    run = UnitRun.model_validate(unit_run_document())
    assert run.unit_config.skill_ids == [run.skill]


def test_unit_run_rejects_multiple_skills() -> None:
    document = unit_run_document()
    document["unit_config"] = {
        "skill_ids": ["feedback-skill", "navigation-skill"],
        "bsa_environment": "staging",
        "bsa_version": "1.4.0",
        "skill_version": "1.3.0",
    }
    with pytest.raises(ValidationError):
        UnitRun.model_validate(document)
