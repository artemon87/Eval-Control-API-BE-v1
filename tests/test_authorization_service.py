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
    AuthorizationScope,
    PrincipalRecord,
    ResourceContext,
    ScopeType,
)
from src.repositories.authorization import AccessRequestNotFoundError
from src.security.authorization_identity import AuthorizationIdentity
from src.security.permissions import (
    ROLE_PERMISSIONS,
    EvalHubRole,
    Permission,
    ResourceType,
)
from src.services.authorization import (
    AccessRequestAlreadySatisfiedError,
    AuthorizationDeniedError,
    AuthorizationService,
    PrincipalNotFoundError,
)


def identity(
    *,
    principal_id: str = "user-1",
    tenant_id: str = "tenant-1",
) -> AuthorizationIdentity:
    return AuthorizationIdentity(
        tenant_id=tenant_id,
        principal_id=principal_id,
        display_name=f"User {principal_id}",
        email=f"{principal_id}@example.com",
    )


def principal(
    principal_id: str,
    *,
    tenant_id: str = "tenant-1",
) -> PrincipalRecord:
    now = datetime.now(UTC)
    return PrincipalRecord(
        tenant_id=tenant_id,
        principal_id=principal_id,
        display_name=f"User {principal_id}",
        email=f"{principal_id}@example.com",
        first_login_at=now,
        last_login_at=now,
        updated_at=now,
    )


def assignment_document(
    *,
    principal_id: str = "user-1",
    role: str = "admin",
    scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "_id": "507f1f77bcf86cd799439011",
        "tenant_id": "tenant-1",
        "principal_id": principal_id,
        "local_role": role,
        "scope": scope
        or {"type": "global", "resource": None, "constraints": {}},
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
    principal_id: str = "user-1",
    status: str = "pending",
) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "_id": "507f1f77bcf86cd799439012",
        "tenant_id": "tenant-1",
        "principal_id": principal_id,
        "display_name": f"User {principal_id}",
        "email": f"{principal_id}@example.com",
        "requested_role": "editor",
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
        "local_role": "admin",
        "scope": {"type": "global", "resource": None, "constraints": {}},
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
        "get_access_request": AsyncMock(return_value=None),
        "cancel_access_request": AsyncMock(),
        "decide_access_request": AsyncMock(),
        "approve_access_request": AsyncMock(),
    }
    methods.update(overrides)
    return SimpleNamespace(**methods)


def authorized_service(repo: Any) -> AuthorizationService:
    service = AuthorizationService(repo)
    service.require_permission = AsyncMock()  # type: ignore[method-assign]
    return service


@pytest.mark.asyncio
async def test_resolve_uses_evalhub_assignments_and_ignores_invalid_roles() -> None:
    repo = repository(
        list_assignments=AsyncMock(
            return_value=(
                [
                    assignment_document(role="editor"),
                    assignment_document(role="not-a-role"),
                ],
                2,
            )
        )
    )

    context = await AuthorizationService(repo).resolve(identity())

    repo.upsert_principal.assert_awaited_once()
    assert context.roles == [EvalHubRole.EDITOR]
    assert set(context.permissions) == set(ROLE_PERMISSIONS[EvalHubRole.EDITOR])


@pytest.mark.asyncio
async def test_resolve_can_skip_login_and_normalizes_legacy_admin_role() -> None:
    repo = repository(
        list_assignments=AsyncMock(
            return_value=([assignment_document(role="platform_admin")], 1)
        )
    )

    context = await AuthorizationService(repo).resolve(
        identity(), record_login=False
    )

    repo.upsert_principal.assert_not_awaited()
    assert context.roles == [EvalHubRole.ADMIN]
    assert set(context.permissions) == set(ROLE_PERMISSIONS[EvalHubRole.ADMIN])


