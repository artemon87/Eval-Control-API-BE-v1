from src.models.authorization import (
    AccessUser,
    ActorReference,
    AssignmentCreate,
    AssignmentRecord,
    AssignmentRevoke,
    AuditEvent,
    AuthorizationContext,
    PaginatedAccessUsers,
    PaginatedAssignments,
    PaginatedAuditEvents,
)
from src.repositories.authorization import AuthorizationRepository
from src.security.authorization_identity import AuthorizationIdentity
from src.security.permissions import (
    ROLE_PERMISSIONS,
    Permission,
    PlatformRole,
    entra_allows_platform_role,
)


class AuthorizationDeniedError(RuntimeError):
    pass


class PrincipalNotFoundError(RuntimeError):
    pass


class InvalidAssignmentError(RuntimeError):
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
        if record_login:
            await self._repository.upsert_principal(identity)

        assignments, _ = await self._repository.list_assignments(
            tenant_id=identity.tenant_id,
            principal_id=identity.principal_id,
            active_only=True,
            limit=100,
        )
        local_roles: set[PlatformRole] = set()
        permissions: set[Permission] = set()

        for document in assignments:
            role = PlatformRole(document["local_role"])
            if not entra_allows_platform_role(identity.entra_roles, role):
                continue
            local_roles.add(role)
            permissions.update(ROLE_PERMISSIONS[role])

        return AuthorizationContext(
            tenant_id=identity.tenant_id,
            principal_id=identity.principal_id,
            entra_roles=sorted(identity.entra_roles),
            local_roles=sorted(local_roles, key=str),
            permissions=sorted(permissions, key=str),
        )

    async def require_permission(
        self,
        identity: AuthorizationIdentity,
        permission: Permission,
    ) -> AuthorizationContext:
        context = await self.resolve(identity)
        if permission not in context.permissions:
            raise AuthorizationDeniedError(f"missing permission: {permission}")
        return context

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
            assignments = [AssignmentRecord.model_validate(item) for item in documents]
            seen_roles = frozenset(principal.entra_roles_last_seen)
            permissions: set[Permission] = set()
            for assignment in assignments:
                if entra_allows_platform_role(seen_roles, assignment.local_role):
                    permissions.update(ROLE_PERMISSIONS[assignment.local_role])

            items.append(
                AccessUser(
                    tenant_id=principal.tenant_id,
                    principal_id=principal.principal_id,
                    display_name=principal.display_name,
                    email=principal.email,
                    entra_roles_last_seen=principal.entra_roles_last_seen,
                    entra_roles_last_confirmed_at=principal.updated_at,
                    assignments=assignments,
                    effective_permissions=sorted(permissions, key=str),
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
            items=[AssignmentRecord.model_validate(item) for item in documents],
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
        if not entra_allows_platform_role(
            frozenset(target.entra_roles_last_seen),
            payload.local_role,
        ):
            raise InvalidAssignmentError(
                "the user's last confirmed Entra role does not satisfy this platform role"
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
