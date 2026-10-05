from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from src.api.dependencies import AuthorizationServiceDependency, CurrentUserDependency

from src.models.authorization import (
    AccessRequestDecision,
    AccessRequestRecord,
    AccessRequestStatus,
    AssignmentCreate,
    AssignmentRecord,
    AssignmentRevoke,
    PaginatedAccessRequests,
    PaginatedAccessUsers,
    PaginatedAssignments,
    PaginatedAuditEvents,
)
from src.repositories.authorization import (
    AccessRequestConflictError,
    AccessRequestNotFoundError,
    AssignmentAlreadyExistsError,
    AssignmentNotFoundError,
)
from src.security.authorization_identity import identity_from_authenticated_user
from src.services.authorization import (
    AccessRequestAlreadySatisfiedError,
    AuthorizationDeniedError,
    InvalidAssignmentError,
    PrincipalNotFoundError,
)

router = APIRouter(prefix="/admin/access", tags=["access-administration"])

PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0, le=100_000)]
HTTP_422_UNPROCESSABLE_CONTENT = getattr(
    status,
    "HTTP_422_UNPROCESSABLE_CONTENT",
    422,
)


def _request_id(request: Request) -> str | None:
    value = request.headers.get("X-Request-ID")
    return value[:128] if value else None


def _translate_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AuthorizationDeniedError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, (PrincipalNotFoundError, AssignmentNotFoundError)):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, AssignmentAlreadyExistsError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, (AccessRequestConflictError, AccessRequestAlreadySatisfiedError)):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, AccessRequestNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, InvalidAssignmentError):
        return HTTPException(status_code=HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    raise exc


@router.get("/users", response_model=PaginatedAccessUsers)
async def list_access_users(
    current_user: CurrentUserDependency,
    service: AuthorizationServiceDependency,
    search: Annotated[str | None, Query(max_length=100)] = None,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
) -> PaginatedAccessUsers:
    try:
        return await service.list_users(
            identity_from_authenticated_user(current_user),
            search=search,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.get("/assignments", response_model=PaginatedAssignments)
async def list_assignments(
    current_user: CurrentUserDependency,
    service: AuthorizationServiceDependency,
    active_only: bool = True,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
) -> PaginatedAssignments:
    try:
        return await service.list_assignments(
            identity_from_authenticated_user(current_user),
            active_only=active_only,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/assignments",
    response_model=AssignmentRecord,
    status_code=status.HTTP_201_CREATED,
)
async def create_assignment(
    payload: AssignmentCreate,
    request: Request,
    current_user: CurrentUserDependency,
    service: AuthorizationServiceDependency,
) -> AssignmentRecord:
    try:
        return await service.create_assignment(
            identity_from_authenticated_user(current_user),
            payload,
            request_id=_request_id(request),
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/assignments/{assignment_id}/revoke",
    response_model=AssignmentRecord,
)
async def revoke_assignment(
    assignment_id: str,
    payload: AssignmentRevoke,
    request: Request,
    current_user: CurrentUserDependency,
    service: AuthorizationServiceDependency,
) -> AssignmentRecord:
    try:
        return await service.revoke_assignment(
            identity_from_authenticated_user(current_user),
            assignment_id,
            payload,
            request_id=_request_id(request),
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.get("/audit", response_model=PaginatedAuditEvents)
async def list_audit_events(
    current_user: CurrentUserDependency,
    service: AuthorizationServiceDependency,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
) -> PaginatedAuditEvents:
    try:
        return await service.list_audit_events(
            identity_from_authenticated_user(current_user),
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.get("/requests", response_model=PaginatedAccessRequests)
async def list_access_requests(
    current_user: CurrentUserDependency,
    service: AuthorizationServiceDependency,
    request_status: AccessRequestStatus | None = None,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
) -> PaginatedAccessRequests:
    try:
        return await service.list_access_requests(
            identity_from_authenticated_user(current_user),
            status=request_status,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


@router.post(
    "/requests/{access_request_id}/decision",
    response_model=AccessRequestRecord,
)
async def decide_access_request(
    access_request_id: str,
    payload: AccessRequestDecision,
    request: Request,
    current_user: CurrentUserDependency,
    service: AuthorizationServiceDependency,
) -> AccessRequestRecord:
    try:
        return await service.decide_access_request(
            identity_from_authenticated_user(current_user),
            access_request_id,
            payload,
            request_id=_request_id(request),
        )
    except Exception as exc:
        raise _translate_error(exc) from exc
