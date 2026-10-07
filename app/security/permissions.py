from enum import StrEnum


class Permission(StrEnum):
    EVAL_ANNOTATE = "evals.annotate"
    EVAL_EDIT = "evals.edit"
    SUGGESTION_MODERATE = "suggestions.moderate"
    ACCESS_MANAGE = "access.manage"
    AUDIT_READ = "audit.read"


class EvalHubRole(StrEnum):
    EDITOR = "editor"
    ADMIN = "admin"


ROLE_PERMISSIONS: dict[EvalHubRole, frozenset[Permission]] = {
    EvalHubRole.EDITOR: frozenset(
        {
            Permission.EVAL_ANNOTATE,
            Permission.EVAL_EDIT,
        }
    ),
    EvalHubRole.ADMIN: frozenset(Permission),
}


def parse_evalhub_role(value: object) -> EvalHubRole:
    """Parse current roles and the previous local platform-admin value."""
    if value == "platform_admin":
        return EvalHubRole.ADMIN
    return EvalHubRole(value)


def effective_permissions(
    roles: frozenset[EvalHubRole],
) -> frozenset[Permission]:
    permissions: set[Permission] = set()
    for role in roles:
        permissions.update(ROLE_PERMISSIONS[role])
    return frozenset(permissions)