@pytest.mark.asyncio
async def test_require_global_permission_returns_context_or_denies() -> None:
    allowed_repo = repository(
        list_assignments=AsyncMock(
            return_value=([assignment_document(role="admin")], 1)
        )
    )
    context = await AuthorizationService(allowed_repo).require_permission(
        identity(), Permission.ACCESS_MANAGE
    )
    assert Permission.ACCESS_MANAGE in context.permissions

    scoped_editor = assignment_document(
        role="editor",
        scope={
            "type": "resource",
            "resource": "evaluation",
            "constraints": {"eval_type": ["e2e"]},
        },
    )
    denied_repo = repository(
        list_assignments=AsyncMock(return_value=([scoped_editor], 1))
    )
    with pytest.raises(AuthorizationDeniedError, match="missing global permission"):
        await AuthorizationService(denied_repo).require_permission(
            identity(), Permission.EVAL_EDIT
        )


@pytest.mark.asyncio
async def test_require_resource_permission_honors_scope_and_resource_type() -> None:
    scoped_editor = assignment_document(
        role="editor",
        scope={
            "type": "resource",
            "resource": "evaluation",
            "constraints": {"eval_type": ["e2e"]},
        },
    )
    service = AuthorizationService(
        repository(list_assignments=AsyncMock(return_value=([scoped_editor], 1)))
    )

    context = await service.require_resource_permission(
        identity(),
        Permission.EVAL_EDIT,
        ResourceContext(
            resource=ResourceType.EVALUATION,
            attributes={"eval_type": "e2e"},
        ),
    )
    assert EvalHubRole.EDITOR in context.roles

    with pytest.raises(AuthorizationDeniedError, match="missing permission"):
        await service.require_resource_permission(
            identity(),
            Permission.EVAL_EDIT,
            ResourceContext(
                resource=ResourceType.EVALUATION,
                attributes={"eval_type": "unit"},
            ),
        )

    with pytest.raises(ValueError, match="applies to evaluation"):
        await service.require_resource_permission(
            identity(),
            Permission.EVAL_EDIT,
            ResourceContext(resource=ResourceType.SUGGESTION),
        )


@pytest.mark.asyncio
async def test_list_users_combines_current_assignments_and_permissions() -> None:
    principals = [principal("target-admin"), principal("target-editor")]
    repo = repository(list_principals=AsyncMock(return_value=(principals, 2)))
    repo.list_assignments.side_effect = [
        ([assignment_document(principal_id="target-admin", role="admin")], 1),
        ([assignment_document(principal_id="target-editor", role="editor")], 1),
    ]
    service = authorized_service(repo)

    page = await service.list_users(
        identity(principal_id="admin-1"),
        search="target",
        limit=20,
        offset=5,
    )

    assert page.total == 2
    assert page.items[0].roles == [EvalHubRole.ADMIN]
    assert set(page.items[0].effective_permissions) == set(
        ROLE_PERMISSIONS[EvalHubRole.ADMIN]
    )
    assert page.items[1].roles == [EvalHubRole.EDITOR]


@pytest.mark.asyncio
async def test_list_assignments_filters_unknown_legacy_roles() -> None:
    repo = repository(
        list_assignments=AsyncMock(
            return_value=(
                [
                    assignment_document(role="admin"),
                    assignment_document(role="moderator"),
                ],
                2,
            )
        )
    )

    page = await authorized_service(repo).list_assignments(
        identity(principal_id="admin-1"),
        active_only=False,
        limit=10,
        offset=1,
    )

    assert len(page.items) == 1
    assert page.items[0].local_role is EvalHubRole.ADMIN
    assert page.total == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "target", "error", "message"),
    [
        (
            AssignmentCreate(
                tenant_id="other-tenant",
                principal_id="target-1",
                local_role=EvalHubRole.EDITOR,
                reason="Needed for evaluations",
            ),
            None,
            AuthorizationDeniedError,
            "cross-tenant",
        ),
        (
            AssignmentCreate(
                tenant_id="tenant-1",
                principal_id="admin-1",
                local_role=EvalHubRole.ADMIN,
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
                local_role=EvalHubRole.EDITOR,
                reason="Needed for evaluations",
            ),
            None,
            PrincipalNotFoundError,
            "sign in once",
        ),
    ],
)
async def test_create_assignment_rejects_invalid_targets(
    payload: AssignmentCreate,
    target: PrincipalRecord | None,
    error: type[RuntimeError],
    message: str,
) -> None:
    service = authorized_service(
        repository(get_principal=AsyncMock(return_value=target))
    )

    with pytest.raises(error, match=message):
        await service.create_assignment(
            identity(principal_id="admin-1"),
            payload,
            request_id="request-1",
        )


