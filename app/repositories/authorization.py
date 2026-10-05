import re
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ReturnDocument
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError
from src.repositories.base import normalize_document

from src.models.authorization import (
    AccessRequestAction,
    AccessRequestCreate,
    AccessRequestStatus,
    ActorReference,
    AssignmentCreate,
    AssignmentStatus,
    PrincipalRecord,
)
from src.security.authorization_identity import AuthorizationIdentity


class AssignmentAlreadyExistsError(RuntimeError):
    pass


class AssignmentNotFoundError(RuntimeError):
    pass


class AccessRequestConflictError(RuntimeError):
    pass


class AccessRequestNotFoundError(RuntimeError):
    pass


def _serialize(document: dict[str, Any] | None) -> dict[str, Any] | None:
    if document is None:
        return None
    normalized = normalize_document(document)
    if not isinstance(normalized, dict):
        raise TypeError("MongoDB document normalization must return a dictionary")
    return normalized


class AuthorizationRepository:
    PRINCIPALS = "authorization_principals"
    ASSIGNMENTS = "authorization_assignments"
    AUDIT_EVENTS = "authorization_audit_events"
    ACCESS_REQUESTS = "authorization_access_requests"

    def __init__(
        self,
        database: AsyncDatabase[dict[str, Any]],
    ) -> None:
        self._principals: AsyncCollection[dict[str, Any]] = database[
            self.PRINCIPALS
        ]
        self._assignments: AsyncCollection[dict[str, Any]] = database[
            self.ASSIGNMENTS
        ]
        self._audit: AsyncCollection[dict[str, Any]] = database[
            self.AUDIT_EVENTS
        ]
        self._access_requests: AsyncCollection[dict[str, Any]] = database[
            self.ACCESS_REQUESTS
        ]

    async def upsert_principal(
        self,
        identity: AuthorizationIdentity,
    ) -> PrincipalRecord:
        now = datetime.now(UTC)
        document = await self._principals.find_one_and_update(
            {
                "tenant_id": identity.tenant_id,
                "principal_id": identity.principal_id,
            },
            {
                "$set": {
                    "principal_type": "user",
                    "display_name": identity.display_name,
                    "email": identity.email,
                    "entra_roles_last_seen": sorted(identity.entra_roles),
                    "last_login_at": now,
                    "updated_at": now,
                },
                "$setOnInsert": {"first_login_at": now},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return PrincipalRecord.model_validate(document)

    async def get_principal(
        self,
        tenant_id: str,
        principal_id: str,
    ) -> PrincipalRecord | None:
        document = await self._principals.find_one(
            {"tenant_id": tenant_id, "principal_id": principal_id}
        )
        return PrincipalRecord.model_validate(document) if document else None

    async def list_principals(
        self,
        *,
        tenant_id: str,
        search: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[PrincipalRecord], int]:
        query: dict[str, Any] = {"tenant_id": tenant_id}
        if search:
            safe_search = re.escape(search.strip()[:100])
            query["$or"] = [
                {"display_name": {"$regex": safe_search, "$options": "i"}},
                {"email": {"$regex": safe_search, "$options": "i"}},
            ]

        total = await self._principals.count_documents(query)
        cursor = (
            self._principals.find(query)
            .sort([("display_name", 1), ("principal_id", 1)])
            .skip(offset)
            .limit(limit)
        )
        return [PrincipalRecord.model_validate(item) async for item in cursor], total

    async def create_assignment(
        self,
        assignment: AssignmentCreate,
        actor: ActorReference,
        target: ActorReference,
        *,
        request_id: str | None,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        await self._assignments.update_many(
            {
                "tenant_id": assignment.tenant_id,
                "principal_id": assignment.principal_id,
                "local_role": assignment.local_role,
                "scope.type": assignment.scope.type,
                "scope.id": assignment.scope.id,
                "status": AssignmentStatus.ACTIVE,
                "expires_at": {"$lte": now},
            },
            {"$set": {"status": AssignmentStatus.EXPIRED}},
        )
        document = {
            **assignment.model_dump(mode="python"),
            "status": AssignmentStatus.ACTIVE,
            "granted_by": actor.model_dump(mode="python"),
            "created_at": now,
            "revoked_at": None,
            "revoked_by": None,
            "revocation_reason": None,
        }
        try:
            result = await self._assignments.insert_one(document)
        except DuplicateKeyError as exc:
            raise AssignmentAlreadyExistsError(
                "an active assignment already exists for this user, role, and scope"
            ) from exc

        assignment_id = str(result.inserted_id)
        await self._audit.insert_one(
            {
                "event_type": "authorization.assignment.granted",
                "actor": actor.model_dump(mode="python"),
                "target": target.model_dump(mode="python"),
                "assignment_id": assignment_id,
                "local_role": assignment.local_role,
                "scope": assignment.scope.model_dump(mode="python"),
                "reason": assignment.reason,
                "occurred_at": now,
                "request_id": request_id,
            }
        )
        document["_id"] = assignment_id
        return document

    async def revoke_assignment(
        self,
        assignment_id: str,
        tenant_id: str,
        actor: ActorReference,
        target: ActorReference,
        reason: str,
        *,
        request_id: str | None,
    ) -> dict[str, Any]:
        try:
            object_id = ObjectId(assignment_id)
        except (InvalidId, TypeError) as exc:
            raise AssignmentNotFoundError("assignment not found") from exc

        now = datetime.now(UTC)
        updated = await self._assignments.find_one_and_update(
            {
                "_id": object_id,
                "tenant_id": tenant_id,
                "status": AssignmentStatus.ACTIVE,
            },
            {
                "$set": {
                    "status": AssignmentStatus.REVOKED,
                    "revoked_at": now,
                    "revoked_by": actor.model_dump(mode="python"),
                    "revocation_reason": reason,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        serialized = _serialize(updated)
        if serialized is None:
            raise AssignmentNotFoundError("active assignment not found")

        await self._audit.insert_one(
            {
                "event_type": "authorization.assignment.revoked",
                "actor": actor.model_dump(mode="python"),
                "target": target.model_dump(mode="python"),
                "assignment_id": assignment_id,
                "local_role": serialized["local_role"],
                "scope": serialized["scope"],
                "reason": reason,
                "occurred_at": now,
                "request_id": request_id,
            }
        )
        return serialized

    async def get_assignment(
        self,
        assignment_id: str,
        *,
        tenant_id: str,
    ) -> dict[str, Any] | None:
        try:
            object_id = ObjectId(assignment_id)
        except (InvalidId, TypeError):
            return None
        return _serialize(
            await self._assignments.find_one(
                {"_id": object_id, "tenant_id": tenant_id}
            )
        )

    async def list_assignments(
        self,
        *,
        tenant_id: str,
        principal_id: str | None = None,
        active_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        query: dict[str, Any] = {
            "tenant_id": tenant_id,
            "local_role": "platform_admin",
        }
        if principal_id:
            query["principal_id"] = principal_id
        if active_only:
            now = datetime.now(UTC)
            query["status"] = AssignmentStatus.ACTIVE
            query["$or"] = [
                {"expires_at": None},
                {"expires_at": {"$gt": now}},
                {"expires_at": {"$exists": False}},
            ]

        total = await self._assignments.count_documents(query)
        cursor = (
            self._assignments.find(query)
            .sort([("created_at", -1), ("_id", -1)])
            .skip(offset)
            .limit(limit)
        )
        return [dict(_serialize(item) or {}) async for item in cursor], total

    async def list_audit_events(
        self,
        *,
        tenant_id: str,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        query = {
            "$or": [
                {"actor.tenant_id": tenant_id},
                {"target.tenant_id": tenant_id},
            ]
        }
        total = await self._audit.count_documents(query)
        cursor = (
            self._audit.find(query)
            .sort([("occurred_at", -1), ("_id", -1)])
            .skip(offset)
            .limit(limit)
        )
        return [dict(_serialize(item) or {}) async for item in cursor], total

    async def create_access_request(
        self,
        identity: AuthorizationIdentity,
        payload: AccessRequestCreate,
        *,
        request_id: str | None,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        document = {
            "tenant_id": identity.tenant_id,
            "principal_id": identity.principal_id,
            "display_name": identity.display_name,
            "email": identity.email,
            **payload.model_dump(mode="python"),
            "status": AccessRequestStatus.PENDING,
            "created_at": now,
            "updated_at": now,
            "decided_at": None,
            "decided_by": None,
            "decision_note": None,
            "fulfilled_at": None,
        }
        try:
            result = await self._access_requests.insert_one(document)
        except DuplicateKeyError as exc:
            raise AccessRequestConflictError(
                "an open request for this Entra role already exists"
            ) from exc

        access_request_id = str(result.inserted_id)
        actor = ActorReference(
            tenant_id=identity.tenant_id,
            principal_id=identity.principal_id,
            display_name=identity.display_name,
        )
        await self._audit.insert_one(
            {
                "event_type": "authorization.access_request.created",
                "actor": actor.model_dump(mode="python"),
                "target": actor.model_dump(mode="python"),
                "access_request_id": access_request_id,
                "requested_role": payload.requested_role,
                "reason": payload.business_reason,
                "occurred_at": now,
                "request_id": request_id,
            }
        )
        document["_id"] = access_request_id
        return document

    async def list_access_requests(
        self,
        *,
        tenant_id: str,
        principal_id: str | None = None,
        status: AccessRequestStatus | None = None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        query: dict[str, Any] = {"tenant_id": tenant_id}
        if principal_id:
            query["principal_id"] = principal_id
        if status:
            query["status"] = status
        total = await self._access_requests.count_documents(query)
        cursor = (
            self._access_requests.find(query)
            .sort([("created_at", -1), ("_id", -1)])
            .skip(offset)
            .limit(limit)
        )
        return [dict(_serialize(item) or {}) async for item in cursor], total

    async def cancel_access_request(
        self,
        access_request_id: str,
        identity: AuthorizationIdentity,
        *,
        request_id: str | None,
    ) -> dict[str, Any]:
        try:
            object_id = ObjectId(access_request_id)
        except (InvalidId, TypeError) as exc:
            raise AccessRequestNotFoundError("access request not found") from exc
        now = datetime.now(UTC)
        updated = await self._access_requests.find_one_and_update(
            {
                "_id": object_id,
                "tenant_id": identity.tenant_id,
                "principal_id": identity.principal_id,
                "status": {"$in": [AccessRequestStatus.PENDING, AccessRequestStatus.APPROVED]},
            },
            {"$set": {"status": AccessRequestStatus.CANCELLED, "updated_at": now}},
            return_document=ReturnDocument.AFTER,
        )
        serialized = _serialize(updated)
        if serialized is None:
            raise AccessRequestNotFoundError("open access request not found")
        actor = ActorReference(
            tenant_id=identity.tenant_id,
            principal_id=identity.principal_id,
            display_name=identity.display_name,
        )
        await self._audit.insert_one(
            {
                "event_type": "authorization.access_request.cancelled",
                "actor": actor.model_dump(mode="python"),
                "target": actor.model_dump(mode="python"),
                "access_request_id": access_request_id,
                "requested_role": serialized["requested_role"],
                "reason": "Cancelled by requester",
                "occurred_at": now,
                "request_id": request_id,
            }
        )
        return serialized

    async def decide_access_request(
        self,
        access_request_id: str,
        tenant_id: str,
        actor: ActorReference,
        action: AccessRequestAction,
        note: str,
        *,
        request_id: str | None,
    ) -> dict[str, Any]:
        try:
            object_id = ObjectId(access_request_id)
        except (InvalidId, TypeError) as exc:
            raise AccessRequestNotFoundError("access request not found") from exc

        transitions = {
            AccessRequestAction.APPROVE: (
                [AccessRequestStatus.PENDING],
                AccessRequestStatus.APPROVED,
            ),
            AccessRequestAction.FULFILL: (
                [AccessRequestStatus.APPROVED],
                AccessRequestStatus.FULFILLED,
            ),
            AccessRequestAction.REJECT: (
                [AccessRequestStatus.PENDING, AccessRequestStatus.APPROVED],
                AccessRequestStatus.REJECTED,
            ),
        }
        allowed_statuses, new_status = transitions[action]
        now = datetime.now(UTC)
        updates: dict[str, Any] = {
            "status": new_status,
            "updated_at": now,
            "decided_at": now,
            "decided_by": actor.model_dump(mode="python"),
            "decision_note": note,
        }
        if action is AccessRequestAction.FULFILL:
            updates["fulfilled_at"] = now

        updated = await self._access_requests.find_one_and_update(
            {
                "_id": object_id,
                "tenant_id": tenant_id,
                "status": {"$in": allowed_statuses},
            },
            {"$set": updates},
            return_document=ReturnDocument.AFTER,
        )
        serialized = _serialize(updated)
        if serialized is None:
            raise AccessRequestNotFoundError(
                "access request not found or transition is no longer valid"
            )
        target = ActorReference(
            tenant_id=tenant_id,
            principal_id=serialized["principal_id"],
            display_name=serialized.get("display_name"),
        )
        await self._audit.insert_one(
            {
                "event_type": f"authorization.access_request.{new_status}",
                "actor": actor.model_dump(mode="python"),
                "target": target.model_dump(mode="python"),
                "access_request_id": access_request_id,
                "requested_role": serialized["requested_role"],
                "reason": note,
                "occurred_at": now,
                "request_id": request_id,
            }
        )
        return serialized
