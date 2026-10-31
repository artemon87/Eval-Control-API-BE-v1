from typing import Annotated, Literal

from pydantic import Field

from src.models.common import APIModel
from src.models.e2e import E2ERun
from src.models.unit import UnitRun

InboxRun = Annotated[UnitRun | E2ERun, Field(discriminator="eval_type")]


class OverviewCounts(APIModel):
    runs: int = 0
    completed: int = 0
    passed: int = 0
    failed: int = 0
    blocked: int = 0
    xpassed: int = 0
    degraded: int = 0
    attention: int = 0
    running: int = 0
    queued: int = 0
    total_cases: int = 0
    passed_cases: int = 0
    timed_completed: int = 0
    p95_duration_ms: float | None = None


class OverviewDay(OverviewCounts):
    date: str
    eval_type: Literal["unit", "e2e"] | None = None


class AttentionRun(APIModel):
    run: InboxRun
    degraded: bool = False
    previous_mean_score: float | None = None


class Overview(APIModel):
    started_from: str
    started_before: str
    totals: dict[str, OverviewCounts]
    days: list[OverviewDay]
    recent_runs: list[InboxRun]
    blocked_runs: list[InboxRun]
    attention_runs: list[AttentionRun]


class UnitFacet(APIModel):
    skill: str
    category: str | None = None
    environment: str | None = None
    stage: str | None = None
    target_environment: str | None = None
    agent_name: str | None = None
    evaluation_layer: str | None = None
    github_repository: str | None = None


class E2EFacet(APIModel):
    stage: str | None = None
    target: str
    github_repository: str | None = None


class GitRefFacet(APIModel):
    github_repository: str | None = None
    git_ref: str


class Facets(APIModel):
    unit: list[UnitFacet]
    e2e: list[E2EFacet]
    git_refs: list[GitRefFacet]
    actors: list[str]
    triggers: list[str]
    pass_rate_thresholds: list[float]
