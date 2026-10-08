from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.api.api_v1.routes import access_admin, access_requests, auth_me
from src.models.authorization import (
    AccessRequestCreate,
    AccessRequestDecision,
    AssignmentCreate,
    AssignmentRevoke,
)
from src.repositories.authorization import (
    AccessRequestConflictError,
    AccessRequestNotFoundError,
    AssignmentAlreadyExistsError,
    AssignmentNotFoundError,
)
from src.security.permissions import EvalHubRole, Permission, ResourceType
from src.security.require_permission import (
    require_permission,
    require_resource_permission,
)
from src.services.authorization import (
    AccessRequestAlreadySatisfiedError,
    AuthorizationDeniedError,
    PrincipalNotFoundError,
)


def current_user() -> Any:
    return SimpleNamespace(
        authenticated=True,
        tenant_id="tenant-1",
        object_id="admin-1",
        display_name="Admin",
        email="admin@example.com",
        role=SimpleNamespace(value="admin"),
    )


def request(request_id: str | None = "request-1") -> Request:
    headers = [] if request_id is None else [(b"x-request-id", request_id.encode())]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


def service(**methods: Any) -> Any:
    return SimpleNamespace(**methods)


def test_request_id_is_optional_and_truncated() -> None:
    assert access_admin._request_id(request(None)) is None
    assert access_requests._request_id(request(None)) is None
    long_value = "x" * 200
    assert access_admin._request_id(request(long_value)) == "x" * 128
    assert access_requests._request_id(request(long_value)) == "x" * 128


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (AuthorizationDeniedError("denied"), 403),
        (PrincipalNotFoundError("missing"), 404),
        (AssignmentNotFoundError("missing"), 404),
        (AssignmentAlreadyExistsError("duplicate"), 409),
        (AccessRequestConflictError("duplicate"), 409),
        (AccessRequestAlreadySatisfiedError("satisfied"), 409),
        (AccessRequestNotFoundError("missing"), 404),
    ],
)
def test_admin_error_translation(error: Exception, status_code: int) -> None:
    translated = access_admin._translate_error(error)
    assert translated.status_code == status_code
    assert translated.detail == str(error)


def test_admin_error_translation_reraises_unknown_exception() -> None:
    error = RuntimeError("unexpected")
    with pytest.raises(RuntimeError, match="unexpected"):
        access_admin._translate_error(error)


@pytest.mark.asyncio
async def test_admin_routes_forward_identity_pagination_and_request_id() -> None:
    marker = object()
    fake = service(
        list_users=AsyncMock(return_value=marker),
        list_assignments=AsyncMock(return_value=marker),
        create_assignment=AsyncMock(return_value=marker),
        revoke_assignment=AsyncMock(return_value=marker),
        list_audit_events=AsyncMock(return_value=marker),
        list_access_requests=AsyncMock(return_value=marker),
        decide_access_request=AsyncMock(return_value=marker),
    )
    user = current_user()
    assignment = AssignmentCreate(
        tenant_id="tenant-1",
        principal_id="target-1",
        local_role=EvalHubRole.ADMIN,
        reason="Needed for administration",
    )
    revoke = AssignmentRevoke(reason="No longer required")
    decision = AccessRequestDecision(
        action="approve", note="Approved for project work"
    )

    assert await access_admin.list_access_users(user, fake, "target", 10, 2) is marker
    assert await access_admin.list_assignments(user, fake, False, 20, 3) is marker
    assert (
        await access_admin.create_assignment(assignment, request(), user, fake)
        is marker
    )
    assert (
        await access_admin.revoke_assignment(
            "assignment-1", revoke, request(), user, fake
        )
        is marker
    )
    assert await access_admin.list_audit_events(user, fake, 30, 4) is marker
    assert (
        await access_admin.list_access_requests(user, fake, None, 40, 5)
        is marker
    )
    assert (
        await access_admin.decide_access_request(
            "access-request-1", decision, request(), user, fake
        )
        is marker
    )

    assert fake.list_users.await_args.kwargs == {
        "search": "target",
        "limit": 10,
        "offset": 2,
    }
    assert fake.create_assignment.await_args.kwargs["request_id"] == "request-1"
    assert fake.decide_access_request.await_args.kwargs["request_id"] == "request-1"