@pytest.mark.asyncio
async def test_create_assignment_builds_actor_target_and_scope() -> None:
    payload = AssignmentCreate(
        tenant_id="tenant-1",
        principal_id="target-1",
        local_role=EvalHubRole.EDITOR,
        scope=AuthorizationScope(
            type=ScopeType.RESOURCE,
            resource=ResourceType.EVALUATION,
            constraints={"eval_type": ["e2e"]},
        ),
        reason="Needed for E2E evaluations",
    )
    document = assignment_document(
        principal_id="target-1",
        role="editor",
        scope=payload.scope.model_dump(mode="python"),
    )
    repo = repository(
        get_principal=AsyncMock(return_value=principal("target-1")),
        create_assignment=AsyncMock(return_value=document),
    )
    service = authorized_service(repo)

    result = await service.create_assignment(
        identity(principal_id="admin-1"), payload, request_id="request-1"
    )

    assert result.local_role is EvalHubRole.EDITOR
    assert result.scope.constraints == {"eval_type": ["e2e"]}
    args = repo.create_assignment.await_args.args
    assert args[1].principal_id == "admin-1"
    assert args[2].principal_id == "target-1"


@pytest.mark.asyncio
async def test_revoke_assignment_handles_missing_and_self_revocation() -> None:
    actor = identity(principal_id="admin-1")
    service = authorized_service(repository())
    with pytest.raises(PrincipalNotFoundError, match="assignment not found"):
        await service.revoke_assignment(
            actor,
            "missing",
            AssignmentRevoke(reason="No longer required"),
            request_id=None,
        )

    repo = repository(
        get_assignment=AsyncMock(
            return_value=assignment_document(principal_id="admin-1")
        )
    )
    with pytest.raises(AuthorizationDeniedError, match="another admin"):
        await authorized_service(repo).revoke_assignment(
            actor,
            "507f1f77bcf86cd799439011",
            AssignmentRevoke(reason="No longer required"),
            request_id=None,
        )


