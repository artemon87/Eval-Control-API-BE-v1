from dataclasses import dataclass
from src.models.auth import CurrentUser


@dataclass(frozen=True, slots=True)
class AuthorizationIdentity:
    tenant_id: str
    principal_id: str
    display_name: str
    email: str | None
    entra_roles: frozenset[str]


def identity_from_authenticated_user(user: CurrentUser) -> AuthorizationIdentity:
    if not user.authenticated:
        raise ValueError("user is not authenticated")

    tenant_id = (user.tenant_id or "").strip()
    principal_id = (user.object_id or "").strip()
    if not tenant_id:
        raise ValueError("authenticated user is missing tenant_id")
    if not principal_id:
        raise ValueError("authenticated user is missing object_id")

    roles = {role.strip() for role in user.entra_roles if role and role.strip()}
    if not roles:
        roles.add(user.role.value)

    return AuthorizationIdentity(
        tenant_id=tenant_id,
        principal_id=principal_id,
        display_name=user.display_name or user.email or principal_id,
        email=user.email,
        entra_roles=frozenset(roles),
    )
