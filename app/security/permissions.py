from enum import StrEnum


class Permission(StrEnum):
    SUGGESTION_MODERATE = "suggestions.moderate"
    RUN_OVERRIDE = "runs.override"
    POLICY_MANAGE = "policies.manage"
    METRIC_MANAGE = "metrics.manage"
    ACCESS_MANAGE = "access.manage"
    AUDIT_READ = "audit.read"


class PlatformRole(StrEnum):
    MODERATOR = "moderator"
    EVALUATION_ADMIN = "evaluation_admin"
    PLATFORM_ADMIN = "platform_admin"


ROLE_PERMISSIONS: dict[PlatformRole, frozenset[Permission]] = {
    PlatformRole.MODERATOR: frozenset({Permission.SUGGESTION_MODERATE}),
    PlatformRole.EVALUATION_ADMIN: frozenset(
        {
            Permission.SUGGESTION_MODERATE,
            Permission.RUN_OVERRIDE,
            Permission.POLICY_MANAGE,
            Permission.METRIC_MANAGE,
        }
    ),
    PlatformRole.PLATFORM_ADMIN: frozenset(Permission),
}

# A local database record must never elevate a user beyond the role granted by Entra.
ROLE_ENTRA_PREREQUISITES: dict[PlatformRole, frozenset[str]] = {
    PlatformRole.MODERATOR: frozenset(
        {"EvalHub.Editor", "EvalHub.Admin", "editor", "admin"}
    ),
    PlatformRole.EVALUATION_ADMIN: frozenset({"EvalHub.Admin", "admin"}),
    PlatformRole.PLATFORM_ADMIN: frozenset({"EvalHub.Admin", "admin"}),
}


def entra_allows_platform_role(
    entra_roles: frozenset[str],
    platform_role: PlatformRole,
) -> bool:
    return bool(entra_roles & ROLE_ENTRA_PREREQUISITES[platform_role])