@pytest.mark.asyncio
async def test_revoke_assignment_builds_target_reference() -> None:
    document = assignment_document(principal_id="target-1", role="editor")
    revoked_document = {
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
    repo = repository(
        get_assignment=AsyncMock(return_value=document),
        get_principal=AsyncMock(return_value=principal("target-1")),
        revoke_assignment=AsyncMock(return_value=revoked_document),
    )

    result = await authorized_service(repo).revoke_assignment(
        identity(principal_id="admin-1"),
        document["_id"],
        AssignmentRevoke(reason="No longer required"),
        request_id="request-1",
    )

    assert result.status.value == "revoked"
    target = repo.revoke_assignment.await_args.args[3]
    assert target.principal_id == "target-1"
    assert target.display_name == "User target-1"


@pytest.mark.asyncio
async def test_list_audit_events_returns_validated_page() -> None:
    repo = repository(
        list_audit_events=AsyncMock(return_value=([audit_document()], 1))
    )
    page = await authorized_service(repo).list_audit_events(
        identity(principal_id="admin-1"), limit=25, offset=2
    )
    assert page.total == 1
    assert page.items[0].local_role is EvalHubRole.ADMIN


@pytest.mark.asyncio
async def test_create_access_request_rejects_roles_already_held_in_evalhub() -> None:
    payload = AccessRequestCreate(
        requested_role=EvalHubRole.EDITOR,
        business_reason="Needed for evaluation work",
    )
    editor_repo = repository(
        list_assignments=AsyncMock(
            return_value=([assignment_document(role="editor")], 1)
        )
    )
    with pytest.raises(AccessRequestAlreadySatisfiedError, match="already have"):
        await AuthorizationService(editor_repo).create_access_request(
            identity(), payload, request_id=None
        )

    admin_repo = repository(
        list_assignments=AsyncMock(
            return_value=([assignment_document(role="admin")], 1)
        )
    )
    with pytest.raises(AccessRequestAlreadySatisfiedError, match="includes Editor"):
        await AuthorizationService(admin_repo).create_access_request(
            identity(), payload, request_id=None
        )


@pytest.mark.asyncio
async def test_create_access_request_upserts_and_returns_record() -> None:
    repo = repository(
        create_access_request=AsyncMock(return_value=request_document())
    )
    result = await AuthorizationService(repo).create_access_request(
        identity(),
        AccessRequestCreate(
            requested_role=EvalHubRole.EDITOR,
            business_reason="Needed for evaluation work",
        ),
        request_id="request-1",
    )

    repo.upsert_principal.assert_awaited_once()
    assert result.status is AccessRequestStatus.PENDING


@pytest.mark.asyncio
async def test_access_request_listing_and_cancellation() -> None:
    pending = request_document()
    repo = repository(
        list_access_requests=AsyncMock(return_value=([pending], 1)),
        cancel_access_request=AsyncMock(
            return_value={**pending, "status": "cancelled"}
        ),
    )
    service = AuthorizationService(repo)

    page = await service.list_my_access_requests(identity(), limit=10, offset=1)
    assert page.total == 1
    assert page.offset == 1

    cancelled = await service.cancel_access_request(
        identity(), pending["_id"], request_id="request-1"
    )
    assert cancelled.status is AccessRequestStatus.CANCELLED


@pytest.mark.asyncio
async def test_decide_access_request_approves_with_global_assignment() -> None:
    pending = request_document(principal_id="target-1")
    approved = {**pending, "status": "approved"}
    repo = repository(
        get_access_request=AsyncMock(return_value=pending),
        get_principal=AsyncMock(return_value=principal("target-1")),
        approve_access_request=AsyncMock(return_value=approved),
    )
    service = authorized_service(repo)

    result = await service.decide_access_request(
        identity(principal_id="admin-1"),
        pending["_id"],
        AccessRequestDecision(
            action="approve", note="Approved for project work"
        ),
        request_id="request-2",
    )

    assert result.status is AccessRequestStatus.APPROVED
    assignment = repo.approve_access_request.await_args.args[4]
    assert assignment.local_role is EvalHubRole.EDITOR
    assert assignment.scope.type is ScopeType.GLOBAL


@pytest.mark.asyncio
async def test_decide_access_request_rejects_and_guards_invalid_requests() -> None:
    pending = request_document(principal_id="target-1")
    rejected = {**pending, "status": "rejected"}
    repo = repository(
        get_access_request=AsyncMock(return_value=pending),
        decide_access_request=AsyncMock(return_value=rejected),
    )
    service = authorized_service(repo)

    result = await service.decide_access_request(
        identity(principal_id="admin-1"),
        pending["_id"],
        AccessRequestDecision(
            action="reject", note="Rejected for project reasons"
        ),
        request_id="request-2",
    )
    assert result.status is AccessRequestStatus.REJECTED

    with pytest.raises(AccessRequestNotFoundError, match="not found"):
        await authorized_service(repository()).decide_access_request(
            identity(principal_id="admin-1"),
            pending["_id"],
            AccessRequestDecision(
                action="reject", note="Rejected for project reasons"
            ),
            request_id=None,
        )

    self_repo = repository(
        get_access_request=AsyncMock(
            return_value=request_document(principal_id="admin-1")
        )
    )
    with pytest.raises(AuthorizationDeniedError, match="self-approval"):
        await authorized_service(self_repo).decide_access_request(
            identity(principal_id="admin-1"),
            pending["_id"],
            AccessRequestDecision(
                action="reject", note="Rejected for project reasons"
            ),
            request_id=None,
        )
