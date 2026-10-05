from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.models.authorization import (
    AccessRequestCreate,
    AccessRequestDecision,
    AccessRequestStatus,
    AssignmentCreate,
    AssignmentRevoke,
    PrincipalRecord,
)
from src.security.authorization_identity import AuthorizationIdentity
from src.security.permissions import Permission, PlatformRole
from src.services.authorization import (
    AccessRequestAlreadySatisfiedError,
    AuthorizationDeniedError,
    AuthorizationService,
    InvalidAssignmentError,
    PrincipalNotFoundError,
)


def identity(
    *roles: str,
    principal_id: str = "user-1",
    tenant_id: str = "tenant-1",
) -> AuthorizationIdentity:
    return AuthorizationIdentity(
        tenant_id=tenant_id,
        principal_id=principal_id,
        display_name=f"User {principal_id}",
        email=f"{principal_id}@example.com",
        entra_roles=frozenset(roles),
    )


def principal(
    principal_id: str,
    *roles: str,
    tenant_id: str = "tenant-1",
) -> PrincipalRecord:
    now = datetime.now(UTC)
    return PrincipalRecord(
        tenant_id=tenant_id,
        principal_id=principal_id,
        display_name=f"User {principal_id}",
        email=f"{principal_id}@example.com",
        entra_roles_last_seen=list(roles),
        first_login_at=now,
        last_login_at=now,
        updated_at=now,
    )


def assignment_document(
    *,
    principal_id: str = "user-1",
    role: str = "platform_admin",
) -> dict[str, Any]:
    return {
        "_id": "507f1f77bcf86cd799439011",
        "tenant_id": "tenant-1",
        "principal_id": principal_id,
        "local_role": role,
        "scope": {"type": "global", "id": "*"},
        "status": "active",
        "reason": "Test assignment",
        "granted_by": {
            "tenant_id": "tenant-1",
            "principal_id": "admin-1",
            "display_name": "Admin",
        },
        "created_at": datetime.now(UTC),
    }


def request_document(
    *,
    request_id: str = "507f1f77bcf86cd799439012",
    status: str = "pending",
) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "_id": request_id,
        "tenant_id": "tenant-1",
        "principal_id": "user-1",
        "display_name": "User user-1",
        "email": "user-1@example.com",
        "requested_role": "EvalHub.Editor",
        "business_reason": "Needed for evaluation work",
        "status": status,
        "created_at": now,
        "updated_at": now,
    }


def audit_document() -> dict[str, Any]:
    return {
        "_id": "507f1f77bcf86cd799439013",
        "event_type": "authorization.assignment.granted",
        "actor": {
            "tenant_id": "tenant-1",
            "principal_id": "admin-1",
            "display_name": "Admin",
        },
        "target": {
            "tenant_id": "tenant-1",
            "principal_id": "user-1",
            "display_name": "User",
        },
        "assignment_id": "507f1f77bcf86cd799439011",
        "local_role": "platform_admin",
        "scope": {"type": "global", "id": "*"},
        "reason": "Needed for administration",
        "occurred_at": datetime.now(UTC),
    }


def repository(**overrides: Any) -> Any:
    methods = {
        "upsert_principal": AsyncMock(),
        "list_assignments": AsyncMock(return_value=([], 0)),
        "list_principals": AsyncMock(return_value=([], 0)),
        "get_principal": AsyncMock(return_value=None),
        "create_assignment": AsyncMock(),
        "get_assignment": AsyncMock(return_value=None),
        "revoke_assignment": AsyncMock(),
        "list_audit_events": AsyncMock(return_value=([], 0)),
        "create_access_request": AsyncMock(),
        "list_access_requests": AsyncMock(return_value=([], 0)),
        "cancel_access_request": AsyncMock(),
        "decide_access_request": AsyncMock(),
    }
    methods.update(overrides)
    return SimpleNamespace(**methods)


def authorized_service(repo: Any) -> AuthorizationService:
    service = AuthorizationService(repo)
    service.require_permission = AsyncMock()  # type: ignore[method-assign]
    return service


@pytest.mark.asyncio
async def test_resolve_records_login_and_ignores_legacy_or_disallowed_roles() -> None:
    repo = repository(
        list_assignments=AsyncMock(
            return_value=(
                [assignment_document(), assignment_document(role="moderator")],
                2,
            )
        )
    )
    service = AuthorizationService(repo)

    context = await service.resolve(identity("EvalHub.Viewer"))

    repo.upsert_principal.assert_awaited_once()
    assert context.local_roles == []
    assert context.permissions == []
    assert context.entra_roles == ["EvalHub.Viewer"]


@pytest.mark.asyncio
async def test_resolve_can_skip_login_and_grants_platform_admin_permissions() -> None:
    repo = repository(
        list_assignments=AsyncMock(return_value=([assignment_document()], 1))
    )
    service = AuthorizationService(repo)

    context = await service.resolve(identity("EvalHub.Admin"), record_login=False)

    repo.upsert_principal.assert_not_awaited()
    assert context.local_roles == [PlatformRole.PLATFORM_ADMIN]
    assert context.permissions == [Permission.ACCESS_MANAGE, Permission.AUDIT_READ]


