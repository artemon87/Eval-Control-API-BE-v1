from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from src.api.dependencies import E2ERepositoryDependency, UnitRepositoryDependency
from src.models.evaluation_insights import (
    EvaluationInsights,
    HistoryWindowQuery,
    InsightsQuery,
    InsightsWindow,
)
from src.repositories.evaluation_insights import (
    InsightsCapacityError,
    insights_window,
    load_cases,
    load_runs,
)
from src.services.evaluation_insights import build_response, select_runs

router = APIRouter(prefix="/history", tags=["evaluation insights"])


@router.get("/window", response_model=InsightsWindow)
async def resolve_history_window(query: Annotated[HistoryWindowQuery, Query()]) -> InsightsWindow:
    try:
        window = insights_window(query)
        return InsightsWindow.model_validate(
            {"startedFrom": window.started_from, "startedBefore": window.started_before}
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/insights", response_model=EvaluationInsights)
async def evaluation_insights(
    query: Annotated[InsightsQuery, Query()],
    unit_repository: UnitRepositoryDependency,
    e2e_repository: E2ERepositoryDependency,
) -> EvaluationInsights:
    try:
        window = insights_window(query)
        repository = unit_repository if query.eval_type == "unit" else e2e_repository
        runs = await load_runs(repository.runs.collection, query.eval_type, window)
        selection = await run_in_threadpool(select_runs, runs, query)
        run_ids = [
            run["runId"] for run in selection["runs"] if run["target"] == selection["activeScope"]
        ]
        cases = await load_cases(repository.cases.collection, query.eval_type, run_ids)
        response = await run_in_threadpool(build_response, selection, cases)
        return EvaluationInsights.model_validate(
            {
                **response,
                "window": {
                    "startedFrom": window.started_from,
                    "startedBefore": window.started_before,
                },
            }
        )
    except InsightsCapacityError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
