from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.security.permissions import EvalHubRole, Permission, parse_evalhub_role


class PrincipalType(StrEnum):
    USER = "user"


class AssignmentStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class ScopeType(StrEnum):
    GLOBAL = "global"


class AccessRequestStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    FULFILLED = "fulfilled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class AccessRequestAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class AuthorizationScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ScopeType = ScopeType.GLOBAL
    id: str = "*"

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if value != "*":
            raise ValueError("EvalHub role assignments are currently global")
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
    first_login_at: datetime
    last_login_at: datetime
    updated_at: datetime


class AssignmentRecord(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str = Field(alias="_id")
    tenant_id: str
    principal_id: str
    local_role: EvalHubRole
    scope: AuthorizationScope
    status: AssignmentStatus
    reason: str
    granted_by: ActorReference
    created_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    revoked_by: ActorReference | None = None
    revocation_reason: str | None = None

    @field_validator("local_role", mode="before")
    @classmethod
    def normalize_legacy_role(cls, value: object) -> EvalHubRole:
        return parse_evalhub_role(value)


class AssignmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    principal_id: str
    local_role: EvalHubRole
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
    roles: list[EvalHubRole]
    permissions: list[Permission]


class AccessUser(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    principal_id: str
    display_name: str
    email: str | None
    roles: list[EvalHubRole]
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

    requested_role: EvalHubRole
    business_reason: str = Field(min_length=10, max_length=1000)

    @field_validator("requested_role", mode="before")
    @classmethod
    def normalize_legacy_role(cls, value: object) -> object:
        return {
            "EvalHub.Editor": EvalHubRole.EDITOR,
            "EvalHub.Admin": EvalHubRole.ADMIN,
        }.get(value, value)


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
    requested_role: EvalHubRole
    business_reason: str
    status: AccessRequestStatus
    created_at: datetime
    updated_at: datetime
    decided_at: datetime | None = None
    decided_by: ActorReference | None = None
    decision_note: str | None = None
    fulfilled_at: datetime | None = None

    @field_validator("requested_role", mode="before")
    @classmethod
    def normalize_legacy_role(cls, value: object) -> object:
        return {
            "EvalHub.Editor": EvalHubRole.EDITOR,
            "EvalHub.Admin": EvalHubRole.ADMIN,
        }.get(value, value)


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
    local_role: EvalHubRole | None = None
    requested_role: EvalHubRole | None = None
    scope: AuthorizationScope | None = None
    request_id: str | None = None

    @field_validator("local_role", mode="before")
    @classmethod
    def normalize_legacy_local_role(cls, value: object) -> object:
        return None if value is None else parse_evalhub_role(value)

    @field_validator("requested_role", mode="before")
    @classmethod
    def normalize_legacy_requested_role(cls, value: object) -> object:
        return {
            "EvalHub.Editor": EvalHubRole.EDITOR,
            "EvalHub.Admin": EvalHubRole.ADMIN,
        }.get(value, value)


class PaginatedAuditEvents(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AuditEvent]
    total: int
    limit: int
    offset: int