@pytest.mark.asyncio
async def test_require_permission_returns_context_or_denies() -> None:
    allowed_repo = repository(
        list_assignments=AsyncMock(return_value=([assignment_document()], 1))
    )
    context = await AuthorizationService(allowed_repo).require_permission(
        identity("EvalHub.Admin"), Permission.ACCESS_MANAGE
    )
    assert Permission.ACCESS_MANAGE in context.permissions

    with pytest.raises(AuthorizationDeniedError, match="missing permission"):
        await AuthorizationService(repository()).require_permission(
            identity("EvalHub.Admin"), Permission.ACCESS_MANAGE
        )


@pytest.mark.asyncio
async def test_list_users_combines_principals_assignments_and_effective_permissions() -> None:
    principals = [
        principal("target-admin", "EvalHub.Admin"),
        principal("target-editor", "EvalHub.Editor"),
    ]
    repo = repository(list_principals=AsyncMock(return_value=(principals, 2)))
    repo.list_assignments.side_effect = [
        ([assignment_document(principal_id="target-admin"), assignment_document(role="moderator")], 2),
        ([assignment_document(principal_id="target-editor")], 1),
    ]
    service = authorized_service(repo)

    page = await service.list_users(
        identity("EvalHub.Admin", principal_id="admin-1"),
        search="target",
        limit=20,
        offset=5,
    )

    assert page.total == 2
    assert page.limit == 20
    assert page.offset == 5
    assert page.items[0].assignments[0].local_role is PlatformRole.PLATFORM_ADMIN
    assert page.items[0].effective_permissions == [
        Permission.ACCESS_MANAGE,
        Permission.AUDIT_READ,
    ]
    assert page.items[1].effective_permissions == []


@pytest.mark.asyncio
async def test_list_assignments_filters_legacy_records() -> None:
    repo = repository(
        list_assignments=AsyncMock(
            return_value=([assignment_document(), assignment_document(role="moderator")], 2)
        )
    )
    service = authorized_service(repo)

    page = await service.list_assignments(
        identity("EvalHub.Admin"), active_only=False, limit=10, offset=1
    )

    assert len(page.items) == 1
    assert page.total == 2
    assert page.offset == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "target", "error", "message"),
    [
        (
            AssignmentCreate(
                tenant_id="other-tenant",
                principal_id="target-1",
                reason="Needed for administration",
            ),
            None,
            AuthorizationDeniedError,
            "cross-tenant",
        ),
        (
            AssignmentCreate(
                tenant_id="tenant-1",
                principal_id="admin-1",
                reason="Needed for administration",
            ),
            None,
            AuthorizationDeniedError,
            "self-assignment",
        ),
        (
            AssignmentCreate(
                tenant_id="tenant-1",
                principal_id="missing",
                reason="Needed for administration",
            ),
            None,
            PrincipalNotFoundError,
            "sign in once",
        ),
        (
            AssignmentCreate(
                tenant_id="tenant-1",
                principal_id="editor-1",
                reason="Needed for administration",
            ),
            principal("editor-1", "EvalHub.Editor"),
            InvalidAssignmentError,
            "does not satisfy",
        ),
    ],
)
async def test_create_assignment_rejects_invalid_grants(
    payload: AssignmentCreate,
    target: PrincipalRecord | None,
    error: type[RuntimeError],
    message: str,
) -> None:
    repo = repository(get_principal=AsyncMock(return_value=target))
    service = authorized_service(repo)

    with pytest.raises(error, match=message):
        await service.create_assignment(
            identity("EvalHub.Admin", principal_id="admin-1"),
            payload,
            request_id="request-1",
        )


@pytest.mark.asyncio
async def test_create_assignment_builds_actor_and_target_references() -> None:
    payload = AssignmentCreate(
        tenant_id="tenant-1",
        principal_id="target-1",
        reason="Needed for administration",
    )
    document = assignment_document(principal_id="target-1")
    repo = repository(
        get_principal=AsyncMock(return_value=principal("target-1", "EvalHub.Admin")),
        create_assignment=AsyncMock(return_value=document),
    )
    service = authorized_service(repo)

    result = await service.create_assignment(
        identity("EvalHub.Admin", principal_id="admin-1"),
        payload,
        request_id="request-1",
    )

    assert result.principal_id == "target-1"
    args = repo.create_assignment.await_args.args
    assert args[1].principal_id == "admin-1"
    assert args[2].principal_id == "target-1"
    assert repo.create_assignment.await_args.kwargs == {"request_id": "request-1"}


