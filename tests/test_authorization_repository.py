from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from src.models.authorization import (
    AccessRequestAction,
    AccessRequestCreate,
    ActorReference,
    AssignmentCreate,
)
from src.repositories import authorization as repository_module
from src.repositories.authorization import (
    AccessRequestConflictError,
    AccessRequestNotFoundError,
    AssignmentAlreadyExistsError,
    AssignmentNotFoundError,
    AuthorizationRepository,
)
from src.security.authorization_identity import AuthorizationIdentity


class FakeInsertResult:
    def __init__(self, inserted_id: ObjectId | None = None) -> None:
        self.inserted_id = inserted_id or ObjectId()


class FakeCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = documents
        self.sort_value: Any = None
        self.skip_value: int | None = None
        self.limit_value: int | None = None

    def sort(self, value: Any) -> "FakeCursor":
        self.sort_value = value
        return self

    def skip(self, value: int) -> "FakeCursor":
        self.skip_value = value
        return self

    def limit(self, value: int) -> "FakeCursor":
        self.limit_value = value
        return self

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        async def iterate() -> AsyncIterator[dict[str, Any]]:
            for document in self.documents:
                yield document

        return iterate()


class FakeCollection:
    def __init__(self) -> None:
        self.find_one_and_update_result: dict[str, Any] | None = None
        self.find_one_result: dict[str, Any] | None = None
        self.find_results: list[dict[str, Any]] = []
        self.count_result = 0
        self.insert_error: Exception | None = None
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.last_cursor: FakeCursor | None = None

    async def find_one_and_update(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(("find_one_and_update", args, kwargs))
        return self.find_one_and_update_result

    async def find_one(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(("find_one", args, kwargs))
        return self.find_one_result

    async def count_documents(self, *args: Any, **kwargs: Any) -> int:
        self.calls.append(("count_documents", args, kwargs))
        return self.count_result

    def find(self, *args: Any, **kwargs: Any) -> FakeCursor:
        self.calls.append(("find", args, kwargs))
        self.last_cursor = FakeCursor(self.find_results)
        return self.last_cursor

    async def update_many(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("update_many", args, kwargs))

    async def insert_one(self, *args: Any, **kwargs: Any) -> FakeInsertResult:
        self.calls.append(("insert_one", args, kwargs))
        if self.insert_error:
            raise self.insert_error
        return FakeInsertResult()


class FakeDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection())


@pytest.fixture
def database() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture
def repository(database: FakeDatabase) -> AuthorizationRepository:
    return AuthorizationRepository(database)  # type: ignore[arg-type]


def identity() -> AuthorizationIdentity:
    return AuthorizationIdentity(
        tenant_id="tenant-1",
        principal_id="user-1",
        display_name="Test User",
        email="test@example.com",
        entra_roles=frozenset({"EvalHub.Admin"}),
    )


def actor(principal_id: str = "admin-1") -> ActorReference:
    return ActorReference(
        tenant_id="tenant-1",
        principal_id=principal_id,
        display_name="Admin",
    )


def principal_document(principal_id: str = "user-1") -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "tenant_id": "tenant-1",
        "principal_id": principal_id,
        "principal_type": "user",
        "display_name": "Test User",
        "email": "test@example.com",
        "entra_roles_last_seen": ["EvalHub.Admin"],
        "first_login_at": now,
        "last_login_at": now,
        "updated_at": now,
    }


def assignment_document(object_id: ObjectId | None = None) -> dict[str, Any]:
    return {
        "_id": object_id or ObjectId(),
        "tenant_id": "tenant-1",
        "principal_id": "user-1",
        "local_role": "platform_admin",
        "scope": {"type": "global", "id": "*"},
        "status": "active",
        "reason": "Needed for administration",
        "granted_by": actor().model_dump(mode="python"),
        "created_at": datetime.now(UTC),
    }


def access_request_document(object_id: ObjectId | None = None) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "_id": object_id or ObjectId(),
        "tenant_id": "tenant-1",
        "principal_id": "user-1",
        "display_name": "Test User",
        "email": "test@example.com",
        "requested_role": "EvalHub.Editor",
        "business_reason": "Needed for evaluation work",
        "status": "pending",
        "created_at": now,
        "updated_at": now,
    }


def test_serialize_handles_none_dict_and_invalid_normalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert repository_module._serialize(None) is None
    monkeypatch.setattr(repository_module, "normalize_document", lambda value: value)
    assert repository_module._serialize({"value": 1}) == {"value": 1}
    monkeypatch.setattr(repository_module, "normalize_document", lambda _value: [])
    with pytest.raises(TypeError, match="must return a dictionary"):
        repository_module._serialize({"value": 1})


