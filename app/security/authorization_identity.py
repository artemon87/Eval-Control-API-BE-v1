from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AuthorizationIdentity:
    tenant_id: str
    principal_id: str
    display_name: str
    email: str | None
    entra_roles: frozenset[str]


def identity_from_authenticated_user(user: Any) -> AuthorizationIdentity:
    """Adapt the existing validated authentication model to authorization.

    This function must only receive a user produced by the backend's verified JWT
    dependency. Never construct it from frontend/session input.
    """

    tenant_id = str(getattr(user, "tenant_id", "")).strip()
    principal_id = str(getattr(user, "object_id", "")).strip()
    if not tenant_id or not principal_id:
        raise ValueError("validated user is missing tenant_id or object_id")

    raw_roles = getattr(user, "entra_roles", ()) or ()
    mapped_role = getattr(getattr(user, "role", None), "value", None)
    roles = {str(role) for role in raw_roles if role}
    if mapped_role:
        roles.add(str(mapped_role))

    return AuthorizationIdentity(
        tenant_id=tenant_id,
        principal_id=principal_id,
        display_name=str(getattr(user, "display_name", None) or "Unknown user"),
        email=getattr(user, "email", None),
        entra_roles=frozenset(roles),
    )