@pytest.mark.asyncio
async def test_revoke_assignment_handles_missing_and_self_revocation() -> None:
    actor = identity("EvalHub.Admin", principal_id="admin-1")
    service = authorized_service(repository())
    with pytest.raises(PrincipalNotFoundError, match="assignment not found"):
        await service.revoke_assignment(
            actor,
            "missing",
            AssignmentRevoke(reason="No longer required"),
            request_id=None,
        )

    repo = repository(
        get_assignment=AsyncMock(return_value=assignment_document(principal_id="admin-1"))
    )
    service = authorized_service(repo)
    with pytest.raises(AuthorizationDeniedError, match="another admin"):
        await service.revoke_assignment(
            actor,
            "507f1f77bcf86cd799439011",
            AssignmentRevoke(reason="No longer required"),
            request_id=None,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("target_exists", [True, False])
async def test_revoke_assignment_supports_known_or_missing_target(
    target_exists: bool,
) -> None:
    document = assignment_document(principal_id="target-1")
    repo = repository(
        get_assignment=AsyncMock(return_value=document),
        get_principal=AsyncMock(
            return_value=principal("target-1", "EvalHub.Admin") if target_exists else None
        ),
        revoke_assignment=AsyncMock(
            return_value={
                **document,
                "status": "revoked",
                "revoked_at": datetime.now(UTC),
                "revoked_by": {
                    "tenant_id": "tenant-1",
                    "principal_id": "admin-1",
                    "display_name": "User admin-1",
                },
                "revocation_reason": "No longer required",
            }
        ),
    )
    service = authorized_service(repo)

    result = await service.revoke_assignment(
        identity("EvalHub.Admin", principal_id="admin-1"),
        "507f1f77bcf86cd799439011",
        AssignmentRevoke(reason="No longer required"),
        request_id="request-1",
    )

    assert result.status.value == "revoked"
    target_ref = repo.revoke_assignment.await_args.args[3]
    assert target_ref.display_name == ("User target-1" if target_exists else None)


@pytest.mark.asyncio
async def test_list_audit_events_returns_validated_page() -> None:
    repo = repository(list_audit_events=AsyncMock(return_value=([audit_document()], 1)))
    service = authorized_service(repo)

    page = await service.list_audit_events(
        identity("EvalHub.Admin"), limit=25, offset=2
    )

    assert page.total == 1
    assert page.items[0].event_type == "authorization.assignment.granted"
    assert page.limit == 25


@pytest.mark.asyncio
async def test_create_access_request_rejects_satisfied_roles() -> None:
    service = AuthorizationService(repository())
    payload = AccessRequestCreate(
        requested_role="EvalHub.Editor",
        business_reason="Needed for evaluation work",
    )

    with pytest.raises(AccessRequestAlreadySatisfiedError, match="already have"):
        await service.create_access_request(
            identity("EvalHub.Editor"), payload, request_id=None
        )
    with pytest.raises(AccessRequestAlreadySatisfiedError, match="includes Editor"):
        await service.create_access_request(
            identity("EvalHub.Admin"), payload, request_id=None
        )


@pytest.mark.asyncio
async def test_create_access_request_upserts_and_returns_record() -> None:
    document = request_document()
    repo = repository(create_access_request=AsyncMock(return_value=document))
    service = AuthorizationService(repo)
    payload = AccessRequestCreate(
        requested_role="EvalHub.Editor",
        business_reason="Needed for evaluation work",
    )

    result = await service.create_access_request(
        identity("EvalHub.Viewer"), payload, request_id="request-1"
    )

    repo.upsert_principal.assert_awaited_once()
    assert result.status is AccessRequestStatus.PENDING


@pytest.mark.asyncio
async def test_access_request_list_cancel_admin_list_and_decision() -> None:
    pending = request_document()
    approved = request_document(status="approved")
    repo = repository(
        list_access_requests=AsyncMock(return_value=([pending], 1)),
        cancel_access_request=AsyncMock(return_value={**pending, "status": "cancelled"}),
        decide_access_request=AsyncMock(return_value=approved),
    )
    user = identity("EvalHub.Viewer")
    actor = identity("EvalHub.Admin", principal_id="admin-1")
    service = AuthorizationService(repo)

    own_page = await service.list_my_access_requests(user, limit=10, offset=1)
    assert own_page.total == 1
    assert own_page.offset == 1
    cancelled = await service.cancel_access_request(
        user, pending["_id"], request_id="request-1"
    )
    assert cancelled.status.value == "cancelled"

    service.require_permission = AsyncMock()  # type: ignore[method-assign]
    admin_page = await service.list_access_requests(
        actor, status=AccessRequestStatus.PENDING, limit=50, offset=0
    )
    assert admin_page.items[0].status is AccessRequestStatus.PENDING
    decided = await service.decide_access_request(
        actor,
        pending["_id"],
        AccessRequestDecision(action="approve", note="Approved for project work"),
        request_id="request-2",
    )
    assert decided.status is AccessRequestStatus.APPROVED
    actor_ref = repo.decide_access_request.await_args.args[2]
    assert actor_ref.principal_id == "admin-1"
