from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class APIModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")


class Page[T](APIModel):
    items: list[T]
    next_cursor: str | None = None


class RunSummary(APIModel):
    total: int = 0
    passed: int = 0
    failed: int = 0
    pass_rate_pct: float = 0
    mean_score: float | None = None
    by_tier: dict[str, dict[str, int]] | None = None


class BaseRun(APIModel):
    id: str | None = Field(default=None, alias="_id")
    run_id: str
    batch_id: str | None = None
    execution_status: Literal["queued", "running", "completed", "error", "cancelled"]
    verdict: Literal["passed", "failed", "blocked", "pending"]
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: float | None = None
    trigger: str | None = None
    actor: str | None = None
    git_sha: str | None = None
    summary: RunSummary


class MetricComparison(APIModel):
    metric: str
    baseline: float | None
    candidate: float | None
    delta: float | None


class HealthResponse(APIModel):
    status: Literal["ok", "not_ready"]
