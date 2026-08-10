from fastapi import APIRouter, HTTPException, Request

from app.models import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health/live", response_model=HealthResponse, include_in_schema=False)
async def liveness() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/health/ready", response_model=HealthResponse, include_in_schema=False)
async def readiness(request: Request) -> HealthResponse:
    try:
        await request.app.state.database.command("ping")
    except Exception as exc:
        raise HTTPException(status_code=503, detail="MongoDB is not ready") from exc
    return HealthResponse(status="ok")
