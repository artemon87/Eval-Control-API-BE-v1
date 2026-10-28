import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.security.permissions import (
    EvalHubRole,
    Permission,
    ResourceType,
    parse_evalhub_role,
)


class PrincipalType(StrEnum):
    USER = "user"


class AssignmentStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class ScopeType(StrEnum):
    GLOBAL = "global"
    RESOURCE = "resource"


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
    resource: ResourceType | None = None
    constraints: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_global_scope(cls, value: object) -> object:
        if isinstance(value, dict) and value.get("type", "global") == "global":
            normalized = dict(value)
            normalized.pop("id", None)
            normalized["resource"] = None
            normalized["constraints"] = {}
            return normalized
        return value

    @field_validator("constraints")
    @classmethod
    def validate_constraints(
        cls,
        constraints: dict[str, list[str]],
    ) -> dict[str, list[str]]:
        normalized: dict[str, list[str]] = {}
        for raw_name, raw_values in constraints.items():
            name = raw_name.strip().lower()
            if not name or len(name) > 64:
                raise ValueError("scope constraint names must contain 1-64 characters")
            if not raw_values:
                raise ValueError(f"scope constraint {name!r} must contain a value")

            values: list[str] = []
            for raw_value in raw_values:
                value = raw_value.strip().lower()
                if not value or len(value) > 128:
                    raise ValueError(
                        f"scope constraint {name!r} values must contain 1-128 characters"
                    )
                if value not in values:
                    values.append(value)
            normalized[name] = values
        return normalized

    @model_validator(mode="after")
    def validate_shape(self) -> "AuthorizationScope":
        if self.type is ScopeType.GLOBAL:
            if self.resource is not None or self.constraints:
                raise ValueError("global scopes cannot contain a resource or constraints")
            return self

        if self.resource is None:
            raise ValueError("resource scopes require a resource")
        return self

    def allows(self, context: "ResourceContext") -> bool:
        if self.type is ScopeType.GLOBAL:
            return True
        if self.resource != context.resource:
            return False

        # Every stored constraint must be present on the resource and match.
        # Consequently, a future or misspelled constraint denies access rather
        # than being silently ignored.
        for name, allowed_values in self.constraints.items():
            actual_value = context.attributes.get(name)
            if actual_value is None or actual_value.lower() not in allowed_values:
                return False
        return True

    def canonical_key(self) -> str:
        if self.type is ScopeType.GLOBAL:
            return "global"
        payload = json.dumps(
            {
                "resource": self.resource,
                "constraints": {
                    name: sorted(values)
                    for name, values in sorted(self.constraints.items())
                },
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return f"resource:{digest}"


class ResourceContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource: ResourceType
    attributes: dict[str, str] = Field(default_factory=dict)

    @field_validator("attributes")
    @classmethod
    def normalize_attributes(cls, attributes: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for raw_name, raw_value in attributes.items():
            name = raw_name.strip().lower()
            value = raw_value.strip().lower()
            if not name or not value:
                raise ValueError("resource attribute names and values cannot be empty")
            normalized[name] = value
        return normalized


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

    @model_validator(mode="after")
    def validate_role_scope(self) -> "AssignmentCreate":
        if self.local_role == EvalHubRole.ADMIN and self.scope.type != ScopeType.GLOBAL:
            raise ValueError("admin assignments must use the global scope")
        if (
            self.local_role == EvalHubRole.EDITOR
            and self.scope.type == ScopeType.RESOURCE
            and self.scope.resource != ResourceType.EVALUATION
        ):
            raise ValueError("editor assignments can only scope the evaluation resource")
        return self


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