@pytest.mark.asyncio
async def test_upsert_get_and_list_principals(
    repository: AuthorizationRepository,
    database: FakeDatabase,
) -> None:
    collection = database.collections[AuthorizationRepository.PRINCIPALS]
    collection.find_one_and_update_result = principal_document()
    result = await repository.upsert_principal(identity())
    assert result.principal_id == "user-1"
    update = collection.calls[-1][1][1]
    assert update["$set"]["entra_roles_last_seen"] == ["EvalHub.Admin"]

    collection.find_one_result = principal_document()
    assert await repository.get_principal("tenant-1", "user-1") is not None
    collection.find_one_result = None
    assert await repository.get_principal("tenant-1", "missing") is None

    collection.count_result = 1
    collection.find_results = [principal_document()]
    principals, total = await repository.list_principals(
        tenant_id="tenant-1",
        search="Test (Admin)",
        limit=10,
        offset=2,
    )
    assert total == 1
    assert principals[0].display_name == "Test User"
    query = next(call for call in collection.calls if call[0] == "count_documents")[1][0]
    assert query["$or"][0]["display_name"]["$regex"] == r"Test\ \(Admin\)"
    assert collection.last_cursor is not None
    assert collection.last_cursor.skip_value == 2
    assert collection.last_cursor.limit_value == 10

    await repository.list_principals(
        tenant_id="tenant-1", search=None, limit=5, offset=0
    )


