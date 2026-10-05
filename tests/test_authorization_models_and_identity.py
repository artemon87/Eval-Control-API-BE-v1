from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from src.models.authorization import (
    AccessRequestCreate,
    AccessRequestDecision,
    AssignmentCreate,
    AuthorizationScope,
)
from src.security.authorization_identity import identity_from_authenticated_user
from src.security.permissions import (
    ROLE_PERMISSIONS,
    Permission,
    PlatformRole,
    entra_allows_platform_role,
)


def current_user(**overrides: Any) -> Any:
    values = {
        "authenticated": True,
        "tenant_id": " tenant-1 ",
        "object_id": " user-1 ",
        "display_name": "Test User",
        "email": "test@example.com",
        "entra_roles": [" EvalHub.Admin ", "", "EvalHub.Admin"],
        "role": SimpleNamespace(value="viewer"),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_identity_normalizes_authenticated_user() -> None:
    identity = identity_from_authenticated_user(current_user())

    assert identity.tenant_id == "tenant-1"
    assert identity.principal_id == "user-1"
    assert identity.display_name == "Test User"
    assert identity.email == "test@example.com"
    assert identity.entra_roles == frozenset({"EvalHub.Admin"})


def test_identity_uses_fallback_role_and_display_name() -> None:
    identity = identity_from_authenticated_user(
        current_user(display_name=None, email=None, entra_roles=[])
    )

    assert identity.display_name == "user-1"
    assert identity.entra_roles == frozenset({"viewer"})


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"authenticated": False}, "not authenticated"),
        ({"tenant_id": "  "}, "missing tenant_id"),
        ({"object_id": None}, "missing object_id"),
    ],
)
def test_identity_rejects_invalid_authenticated_user(
    overrides: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        identity_from_authenticated_user(current_user(**overrides))


def test_platform_admin_permissions_and_entra_prerequisite() -> None:
    assert ROLE_PERMISSIONS[PlatformRole.PLATFORM_ADMIN] == frozenset(Permission)
    assert entra_allows_platform_role(
        frozenset({"EvalHub.Admin"}), PlatformRole.PLATFORM_ADMIN
    )
    assert entra_allows_platform_role(
        frozenset({"admin"}), PlatformRole.PLATFORM_ADMIN
    )
    assert not entra_allows_platform_role(
        frozenset({"EvalHub.Editor"}), PlatformRole.PLATFORM_ADMIN
    )


def test_authorization_scope_only_accepts_global_wildcard() -> None:
    assert AuthorizationScope().id == "*"
    with pytest.raises(ValidationError, match="always global"):
        AuthorizationScope(id="repository-1")
    with pytest.raises(ValidationError):
        AuthorizationScope(id="*", unexpected=True)  # type: ignore[call-arg]


def test_assignment_expiration_normalizes_naive_future_datetime() -> None:
    future = datetime(2099, 1, 1)  # noqa: DTZ001 - intentionally exercises naive input
    payload = AssignmentCreate(
        tenant_id="tenant-1",
        principal_id="user-1",
        reason="Valid platform administration reason",
        expires_at=future,
    )

    assert payload.local_role is PlatformRole.PLATFORM_ADMIN
    assert payload.expires_at is not None
    assert payload.expires_at.tzinfo is UTC


def test_assignment_expiration_accepts_none_and_rejects_past() -> None:
    payload = AssignmentCreate(
        tenant_id="tenant-1",
        principal_id="user-1",
        reason="Valid platform administration reason",
        expires_at=None,
    )
    assert payload.expires_at is None

    with pytest.raises(ValidationError, match="must be in the future"):
        AssignmentCreate(
            tenant_id="tenant-1",
            principal_id="user-1",
            reason="Valid platform administration reason",
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )


def test_request_payload_constraints_are_enforced() -> None:
    assert (
        AccessRequestCreate(
            requested_role="EvalHub.Editor",
            business_reason="Needed for evaluation work",
        ).requested_role.value
        == "EvalHub.Editor"
    )
    assert (
        AccessRequestDecision(action="approve", note="Approved for project work").action.value
        == "approve"
    )
    with pytest.raises(ValidationError):
        AccessRequestCreate(
            requested_role="EvalHub.Editor",
            business_reason="short",
        )
    with pytest.raises(ValidationError):
        AccessRequestDecision(action="approve", note="no")
