from dataclasses import dataclass

from src.models.auth import CurrentUser


@dataclass(frozen=True, slots=True)
class AuthorizationIdentity:
    tenant_id: str
    principal_id: str
    display_name: str
    email: str | None


def identity_from_authenticated_user(user: CurrentUser) -> AuthorizationIdentity:
    if not user.authenticated:
        raise ValueError("user is not authenticated")

    tenant_id = (user.tenant_id or "").strip()
    principal_id = (user.object_id or "").strip()
    if not tenant_id:
        raise ValueError("authenticated user is missing tenant_id")
    if not principal_id:
        raise ValueError("authenticated user is missing object_id")

    return AuthorizationIdentity(
        tenant_id=tenant_id,
        principal_id=principal_id,
        display_name=user.display_name or user.email or principal_id,
        email=user.email,
    )
