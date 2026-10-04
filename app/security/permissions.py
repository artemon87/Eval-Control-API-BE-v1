from enum import StrEnum


class Permission(StrEnum):
    ACCESS_MANAGE = "access.manage"
    AUDIT_READ = "audit.read"


class PlatformRole(StrEnum):
    PLATFORM_ADMIN = "platform_admin"


ROLE_PERMISSIONS: dict[PlatformRole, frozenset[Permission]] = {
    PlatformRole.PLATFORM_ADMIN: frozenset(Permission),
}


ROLE_ENTRA_PREREQUISITES: dict[PlatformRole, frozenset[str]] = {
    PlatformRole.PLATFORM_ADMIN: frozenset({"EvalHub.Admin", "admin"}),
}


def entra_allows_platform_role(
    entra_roles: frozenset[str],
    platform_role: PlatformRole,
) -> bool:
    return bool(entra_roles & ROLE_ENTRA_PREREQUISITES[platform_role])
