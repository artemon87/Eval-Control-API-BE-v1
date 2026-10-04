from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.security.permissions import Permission, PlatformRole


class PrincipalType(StrEnum):
    USER = "user"


class AssignmentStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class ScopeType(StrEnum):
    GLOBAL = "global"


class RequestedEntraRole(StrEnum):
    EDITOR = "EvalHub.Editor"
    ADMIN = "EvalHub.Admin"


class AccessRequestStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    FULFILLED = "fulfilled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class AccessRequestAction(StrEnum):
    APPROVE = "approve"
    FULFILL = "fulfill"
    REJECT = "reject"


class AuthorizationScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ScopeType = ScopeType.GLOBAL
    id: str = "*"

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if value != "*":
            raise ValueError("platform_admin is always global")
        return value


class ActorReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    principal_id: str
    display_name: str | None = None


class PrincipalRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tenant_id: str
    principal_id: str
    principal_type: PrincipalType = PrincipalType.USER
    display_name: str
    email: str | None = None
    entra_roles_last_seen: list[str] = Field(default_factory=list)
    first_login_at: datetime
    last_login_at: datetime
    updated_at: datetime


class AssignmentRecord(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str = Field(alias="_id")
    tenant_id: str
    principal_id: str
    local_role: PlatformRole
    scope: AuthorizationScope
    status: AssignmentStatus
    reason: str
    granted_by: ActorReference
    created_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    revoked_by: ActorReference | None = None
    revocation_reason: str | None = None


class AssignmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    principal_id: str
    local_role: PlatformRole = PlatformRole.PLATFORM_ADMIN
    scope: AuthorizationScope = Field(default_factory=AuthorizationScope)
    reason: str = Field(min_length=5, max_length=500)
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def validate_expiration(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        normalized = value if value.tzinfo else value.replace(tzinfo=UTC)
        if normalized <= datetime.now(UTC):
            raise ValueError("expires_at must be in the future")
        return normalized


class AssignmentRevoke(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=5, max_length=500)


class AuthorizationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    principal_id: str
    entra_roles: list[str]
    local_roles: list[PlatformRole]
    permissions: list[Permission]


class AccessUser(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    principal_id: str
    display_name: str
    email: str | None
    entra_roles_last_seen: list[str]
    entra_roles_last_confirmed_at: datetime
    assignments: list[AssignmentRecord]
    effective_permissions: list[Permission]
    last_login_at: datetime


class PaginatedAccessUsers(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AccessUser]
    total: int
    limit: int
    offset: int


class PaginatedAssignments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AssignmentRecord]
    total: int
    limit: int
    offset: int


class AccessRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_role: RequestedEntraRole
    business_reason: str = Field(min_length=10, max_length=1000)


class AccessRequestDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: AccessRequestAction
    note: str = Field(min_length=5, max_length=1000)


class AccessRequestRecord(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str = Field(alias="_id")
    tenant_id: str
    principal_id: str
    display_name: str
    email: str | None = None
    requested_role: RequestedEntraRole
    business_reason: str
    status: AccessRequestStatus
    created_at: datetime
    updated_at: datetime
    decided_at: datetime | None = None
    decided_by: ActorReference | None = None
    decision_note: str | None = None
    fulfilled_at: datetime | None = None


class PaginatedAccessRequests(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AccessRequestRecord]
    total: int
    limit: int
    offset: int


class AuditEvent(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str = Field(alias="_id")
    event_type: str
    actor: ActorReference
    target: ActorReference
    reason: str
    occurred_at: datetime
    assignment_id: str | None = None
    access_request_id: str | None = None
    local_role: PlatformRole | None = None
    requested_role: RequestedEntraRole | None = None
    scope: AuthorizationScope | None = None
    request_id: str | None = None


class PaginatedAuditEvents(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AuditEvent]
    total: int
    limit: int
    offset: int
