from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class HistoryModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class UnitHistoryScope(HistoryModel):
    all_stages: bool = False
    all_targets: bool = False
    scope_key: str
    skill: str
    github_repository: str | None
    stage: str
    target_environment: str | None
    environment: str


class HistoryTrendReference(HistoryModel):
    run_id: str
    branch: str
    started_at: datetime
    pass_rate_delta: float | None
    duration_delta_ms: float | None


class HistoryRun(HistoryModel):
    source_branch: str | None = None
    trend_references: list[HistoryTrendReference] = Field(default_factory=list)
    trend_context_previous_run_id: str | None = None
    trend_context_changes: list[str] = Field(default_factory=list)
    trend_note: str | None = None
    trend_previous_run_id: str | None = None
    trend_transition: Literal["first", "comparable", "settings_changed", "incomplete"] = "first"
    trend_pass_rate_delta: float | None = None
    trend_duration_delta_ms: float | None = None
    agent_name: str | None = None
    stage: str = "unknown"
    target_environment: str | None = None
    environment: str = "unknown"
    bsa_version: str | None = None
    run_id: str
    branch_key: str
    branch_label: str
    git_sha: str | None
    actor: str | None
    trigger: str | None
    started_at: datetime
    execution_status: str
    verdict: str
    skill_version: str | None
    total: int | None
    passed: int | None
    pass_rate_pct: float | None
    duration_ms: float | None
    case_count: int
    cases_complete: bool
    cohort_key: str | None
    case_set_key: str | None = None
    context_match: bool = False
    quality_measured: bool = False
    blocked_cases: int = 0
    pass_rate_threshold: float | None = None
    error: str | None = None


class HistoryIssue(HistoryModel):
    run_id: str
    branch_key: str
    started_at: datetime
    case_name: str | None = None
    test_type: str | None = None
    error: str | None = None


class HistoryCommit(HistoryModel):
    key: str
    sha: str | None
    first_at: datetime
    last_at: datetime
    run_ids: list[str]
    low_pass_rate: float | None
    high_pass_rate: float | None


class HistoryBranch(HistoryModel):
    key: str
    label: str
    run_count: int
    latest_run_id: str
    latest_actor: str | None
    latest_at: datetime
    direction: Literal["improved", "declined", "unchanged", "insufficient", "context_changed"]
    first_pass_rate: float | None
    latest_pass_rate: float | None
    first_total: int | None
    latest_total: int | None
    latest_context_match: bool
    coverage_expected: int | None
    coverage_actual: int | None
    fewer_cases: bool
    inconsistent_latest: bool
    commits: list[HistoryCommit]


CaseState = Literal[
    "passed",
    "failed",
    "blocked",
    "xpassed",
    "skipped",
    "pending",
    "not_run",
    "unavailable",
]


class HistoryCaseCell(HistoryModel):
    branch_key: str
    executions: int
    failed: int
    passed: int
    blocked: int
    xpassed: int
    skipped: int
    pending: int
    latest_state: CaseState
    latest_run_id: str
    latest_context_match: bool
    latest_at: datetime


class HistoryCase(HistoryModel):
    key: str
    label: str
    test_type: str | None
    executions: int
    failed: int
    affected_branches: int
    first_failed_at: datetime | None
    last_failed_at: datetime | None
    inconsistent_updates: int
    cells: list[HistoryCaseCell]


class HistoryCohort(HistoryModel):
    key: str
    label: str
    run_count: int
    branch_count: int
    case_count: int
    configuration_complete: bool
    details: list[str] = Field(default_factory=list)


class HistorySignals(HistoryModel):
    recurring_case_key: str | None = None
    inconsistent_updates: int = 0
    inconsistent_case_keys: list[str] = Field(default_factory=list)
    coverage_gap_branch_keys: list[str] = Field(default_factory=list)
    excluded_runs: int = 0
    incomplete_case_runs: int = 0
    operational_runs: int = 0


class HistorySettingsGroup(HistoryModel):
    key: str
    label: str
    run_ids: list[str]
    cohort_keys: list[str]


class UnitHistoryInsights(HistoryModel):
    settings_groups: list[HistorySettingsGroup] = Field(default_factory=list)
    scope: UnitHistoryScope
    started_from: datetime
    started_before: datetime
    cohorts: list[HistoryCohort]
    selected_cohort_key: str | None
    runs: list[HistoryRun]
    branches: list[HistoryBranch]
    cases: list[HistoryCase]
    signals: HistorySignals
    execution_issues: list[HistoryIssue] = Field(default_factory=list)
