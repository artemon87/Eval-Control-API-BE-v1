from typing import Literal

from pydantic import Field

from app.models.common import APIModel, BaseRun


class E2EConfig(APIModel):
    selected_suites: list[str]
    max_tier: int | None = None
    live_conversation: bool = True


class E2ERun(BaseRun):
    eval_type: Literal["e2e"] = "e2e"
    stage: str
    target: str
    e2e_config: E2EConfig


class E2ECase(APIModel):
    id: str | None = Field(default=None, alias="_id")
    case_id: str
    run_id: str
    batch_id: str | None = None
    eval_type: Literal["e2e"] = "e2e"
    suite: str
    role: str | None = None
    tier: int | None = None
    verdict: Literal["passed", "failed", "error"]
    status: str | None = None
    score: float | None = None
    threshold: float | None = None
    response_time_ms: float | None = None
    response_text: str | None = None
    explanation: str | None = None
    error: str | None = None
    bug_ref: str | None = None