@pytest.mark.asyncio
async def test_create_assignment_writes_assignment_and_audit(
    repository: AuthorizationRepository,
    database: FakeDatabase,
) -> None:
    payload = AssignmentCreate(
        tenant_id="tenant-1",
        principal_id="user-1",
        reason="Needed for administration",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    document = await repository.create_assignment(
        payload,
        actor(),
        actor("user-1"),
        request_id="request-1",
    )

    assert ObjectId.is_valid(document["_id"])
    assignments = database.collections[AuthorizationRepository.ASSIGNMENTS]
    assert assignments.calls[0][0] == "update_many"
    assert assignments.calls[1][0] == "insert_one"
    audit = database.collections[AuthorizationRepository.AUDIT_EVENTS]
    audit_document = audit.calls[-1][1][0]
    assert audit_document["event_type"] == "authorization.assignment.granted"
    assert audit_document["request_id"] == "request-1"


@pytest.mark.asyncio
async def test_create_assignment_translates_duplicate_key(
    repository: AuthorizationRepository,
    database: FakeDatabase,
) -> None:
    database.collections[AuthorizationRepository.ASSIGNMENTS].insert_error = (
        DuplicateKeyError("duplicate")
    )
    payload = AssignmentCreate(
        tenant_id="tenant-1",
        principal_id="user-1",
        reason="Needed for administration",
    )
    with pytest.raises(AssignmentAlreadyExistsError, match="already exists"):
        await repository.create_assignment(
            payload, actor(), actor("user-1"), request_id=None
        )


@pytest.mark.asyncio
async def test_revoke_and_get_assignment_success_and_not_found(
    repository: AuthorizationRepository,
    database: FakeDatabase,
) -> None:
    collection = database.collections[AuthorizationRepository.ASSIGNMENTS]
    object_id = ObjectId()
    collection.find_one_and_update_result = {
        **assignment_document(object_id),
        "status": "revoked",
    }
    revoked = await repository.revoke_assignment(
        str(object_id),
        "tenant-1",
        actor(),
        actor("user-1"),
        "No longer required",
        request_id="request-1",
    )
    assert revoked["_id"] == str(object_id)
    assert database.collections[AuthorizationRepository.AUDIT_EVENTS].calls[-1][1][0][
        "event_type"
    ] == "authorization.assignment.revoked"

    collection.find_one_and_update_result = None
    with pytest.raises(AssignmentNotFoundError, match="active assignment"):
        await repository.revoke_assignment(
            str(object_id),
            "tenant-1",
            actor(),
            actor("user-1"),
            "No longer required",
            request_id=None,
        )
    with pytest.raises(AssignmentNotFoundError, match="assignment not found"):
        await repository.revoke_assignment(
            "invalid",
            "tenant-1",
            actor(),
            actor("user-1"),
            "No longer required",
            request_id=None,
        )

    assert await repository.get_assignment("invalid", tenant_id="tenant-1") is None
    collection.find_one_result = assignment_document(object_id)
    found = await repository.get_assignment(str(object_id), tenant_id="tenant-1")
    assert found is not None and found["_id"] == str(object_id)


@pytest.mark.asyncio
async def test_list_assignments_and_audit_build_queries(
    repository: AuthorizationRepository,
    database: FakeDatabase,
) -> None:
    assignments = database.collections[AuthorizationRepository.ASSIGNMENTS]
    assignments.find_results = [assignment_document()]
    assignments.count_result = 1
    items, total = await repository.list_assignments(
        tenant_id="tenant-1",
        principal_id="user-1",
        active_only=True,
        limit=20,
        offset=3,
    )
    assert total == 1 and len(items) == 1
    query = assignments.calls[0][1][0]
    assert query["local_role"] == "platform_admin"
    assert query["principal_id"] == "user-1"
    assert query["status"].value == "active"
    await repository.list_assignments(tenant_id="tenant-1")

    audit = database.collections[AuthorizationRepository.AUDIT_EVENTS]
    audit.find_results = [
        {
            "_id": ObjectId(),
            "actor": {"tenant_id": "tenant-1"},
            "target": {"tenant_id": "tenant-1"},
        }
    ]
    audit.count_result = 1
    events, event_total = await repository.list_audit_events(
        tenant_id="tenant-1", limit=10, offset=0
    )
    assert event_total == 1 and len(events) == 1


@pytest.mark.asyncio
async def test_create_and_list_access_requests(
    repository: AuthorizationRepository,
    database: FakeDatabase,
) -> None:
    payload = AccessRequestCreate(
        requested_role="EvalHub.Editor",
        business_reason="Needed for evaluation work",
    )
    created = await repository.create_access_request(
        identity(), payload, request_id="request-1"
    )
    assert created["status"].value == "pending"
    audit_document = database.collections[AuthorizationRepository.AUDIT_EVENTS].calls[-1][1][0]
    assert audit_document["event_type"] == "authorization.access_request.created"

    requests = database.collections[AuthorizationRepository.ACCESS_REQUESTS]
    requests.find_results = [access_request_document()]
    requests.count_result = 1
    items, total = await repository.list_access_requests(
        tenant_id="tenant-1",
        principal_id="user-1",
        status=repository_module.AccessRequestStatus.PENDING,
        limit=10,
        offset=2,
    )
    assert total == 1 and len(items) == 1
    query = next(call for call in requests.calls if call[0] == "count_documents")[1][0]
    assert query["principal_id"] == "user-1"
    assert query["status"].value == "pending"
    await repository.list_access_requests(
        tenant_id="tenant-1", limit=10, offset=0
    )


@pytest.mark.asyncio
async def test_create_access_request_translates_duplicate_key(
    repository: AuthorizationRepository,
    database: FakeDatabase,
) -> None:
    database.collections[AuthorizationRepository.ACCESS_REQUESTS].insert_error = (
        DuplicateKeyError("duplicate")
    )
    with pytest.raises(AccessRequestConflictError, match="open request"):
        await repository.create_access_request(
            identity(),
            AccessRequestCreate(
                requested_role="EvalHub.Editor",
                business_reason="Needed for evaluation work",
            ),
            request_id=None,
        )


@pytest.mark.asyncio
async def test_cancel_access_request_success_and_failure_paths(
    repository: AuthorizationRepository,
    database: FakeDatabase,
) -> None:
    requests = database.collections[AuthorizationRepository.ACCESS_REQUESTS]
    object_id = ObjectId()
    requests.find_one_and_update_result = {
        **access_request_document(object_id),
        "status": "cancelled",
    }
    cancelled = await repository.cancel_access_request(
        str(object_id), identity(), request_id="request-1"
    )
    assert cancelled["status"] == "cancelled"
    audit_document = database.collections[AuthorizationRepository.AUDIT_EVENTS].calls[-1][1][0]
    assert audit_document["event_type"] == "authorization.access_request.cancelled"

    requests.find_one_and_update_result = None
    with pytest.raises(AccessRequestNotFoundError, match="open access request"):
        await repository.cancel_access_request(
            str(object_id), identity(), request_id=None
        )
    with pytest.raises(AccessRequestNotFoundError, match="access request not found"):
        await repository.cancel_access_request("invalid", identity(), request_id=None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "expected_status", "fulfilled"),
    [
        (AccessRequestAction.APPROVE, "approved", False),
        (AccessRequestAction.FULFILL, "fulfilled", True),
        (AccessRequestAction.REJECT, "rejected", False),
    ],
)
async def test_decide_access_request_transitions_and_audits(
    repository: AuthorizationRepository,
    database: FakeDatabase,
    action: AccessRequestAction,
    expected_status: str,
    fulfilled: bool,
) -> None:
    requests = database.collections[AuthorizationRepository.ACCESS_REQUESTS]
    object_id = ObjectId()
    requests.find_one_and_update_result = {
        **access_request_document(object_id),
        "status": expected_status,
    }
    result = await repository.decide_access_request(
        str(object_id),
        "tenant-1",
        actor(),
        action,
        "Decision made for project work",
        request_id="request-1",
    )
    assert result["status"] == expected_status
    updates = requests.calls[-1][1][1]["$set"]
    assert ("fulfilled_at" in updates) is fulfilled
    audit_document = database.collections[AuthorizationRepository.AUDIT_EVENTS].calls[-1][1][0]
    assert audit_document["event_type"].endswith(expected_status)


@pytest.mark.asyncio
async def test_decide_access_request_invalid_id_or_transition(
    repository: AuthorizationRepository,
    database: FakeDatabase,
) -> None:
    with pytest.raises(AccessRequestNotFoundError, match="access request not found"):
        await repository.decide_access_request(
            "invalid",
            "tenant-1",
            actor(),
            AccessRequestAction.APPROVE,
            "Approved for project work",
            request_id=None,
        )

    database.collections[
        AuthorizationRepository.ACCESS_REQUESTS
    ].find_one_and_update_result = None
    with pytest.raises(AccessRequestNotFoundError, match="transition"):
        await repository.decide_access_request(
            str(ObjectId()),
            "tenant-1",
            actor(),
            AccessRequestAction.APPROVE,
            "Approved for project work",
            request_id=None,
        )
