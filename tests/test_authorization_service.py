from datetime import UTC, datetime
from typing import Any

import pytest

from src.models.authorization import PrincipalRecord
from src.security.authorization_identity import AuthorizationIdentity
from src.security.permissions import Permission
from src.services.authorization import AuthorizationDeniedError, AuthorizationService


class FakeAuthorizationRepository:
    def __init__(self, assignments: list[dict[str, Any]]) -> None:
        self.assignments = assignments

    async def upsert_principal(
        self,
        identity: AuthorizationIdentity,
    ) -> PrincipalRecord:
        now = datetime.now(UTC)
        return PrincipalRecord(
            tenant_id=identity.tenant_id,
            principal_id=identity.principal_id,
            display_name=identity.display_name,
            email=identity.email,
            entra_roles_last_seen=sorted(identity.entra_roles),
            first_login_at=now,
            last_login_at=now,
            updated_at=now,
        )

    async def list_assignments(self, **_: Any) -> tuple[list[dict[str, Any]], int]:
        return self.assignments, len(self.assignments)


def identity(*roles: str) -> AuthorizationIdentity:
    return AuthorizationIdentity(
        tenant_id="tenant-1",
        principal_id="user-1",
        display_name="Test User",
        email="test@example.com",
        entra_roles=frozenset(roles),
    )


def active_assignment(role: str) -> dict[str, Any]:
    return {
        "_id": "507f1f77bcf86cd799439011",
        "tenant_id": "tenant-1",
        "principal_id": "user-1",
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


@pytest.mark.asyncio
async def test_local_platform_admin_cannot_elevate_viewer() -> None:
    repository = FakeAuthorizationRepository([active_assignment("platform_admin")])
    service = AuthorizationService(repository)  # type: ignore[arg-type]

    context = await service.resolve(identity("EvalHub.Viewer"))

    assert context.local_roles == []
    assert context.permissions == []


@pytest.mark.asyncio
async def test_entra_admin_and_platform_admin_receive_access_manage() -> None:
    repository = FakeAuthorizationRepository([active_assignment("platform_admin")])
    service = AuthorizationService(repository)  # type: ignore[arg-type]

    context = await service.resolve(identity("EvalHub.Admin"))

    assert Permission.ACCESS_MANAGE in context.permissions
    assert Permission.AUDIT_READ in context.permissions


@pytest.mark.asyncio
async def test_missing_permission_is_denied() -> None:
    repository = FakeAuthorizationRepository([])
    service = AuthorizationService(repository)  # type: ignore[arg-type]

    with pytest.raises(AuthorizationDeniedError, match="missing permission"):
        await service.require_permission(
            identity("EvalHub.Admin"),
            Permission.ACCESS_MANAGE,
        )
