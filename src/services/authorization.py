from src.models.authorization import (
    AccessRequestCreate,
    AccessRequestDecision,
    AccessRequestAction,
    AccessRequestRecord,
    AccessRequestStatus,
    AccessUser,
    ActorReference,
    AssignmentCreate,
    AssignmentRecord,
    AssignmentRevoke,
    AuditEvent,
    AuthorizationContext,
    AuthorizationScope,
    PaginatedAccessRequests,
    PaginatedAccessUsers,
    PaginatedAssignments,
    PaginatedAuditEvents,
    ResourceContext,
    ScopeType,
)
from src.repositories.authorization import (
    AccessRequestNotFoundError,
    AuthorizationRepository,
)
from src.security.authorization_identity import AuthorizationIdentity
from src.security.permissions import (
    EvalHubRole,
    PERMISSION_RESOURCES,
    Permission,
    effective_permissions,
    parse_evalhub_role,
    role_has_permission,
)


class AuthorizationDeniedError(RuntimeError):
    pass


class PrincipalNotFoundError(RuntimeError):
    pass


class AccessRequestAlreadySatisfiedError(RuntimeError):
    pass


class AuthorizationService:
    def __init__(self, repository: AuthorizationRepository) -> None:
        self._repository = repository

    async def resolve(
        self,
        identity: AuthorizationIdentity,
        *,
        record_login: bool = True,
    ) -> AuthorizationContext:
        context, _ = await self._resolve_with_assignments(
            identity,
            record_login=record_login,
        )
        return context

    async def _resolve_with_assignments(
        self,
        identity: AuthorizationIdentity,
        *,
        record_login: bool,
    ) -> tuple[
        AuthorizationContext,
        list[tuple[EvalHubRole, AuthorizationScope]],
    ]:
        if record_login:
            await self._repository.upsert_principal(identity)

        assignments, _ = await self._repository.list_assignments(
            tenant_id=identity.tenant_id,
            principal_id=identity.principal_id,
            active_only=True,
            limit=100,
        )
        roles: set[EvalHubRole] = set()
        parsed_assignments: list[tuple[EvalHubRole, AuthorizationScope]] = []

        for document in assignments:
            try:
                role = parse_evalhub_role(document["local_role"])
                scope = AuthorizationScope.model_validate(document.get("scope", {}))
            except (TypeError, ValueError):
                continue
            roles.add(role)
            parsed_assignments.append((role, scope))

        context = AuthorizationContext(
            tenant_id=identity.tenant_id,
            principal_id=identity.principal_id,
            roles=sorted(roles, key=str),
            permissions=sorted(
                effective_permissions(frozenset(roles)),
                key=str,
            ),
        )
        return context, parsed_assignments

    async def require_permission(
        self,
        identity: AuthorizationIdentity,
        permission: Permission,
    ) -> AuthorizationContext:
        context, assignments = await self._resolve_with_assignments(
            identity,
            record_login=True,
        )
        if any(
            scope.type == ScopeType.GLOBAL and role_has_permission(role, permission)
            for role, scope in assignments
        ):
            return context
        raise AuthorizationDeniedError(f"missing global permission: {permission}")

    async def require_resource_permission(
        self,
        identity: AuthorizationIdentity,
        permission: Permission,
        resource: ResourceContext,
    ) -> AuthorizationContext:
        expected_resource = PERMISSION_RESOURCES[permission]
        if expected_resource != resource.resource:
            raise ValueError(
                f"permission {permission} applies to {expected_resource}, "
                f"not {resource.resource}"
            )

        context, assignments = await self._resolve_with_assignments(
            identity,
            record_login=True,
        )
        if any(
            role_has_permission(role, permission) and scope.allows(resource)
            for role, scope in assignments
        ):
            return context

        raise AuthorizationDeniedError(
            f"missing permission {permission} for {resource.resource} "
            f"with attributes {resource.attributes}"
        )

    async def list_users(
        self,
        actor: AuthorizationIdentity,
        *,
        search: str | None,
        limit: int,
        offset: int,
    ) -> PaginatedAccessUsers:
        await self.require_permission(actor, Permission.ACCESS_MANAGE)
        principals, total = await self._repository.list_principals(
            tenant_id=actor.tenant_id,
            search=search,
            limit=limit,
            offset=offset,
        )

        items: list[AccessUser] = []
        for principal in principals:
            documents, _ = await self._repository.list_assignments(
                tenant_id=actor.tenant_id,
                principal_id=principal.principal_id,
                active_only=True,
                limit=100,
            )
            assignments = [
                AssignmentRecord.model_validate(item)
                for item in documents
                if item.get("local_role") in {"editor", "admin", "platform_admin"}
            ]
            roles = frozenset(assignment.local_role for assignment in assignments)

            items.append(
                AccessUser(
                    tenant_id=principal.tenant_id,
                    principal_id=principal.principal_id,
                    display_name=principal.display_name,
                    email=principal.email,
                    roles=sorted(roles, key=str),
                    assignments=assignments,
                    effective_permissions=sorted(
                        effective_permissions(roles),
                        key=str,
                    ),
                    last_login_at=principal.last_login_at,
                )
            )

        return PaginatedAccessUsers(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
        )

    async def list_assignments(
        self,
        actor: AuthorizationIdentity,
        *,
        active_only: bool,
        limit: int,
        offset: int,
    ) -> PaginatedAssignments:
        await self.require_permission(actor, Permission.ACCESS_MANAGE)
        documents, total = await self._repository.list_assignments(
            tenant_id=actor.tenant_id,
            active_only=active_only,
            limit=limit,
            offset=offset,
        )
        return PaginatedAssignments(
            items=[
                AssignmentRecord.model_validate(item)
                for item in documents
                if item.get("local_role") in {"editor", "admin", "platform_admin"}
            ],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def create_assignment(
        self,
        actor: AuthorizationIdentity,
        payload: AssignmentCreate,
        *,
        request_id: str | None,
    ) -> AssignmentRecord:
        await self.require_permission(actor, Permission.ACCESS_MANAGE)
        if payload.tenant_id != actor.tenant_id:
            raise AuthorizationDeniedError("cross-tenant assignment is not allowed")
        if payload.principal_id == actor.principal_id:
            raise AuthorizationDeniedError("self-assignment is not allowed")

        target = await self._repository.get_principal(
            payload.tenant_id,
            payload.principal_id,
        )
        if target is None:
            raise PrincipalNotFoundError(
                "user is not known to EvalHub; ask the user to sign in once"
            )
        actor_ref = ActorReference(
            tenant_id=actor.tenant_id,
            principal_id=actor.principal_id,
            display_name=actor.display_name,
        )
        target_ref = ActorReference(
            tenant_id=target.tenant_id,
            principal_id=target.principal_id,
            display_name=target.display_name,
        )
        document = await self._repository.create_assignment(
            payload,
            actor_ref,
            target_ref,
            request_id=request_id,
        )
        return AssignmentRecord.model_validate(document)

    async def revoke_assignment(
        self,
        actor: AuthorizationIdentity,
        assignment_id: str,
        payload: AssignmentRevoke,
        *,
        request_id: str | None,
    ) -> AssignmentRecord:
        await self.require_permission(actor, Permission.ACCESS_MANAGE)
        existing = await self._repository.get_assignment(
            assignment_id,
            tenant_id=actor.tenant_id,
        )
        if existing is None:
            raise PrincipalNotFoundError("assignment not found")
        if existing["principal_id"] == actor.principal_id:
            raise AuthorizationDeniedError(
                "self-revocation must be performed by another admin"
            )
        actor_ref = ActorReference(
            tenant_id=actor.tenant_id,
            principal_id=actor.principal_id,
            display_name=actor.display_name,
        )
        target = await self._repository.get_principal(
            actor.tenant_id,
            existing["principal_id"],
        )
        target_ref = ActorReference(
            tenant_id=actor.tenant_id,
            principal_id=existing["principal_id"],
            display_name=target.display_name if target else None,
        )
        document = await self._repository.revoke_assignment(
            assignment_id,
            actor.tenant_id,
            actor_ref,
            target_ref,
            payload.reason,
            request_id=request_id,
        )
        return AssignmentRecord.model_validate(document)

    async def list_audit_events(
        self,
        actor: AuthorizationIdentity,
        *,
        limit: int,
        offset: int,
    ) -> PaginatedAuditEvents:
        await self.require_permission(actor, Permission.AUDIT_READ)
        documents, total = await self._repository.list_audit_events(
            tenant_id=actor.tenant_id,
            limit=limit,
            offset=offset,
        )
        return PaginatedAuditEvents(
            items=[AuditEvent.model_validate(item) for item in documents],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def create_access_request(
        self,
        identity: AuthorizationIdentity,
        payload: AccessRequestCreate,
        *,
        request_id: str | None,
    ) -> AccessRequestRecord:
        await self._repository.upsert_principal(identity)
        context = await self.resolve(identity, record_login=False)
        existing_roles = set(context.roles)
        if payload.requested_role in existing_roles:
            raise AccessRequestAlreadySatisfiedError(
                "you already have the requested EvalHub role"
            )
        if EvalHubRole.ADMIN in existing_roles:
            raise AccessRequestAlreadySatisfiedError(
                "EvalHub Admin already includes Editor access"
            )
        document = await self._repository.create_access_request(
            identity,
            payload,
            request_id=request_id,
        )
        return AccessRequestRecord.model_validate(document)

    async def list_my_access_requests(
        self,
        identity: AuthorizationIdentity,
        *,
        limit: int,
        offset: int,
    ) -> PaginatedAccessRequests:
        documents, total = await self._repository.list_access_requests(
            tenant_id=identity.tenant_id,
            principal_id=identity.principal_id,
            limit=limit,
            offset=offset,
        )
        return PaginatedAccessRequests(
            items=[AccessRequestRecord.model_validate(item) for item in documents],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def cancel_access_request(
        self,
        identity: AuthorizationIdentity,
        access_request_id: str,
        *,
        request_id: str | None,
    ) -> AccessRequestRecord:
        document = await self._repository.cancel_access_request(
            access_request_id,
            identity,
            request_id=request_id,
        )
        return AccessRequestRecord.model_validate(document)

    async def list_access_requests(
        self,
        actor: AuthorizationIdentity,
        *,
        status: AccessRequestStatus | None,
        limit: int,
        offset: int,
    ) -> PaginatedAccessRequests:
        await self.require_permission(actor, Permission.ACCESS_MANAGE)
        documents, total = await self._repository.list_access_requests(
            tenant_id=actor.tenant_id,
            status=status,
            limit=limit,
            offset=offset,
        )
        return PaginatedAccessRequests(
            items=[AccessRequestRecord.model_validate(item) for item in documents],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def decide_access_request(
        self,
        actor: AuthorizationIdentity,
        access_request_id: str,
        payload: AccessRequestDecision,
        *,
        request_id: str | None,
    ) -> AccessRequestRecord:
        await self.require_permission(actor, Permission.ACCESS_MANAGE)
        request_document = await self._repository.get_access_request(
            access_request_id,
            tenant_id=actor.tenant_id,
        )
        if request_document is None:
            raise AccessRequestNotFoundError("access request not found")
        access_request = AccessRequestRecord.model_validate(request_document)
        if access_request.status is not AccessRequestStatus.PENDING:
            raise AccessRequestNotFoundError(
                "access request is no longer pending"
            )
        if access_request.principal_id == actor.principal_id:
            raise AuthorizationDeniedError("self-approval is not allowed")

        actor_ref = ActorReference(
            tenant_id=actor.tenant_id,
            principal_id=actor.principal_id,
            display_name=actor.display_name,
        )
        if payload.action is AccessRequestAction.APPROVE:
            target = await self._repository.get_principal(
                access_request.tenant_id,
                access_request.principal_id,
            )
            if target is None:
                raise PrincipalNotFoundError("requesting user is no longer known")
            target_ref = ActorReference(
                tenant_id=target.tenant_id,
                principal_id=target.principal_id,
                display_name=target.display_name,
            )
            document = await self._repository.approve_access_request(
                access_request_id,
                actor.tenant_id,
                actor_ref,
                target_ref,
                AssignmentCreate(
                    tenant_id=access_request.tenant_id,
                    principal_id=access_request.principal_id,
                    local_role=access_request.requested_role,
                    scope=AuthorizationScope(),
                    reason=payload.note,
                ),
                payload.note,
                request_id=request_id,
            )
            return AccessRequestRecord.model_validate(document)

        document = await self._repository.decide_access_request(
            access_request_id,
            actor.tenant_id,
            actor_ref,
            payload.action,
            payload.note,
            request_id=request_id,
        )
        return AccessRequestRecord.model_validate(document)
