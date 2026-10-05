from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from src.api.dependencies import AuthorizationServiceDependency, CurrentUserDependency

from src.models.authorization import (
    AccessRequestCreate,
    AccessRequestRecord,
    PaginatedAccessRequests,
)
from src.repositories.authorization import (
    AccessRequestConflictError,
    AccessRequestNotFoundError,
)
from src.security.authorization_identity import identity_from_authenticated_user
from src.services.authorization import AccessRequestAlreadySatisfiedError

router = APIRouter(prefix="/access-requests", tags=["access-requests"])
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0, le=100_000)]


def _request_id(request: Request) -> str | None:
    value = request.headers.get("X-Request-ID")
    return value[:128] if value else None


@router.post("", response_model=AccessRequestRecord, status_code=status.HTTP_201_CREATED)
async def create_access_request(
    payload: AccessRequestCreate,
    request: Request,
    current_user: CurrentUserDependency,
    service: AuthorizationServiceDependency,
) -> AccessRequestRecord:
    try:
        return await service.create_access_request(
            identity_from_authenticated_user(current_user),
            payload,
            request_id=_request_id(request),
        )
    except (AccessRequestConflictError, AccessRequestAlreadySatisfiedError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/me", response_model=PaginatedAccessRequests)
async def list_my_access_requests(
    current_user: CurrentUserDependency,
    service: AuthorizationServiceDependency,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
) -> PaginatedAccessRequests:
    return await service.list_my_access_requests(
        identity_from_authenticated_user(current_user),
        limit=limit,
        offset=offset,
    )


@router.post("/{access_request_id}/cancel", response_model=AccessRequestRecord)
async def cancel_access_request(
    access_request_id: str,
    request: Request,
    current_user: CurrentUserDependency,
    service: AuthorizationServiceDependency,
) -> AccessRequestRecord:
    try:
        return await service.cancel_access_request(
            identity_from_authenticated_user(current_user),
            access_request_id,
            request_id=_request_id(request),
        )
    except AccessRequestNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