AdminRouteCall = Callable[[Any], Awaitable[Any]]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "route_call",
    [
        lambda fake: access_admin.list_access_users(
            current_user(), fake, None, 50, 0
        ),
        lambda fake: access_admin.list_assignments(
            current_user(), fake, True, 50, 0
        ),
        lambda fake: access_admin.create_assignment(
            AssignmentCreate(
                tenant_id="tenant-1",
                principal_id="target-1",
                local_role=EvalHubRole.ADMIN,
                reason="Needed for administration",
            ),
            request(),
            current_user(),
            fake,
        ),
        lambda fake: access_admin.revoke_assignment(
            "assignment-1",
            AssignmentRevoke(reason="No longer required"),
            request(),
            current_user(),
            fake,
        ),
        lambda fake: access_admin.list_audit_events(
            current_user(), fake, 50, 0
        ),
        lambda fake: access_admin.list_access_requests(
            current_user(), fake, None, 50, 0
        ),
        lambda fake: access_admin.decide_access_request(
            "access-request-1",
            AccessRequestDecision(
                action="reject", note="Rejected for project reasons"
            ),
            request(),
            current_user(),
            fake,
        ),
    ],
)
async def test_each_admin_route_translates_service_errors(
    route_call: AdminRouteCall,
) -> None:
    denied = AsyncMock(side_effect=AuthorizationDeniedError("denied"))
    fake = service(
        list_users=denied,
        list_assignments=denied,
        create_assignment=denied,
        revoke_assignment=denied,
        list_audit_events=denied,
        list_access_requests=denied,
        decide_access_request=denied,
    )
    with pytest.raises(HTTPException) as error:
        await route_call(fake)
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_user_access_request_routes_success() -> None:
    marker = object()
    fake = service(
        create_access_request=AsyncMock(return_value=marker),
        list_my_access_requests=AsyncMock(return_value=marker),
        cancel_access_request=AsyncMock(return_value=marker),
    )
    payload = AccessRequestCreate(
        requested_role="EvalHub.Editor",
        business_reason="Needed for evaluation work",
    )
    user = current_user()

    assert (
        await access_requests.create_access_request(payload, request(), user, fake)
        is marker
    )
    assert await access_requests.list_my_access_requests(user, fake, 25, 2) is marker
    assert (
        await access_requests.cancel_access_request(
            "access-request-1", request(), user, fake
        )
        is marker
    )
    assert fake.create_access_request.await_args.kwargs["request_id"] == "request-1"
    assert fake.list_my_access_requests.await_args.kwargs == {"limit": 25, "offset": 2}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        AccessRequestConflictError("duplicate"),
        AccessRequestAlreadySatisfiedError("satisfied"),
    ],
)
async def test_create_access_request_translates_conflicts(error: Exception) -> None:
    fake = service(create_access_request=AsyncMock(side_effect=error))
    with pytest.raises(HTTPException) as translated:
        await access_requests.create_access_request(
            AccessRequestCreate(
                requested_role="EvalHub.Editor",
                business_reason="Needed for evaluation work",
            ),
            request(),
            current_user(),
            fake,
        )
    assert translated.value.status_code == 409


@pytest.mark.asyncio
async def test_cancel_access_request_translates_not_found() -> None:
    fake = service(
        cancel_access_request=AsyncMock(
            side_effect=AccessRequestNotFoundError("missing")
        )
    )
    with pytest.raises(HTTPException) as translated:
        await access_requests.cancel_access_request(
            "missing", request(), current_user(), fake
        )
    assert translated.value.status_code == 404


@pytest.mark.asyncio
async def test_auth_me_resolves_current_identity() -> None:
    marker = object()
    fake = service(resolve=AsyncMock(return_value=marker))
    assert await auth_me.get_my_authorization(current_user(), fake) is marker
    assert fake.resolve.await_args.args[0].principal_id == "admin-1"


@pytest.mark.asyncio
async def test_require_permission_dependency_returns_user_or_raises_403() -> None:
    dependency = require_permission(Permission.ACCESS_MANAGE)
    user = current_user()
    fake = service(require_permission=AsyncMock())
    assert await dependency(user, fake) is user
    fake.require_permission.side_effect = AuthorizationDeniedError("missing permission")
    with pytest.raises(HTTPException) as translated:
        await dependency(user, fake)
    assert translated.value.status_code == 403
    assert translated.value.detail == "missing permission"


@pytest.mark.asyncio
async def test_resource_permission_dependency_forwards_resource_context() -> None:
    dependency = require_resource_permission(
        Permission.EVAL_EDIT,
        ResourceType.EVALUATION,
        attributes={"eval_type": "e2e"},
    )
    user = current_user()
    fake = service(require_resource_permission=AsyncMock())

    assert await dependency(user, fake) is user
    resource = fake.require_resource_permission.await_args.args[2]
    assert resource.resource is ResourceType.EVALUATION
    assert resource.attributes == {"eval_type": "e2e"}

    fake.require_resource_permission.side_effect = AuthorizationDeniedError(
        "missing scoped permission"
    )
    with pytest.raises(HTTPException) as translated:
        await dependency(user, fake)
    assert translated.value.status_code == 403
    assert translated.value.detail == "missing scoped permission"
