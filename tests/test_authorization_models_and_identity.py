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
    ResourceContext,
)
from src.security.authorization_identity import identity_from_authenticated_user
from src.security.permissions import (
    ROLE_PERMISSIONS,
    EvalHubRole,
    Permission,
    ResourceType,
    parse_evalhub_role,
)


def current_user(**overrides: Any) -> Any:
    values = {
        "authenticated": True,
        "tenant_id": " tenant-1 ",
        "object_id": " user-1 ",
        "display_name": "Test User",
        "email": "test@example.com",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_identity_normalizes_authenticated_user() -> None:
    identity = identity_from_authenticated_user(current_user())

    assert identity.tenant_id == "tenant-1"
    assert identity.principal_id == "user-1"
    assert identity.display_name == "Test User"
    assert identity.email == "test@example.com"


def test_identity_uses_principal_id_as_display_name_fallback() -> None:
    identity = identity_from_authenticated_user(
        current_user(display_name=None, email=None)
    )
    assert identity.display_name == "user-1"


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


def test_roles_are_owned_by_evalhub_and_parse_legacy_values() -> None:
    assert ROLE_PERMISSIONS[EvalHubRole.EDITOR] == frozenset(
        {Permission.EVAL_ANNOTATE, Permission.EVAL_EDIT}
    )
    assert ROLE_PERMISSIONS[EvalHubRole.ADMIN] == frozenset(Permission)
    assert parse_evalhub_role("editor") is EvalHubRole.EDITOR
    assert parse_evalhub_role("EvalHub.Editor") is EvalHubRole.EDITOR
    assert parse_evalhub_role("EvalHub.Admin") is EvalHubRole.ADMIN
    assert parse_evalhub_role("platform_admin") is EvalHubRole.ADMIN


def test_scope_accepts_legacy_global_and_matches_resource_constraints() -> None:
    legacy = AuthorizationScope.model_validate({"type": "global", "id": "*"})
    assert legacy.canonical_key() == "global"

    e2e = AuthorizationScope(
        type="resource",
        resource=ResourceType.EVALUATION,
        constraints={"eval_type": ["E2E"]},
    )
    assert e2e.allows(
        ResourceContext(resource="evaluation", attributes={"eval_type": "e2e"})
    )
    assert not e2e.allows(
        ResourceContext(resource="evaluation", attributes={"eval_type": "unit"})
    )


def test_scope_denies_when_a_future_constraint_is_missing() -> None:
    scoped = AuthorizationScope(
        type="resource",
        resource="evaluation",
        constraints={"eval_type": ["unit"], "category": ["platform"]},
    )
    assert not scoped.allows(
        ResourceContext(resource="evaluation", attributes={"eval_type": "unit"})
    )


def test_assignment_role_scope_and_expiration_validation() -> None:
    future = datetime(2099, 1, 1)  # noqa: DTZ001 - exercises normalization
    payload = AssignmentCreate(
        tenant_id="tenant-1",
        principal_id="user-1",
        local_role=EvalHubRole.EDITOR,
        scope=AuthorizationScope(
            type="resource",
            resource="evaluation",
            constraints={"eval_type": ["e2e"]},
        ),
        reason="Needed for evaluation work",
        expires_at=future,
    )
    assert payload.expires_at is not None
    assert payload.expires_at.tzinfo is UTC

    with pytest.raises(ValidationError, match="admin assignments must use the global"):
        AssignmentCreate(
            tenant_id="tenant-1",
            principal_id="user-1",
            local_role=EvalHubRole.ADMIN,
            scope=AuthorizationScope(
                type="resource",
                resource="evaluation",
                constraints={"eval_type": ["e2e"]},
            ),
            reason="Invalid scoped administrator",
        )

    with pytest.raises(ValidationError, match="must be in the future"):
        AssignmentCreate(
            tenant_id="tenant-1",
            principal_id="user-1",
            local_role=EvalHubRole.EDITOR,
            reason="Needed for evaluation work",
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )


def test_request_payload_constraints_and_legacy_roles() -> None:
    request = AccessRequestCreate(
        requested_role="EvalHub.Editor",
        business_reason="Needed for evaluation work",
    )
    assert request.requested_role is EvalHubRole.EDITOR
    assert (
        AccessRequestDecision(
            action="approve",
            note="Approved for project work",
        ).action.value
        == "approve"
    )

    with pytest.raises(ValidationError):
        AccessRequestCreate(
            requested_role="editor",
            business_reason="short",
        )
