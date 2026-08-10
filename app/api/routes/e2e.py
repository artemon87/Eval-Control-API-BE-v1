from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.api.dependencies import E2ERepositoryDependency
from app.models import E2ECase, E2ERun, Page

router = APIRouter(prefix="/e2e", tags=["E2E evaluations"])
Limit = Annotated[int, Query(ge=1, le=200)]


@router.get("/runs", response_model=Page[E2ERun])
async def list_e2e_runs(
    repository: E2ERepositoryDependency,
    stage: str | None = None,
    target: str | None = None,
    verdict: str | None = None,
    limit: Limit = 50,
    cursor: str | None = None,
) -> Page[E2ERun]:
    try:
        items, next_cursor = await repository.list_runs(
            stage=stage, target=target, verdict=verdict, limit=limit, cursor=cursor
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Page(items=[E2ERun.model_validate(item) for item in items], next_cursor=next_cursor)


@router.get("/runs/{run_id}", response_model=E2ERun)
async def get_e2e_run(run_id: str, repository: E2ERepositoryDependency) -> E2ERun:
    item = await repository.runs.get_by_run_id(run_id)
    if not item:
        raise HTTPException(status_code=404, detail="E2E run not found")
    return E2ERun.model_validate(item)


@router.get("/runs/{run_id}/cases", response_model=Page[E2ECase])
async def list_e2e_cases(
    run_id: str,
    repository: E2ERepositoryDependency,
    suite: str | None = None,
    verdict: str | None = None,
    limit: Limit = 100,
    cursor: str | None = None,
) -> Page[E2ECase]:
    if not await repository.runs.get_by_run_id(run_id):
        raise HTTPException(status_code=404, detail="E2E run not found")
    try:
        items, next_cursor = await repository.list_cases(
            run_id, suite=suite, verdict=verdict, limit=limit, cursor=cursor
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Page(items=[E2ECase.model_validate(item) for item in items], next_cursor=next_cursor)
