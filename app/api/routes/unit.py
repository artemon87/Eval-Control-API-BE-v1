from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.api.dependencies import UnitRepositoryDependency
from app.models import Page, UnitCase, UnitComparison, UnitRun, UnitVersionSummary
from app.services import build_unit_comparison

router = APIRouter(prefix="/unit", tags=["unit evaluations"])
Limit = Annotated[int, Query(ge=1, le=200)]


@router.get("/runs", response_model=Page[UnitRun])
async def list_unit_runs(
    repository: UnitRepositoryDependency,
    skill: str | None = None,
    environment: str | None = None,
    skill_version: str | None = None,
    verdict: str | None = None,
    limit: Limit = 50,
    cursor: str | None = None,
) -> Page[UnitRun]:
    try:
        items, next_cursor = await repository.list_runs(
            skill=skill,
            environment=environment,
            skill_version=skill_version,
            verdict=verdict,
            limit=limit,
            cursor=cursor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Page(items=[UnitRun.model_validate(item) for item in items], next_cursor=next_cursor)


@router.get("/runs/{run_id}", response_model=UnitRun)
async def get_unit_run(run_id: str, repository: UnitRepositoryDependency) -> UnitRun:
    item = await repository.runs.get_by_run_id(run_id)
    if not item:
        raise HTTPException(status_code=404, detail="Unit run not found")
    return UnitRun.model_validate(item)


@router.get("/runs/{run_id}/cases", response_model=Page[UnitCase])
async def list_unit_cases(
    run_id: str,
    repository: UnitRepositoryDependency,
    verdict: str | None = None,
    limit: Limit = 100,
    cursor: str | None = None,
) -> Page[UnitCase]:
    if not await repository.runs.get_by_run_id(run_id):
        raise HTTPException(status_code=404, detail="Unit run not found")
    try:
        items, next_cursor = await repository.list_cases(
            run_id, verdict=verdict, limit=limit, cursor=cursor
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Page(items=[UnitCase.model_validate(item) for item in items], next_cursor=next_cursor)


@router.get("/skills/{skill}/versions", response_model=list[UnitVersionSummary])
async def list_skill_versions(
    skill: str,
    repository: UnitRepositoryDependency,
    environment: str | None = None,
) -> list[UnitVersionSummary]:
    runs = await repository.latest_runs_by_version(skill, environment)
    def started_at(run: dict[str, object]) -> str | None:
        value = run.get("started_at")
        return value.isoformat() if isinstance(value, datetime) else None

    return [
        UnitVersionSummary(
            skill=run["skill"],
            environment=run["environment"],
            version=run["unit_config"]["skill_version"],
            latest_run_id=run["run_id"],
            started_at=started_at(run),
            pass_rate_pct=run["summary"]["pass_rate_pct"],
        )
        for run in runs
    ]


@router.get("/skills/{skill}/comparison", response_model=UnitComparison)
async def compare_skill_versions(
    skill: str,
    repository: UnitRepositoryDependency,
    environment: str | None = None,
    baseline_version: str | None = None,
    candidate_version: str | None = None,
) -> UnitComparison:
    try:
        return await build_unit_comparison(
            repository,
            skill=skill,
            environment=environment,
            baseline_version=baseline_version,
            candidate_version=candidate_version,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
