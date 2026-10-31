from fastapi import APIRouter, HTTPException, Request

from src.api.dependencies import GitFilterDependency, GroupingFilterDependency, TimeWindowDependency
from src.models.analytics import Facets, Overview
from src.repositories.analytics import AnalyticsRepository

router = APIRouter(tags=["analytics"])


@router.get("/overview", response_model=Overview)
async def get_overview(
    request: Request,
    git_filters: GitFilterDependency,
    grouping_filters: GroupingFilterDependency,
    time_window: TimeWindowDependency,
    skill: str | None = None,
    agent_name: str | None = None,
    environment: str | None = None,
    e2e_stage: str | None = None,
    e2e_target: str | None = None,
) -> Overview:
    repository = AnalyticsRepository(request.app.state.database, request.app.state.settings)
    try:
        result = await repository.overview(
            time_window=time_window,
            git_filters=git_filters,
            grouping_filters=grouping_filters,
            skill=skill,
            agent_name=agent_name,
            environment=environment,
            e2e_stage=e2e_stage,
            e2e_target=e2e_target,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Overview.model_validate(result)


@router.get("/facets", response_model=Facets)
async def get_facets(request: Request, time_window: TimeWindowDependency) -> Facets:
    repository = AnalyticsRepository(request.app.state.database, request.app.state.settings)
    try:
        result = await repository.facets(time_window=time_window)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Facets.model_validate(result)
