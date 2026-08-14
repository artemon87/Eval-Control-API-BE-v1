from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.models.common import APIModel, BaseRun, MetricComparison, Verdict


class UnitConfig(APIModel):
    skill_ids: list[str] = Field(min_length=1, max_length=1)
    mode: str | None = None
    metrics: list[str] = Field(default_factory=list)
    bsa_environment: str
    bsa_version: str = "unknown"
    skill_version: str

    @field_validator("bsa_version", mode="before")
    @classmethod
    def default_bsa_version(cls, value: object) -> object:
        return "unknown" if value is None else value


class UnitRun(BaseRun):
    eval_type: Literal["unit"] = "unit"
    skill: str
    environment: str
    unit_config: UnitConfig

    @model_validator(mode="after")
    def validate_single_skill(self) -> "UnitRun":
        if self.unit_config.skill_ids != [self.skill]:
            raise ValueError("unit_config.skill_ids must contain exactly the top-level skill")
        return self


class ToolCall(APIModel):
    name: str
    parameters: dict[str, object] = Field(default_factory=dict)
    tool_call_id: str | None = None


class UnitCase(APIModel):
    id: str | None = Field(default=None, alias="_id")
    case_id: str
    run_id: str
    batch_id: str | None = None
    eval_type: Literal["unit"] = "unit"
    skill: str
    test_name: str
    test_type: str
    tier: int | None = None
    verdict: Verdict
    scores: dict[str, float] = Field(default_factory=dict)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    validation_result: object | None = None
    turns: list[dict[str, object]] | None = None
    response: str | None = None
    error: str | None = None
    skill_version: str
    bsa_version: str = "unknown"

    @field_validator("bsa_version", mode="before")
    @classmethod
    def default_bsa_version(cls, value: object) -> object:
        return "unknown" if value is None else value


class UnitVersionSummary(APIModel):
    skill: str
    environment: str
    version: str
    latest_run_id: str
    started_at: str | None = None
    pass_rate_pct: float


class UnitComparison(APIModel):
    skill: str
    environment: str | None
    baseline_version: str
    candidate_version: str
    baseline_run: UnitRun
    candidate_run: UnitRun
    pass_rate_delta: float
    duration_delta_ms: float | None
    metrics: list[MetricComparison]
