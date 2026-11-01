from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.models.unit_history import HistoryModel


class HistoryWindowQuery(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    period: Literal["7d", "30d", "90d"] = Field(default="30d", alias="range")
    window_end: datetime | None = None

    @field_validator("window_end")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("window_end must include a timezone")
        return value


class InsightsQuery(HistoryWindowQuery):
    eval_type: Literal["unit", "e2e"] = "unit"
    repository: str = Field(default="all", max_length=1024)
    category: str | None = Field(default=None, max_length=1024)
    stage: str | None = Field(default=None, max_length=1024)
    target_environment: str = Field(default="all", max_length=1024)
    scope: str | None = Field(default=None, max_length=1024)
    baseline: str | None = Field(default=None, max_length=1024)


class InsightsWindow(HistoryModel):
    started_from: datetime
    started_before: datetime


class InsightsCounts(HistoryModel):
    run_count: int
    scope_count: int
    branch_count: int
    regressed_cells: int
    systemic_scopes: int
    case_run_count: int
    tests_tracked: int
    bottleneck_tests: int
    branch_regressions: int
    flaky_tests: int


class Insight(HistoryModel):
    id: str
    severity: Literal["critical", "warning", "good", "info"]
    kind: Literal[
        "bottleneck",
        "branch-regression",
        "branch-fix",
        "flaky",
        "systemic",
        "baseline-drift",
        "concentration",
        "coverage",
        "environment-gap",
    ]
    title: str
    detail: str
    scope: str | None = None
    branch: str | None = None
    test_key: str | None = None


class InsightsSummary(HistoryModel):
    total: int
    pass_rate_pct: float


class InsightsRun(HistoryModel):
    run_id: str
    eval_type: Literal["unit", "e2e"]
    target: str
    stage: str
    context: str
    started_at: str
    verdict: str
    git_sha: str | None = None
    summary: InsightsSummary


class ScopeBranchCell(HistoryModel):
    scope: str
    branch: str
    runs: list[InsightsRun]
    latest: InsightsRun
    latest_pass_rate: float
    mean_pass_rate: float
    delta_vs_baseline: float | None
    regressed: bool
    improved: bool


class BranchRate(HistoryModel):
    branch: str
    pass_rate: float


class ScopeRow(HistoryModel):
    scope: str
    baseline_pass_rate: float | None
    branches_regressed: int
    worst_branch: BranchRate | None
    mean_pass_rate: float
    run_count: int
    systemic: bool


class BranchColumn(HistoryModel):
    branch: str
    is_baseline: bool
    scopes_regressed: int
    scopes_improved: int
    run_count: int
    mean_pass_rate: float
    last_run_at: str
    actors: list[str]


class Landscape(HistoryModel):
    scopes: list[ScopeRow]
    branches: list[BranchColumn]
    cells: dict[str, ScopeBranchCell]
    insights: list[Insight]


class Observation(HistoryModel):
    run_id: str
    started_at: str
    context: str
    git_sha: str | None = None
    outcome: Literal["pass", "fail", "error", "skip"]
    score: float


class TestBranchCell(HistoryModel):
    test_key: str
    branch: str
    observations: list[Observation]
    latest: Observation
    passes: int
    failures: int
    flips: int
    state: Literal["pass", "fail", "regression", "fixed", "flaky", "missing"]


class EnvironmentSplit(HistoryModel):
    branch: str
    failing: list[str]
    passing: list[str]


class TestRow(HistoryModel):
    test_key: str
    label: str
    group: str
    failing_branches: int
    branches_with_data: int
    regressions: int
    fixes: int
    flaky: bool
    fails_on_baseline: bool
    bottleneck: bool
    branch_specific: str | None
    environment_split: EnvironmentSplit | None
    failure_count: int
    history: list[Observation]


class BranchTestColumn(HistoryModel):
    branch: str
    is_baseline: bool
    regressions: int
    fixes: int
    pass_rate: float
    tests_run: int
    runs_loaded: int


class TestMatrix(HistoryModel):
    tests: list[TestRow]
    branches: list[BranchTestColumn]
    cells: dict[str, TestBranchCell]
    insights: list[Insight]
    total_failures: int


class EvaluationInsights(HistoryModel):
    window: InsightsWindow
    repositories: list[str]
    categories: list[str]
    scope_options: list[str]
    stage_options: list[str]
    target_options: list[str]
    active_category: str
    active_stage: str
    active_target: str
    active_scope: str | None
    baseline: str | None
    branch_names: list[str]
    mixed_stages: int
    mixed_targets: int
    counts: InsightsCounts
    regression_points: int
    # Presentation graphs have the same cells/rows as the existing Insights UI.
    landscape: Landscape
    matrix: TestMatrix | None
    coverage: Literal["complete"] = "complete"
    rules_version: str = "insights-v1"
