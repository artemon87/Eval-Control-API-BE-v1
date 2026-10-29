"""Suggestions repository tests: original listing/voting coverage plus new attachments/status audit.

Requires pytest-asyncio with asyncio_mode=auto (no per-test asyncio decorators).
"""
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from src.models.suggestions import SuggestionStatus
from src.repositories.suggestions import SuggestionRepository


class _FindCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs
        self.sort_spec: Any = None
        self.limit_value: int | None = None

    def sort(self, spec: Any) -> "_FindCursor":
        self.sort_spec = spec
        return self

    def limit(self, value: int) -> "_FindCursor":
        self.limit_value = value
        return self

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        effective = length if length is not None else self.limit_value
        return self._docs[:effective] if effective is not None else list(self._docs)


class _SuggestionsCollectionSpy:
    def __init__(
        self,
        docs: list[dict[str, Any]] | None = None,
        find_one_result: dict[str, Any] | None = None,
    ) -> None:
        self._docs = docs or []
        self.find_one_result = find_one_result
        self.last_find_query: dict[str, Any] | None = None
        self.last_cursor: _FindCursor | None = None
        self.last_find_one_query: dict[str, Any] | None = None
        self.find_one_calls: list[tuple[dict[str, Any], Any]] = []
        self.inserted_documents: list[dict[str, Any]] = []
        self.update_calls: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.create_index_calls: list[tuple[Any, dict[str, Any]]] = []
        self.find_one_and_update_result: dict[str, Any] | None = None
        self.find_one_and_update_calls: list[tuple[dict[str, Any], dict[str, Any], Any]] = []
        self.force_update_conflict = False

    def find(self, query: dict[str, Any]) -> _FindCursor:
        self.last_find_query = query
        self.last_cursor = _FindCursor(self._docs)
        return self.last_cursor

    async def find_one(
        self, query: dict[str, Any], *args: Any, **kwargs: Any
    ) -> dict[str, Any] | None:
        self.last_find_one_query = query
        self.find_one_calls.append((query, args[0] if args else kwargs.get("projection")))
        if "attachments.id" in query:
            source = self.find_one_result or next(
                (d for d in self._docs if d.get("_id") == query.get("_id")), None
            )
            if source is None:
                return None
            matches = [a for a in source.get("attachments", []) if a.get("id") == query["attachments.id"]]
            return {"attachments": matches} if matches else None
        return self.find_one_result

    async def insert_one(self, document: dict[str, Any]) -> SimpleNamespace:
        self.inserted_documents.append(document)
        return SimpleNamespace(inserted_id=document["_id"])

    async def update_one(
        self, query: dict[str, Any], update: dict[str, Any]
    ) -> SimpleNamespace:
        self.update_calls.append((query, update))
        return SimpleNamespace(matched_count=1, modified_count=1)

    async def find_one_and_update(
        self, query: dict[str, Any], update: dict[str, Any], *, return_document: Any
    ) -> dict[str, Any] | None:
        self.find_one_and_update_calls.append((query, update, return_document))
        if self.force_update_conflict:
            return None
        if self.find_one_and_update_result is not None:
            return self.find_one_and_update_result
        # Simulate MongoDB's return_document=AFTER and status/audit persistence.
        original = self.find_one_result
        if original is None:
            return None
        updated = {**original, **update.get("$set", {})}
        updated["status_history"] = [*original.get("status_history", []), update["$push"]["status_history"]]
        return updated

    async def create_index(self, keys: Any, **kwargs: Any) -> str:
        self.create_index_calls.append((keys, kwargs))
        return "index-name"


class _VotesCollectionSpy:
    def __init__(
        self,
        docs: list[dict[str, Any]] | None = None,
        *,
        raise_duplicate: bool = False,
        deleted_count: int = 1,
    ) -> None:
        self._docs = docs or []
        self.raise_duplicate = raise_duplicate
        self.deleted_count = deleted_count
        self.last_find_query: dict[str, Any] | None = None
        self.inserted_documents: list[dict[str, Any]] = []
        self.last_delete_query: dict[str, Any] | None = None
        self.create_index_calls: list[tuple[Any, dict[str, Any]]] = []
        self.find_one_result: dict[str, Any] | None = None

    def find(self, query: dict[str, Any]) -> _FindCursor:
        self.last_find_query = query
        return _FindCursor(self._docs)

    async def find_one(self, query: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any] | None:
        self.last_find_query = query
        if self.find_one_result is not None:
            return self.find_one_result
        return next((d for d in self._docs if all(d.get(k) == v for k, v in query.items())), None)

    async def insert_one(self, document: dict[str, Any]) -> SimpleNamespace:
        if self.raise_duplicate:
            raise DuplicateKeyError("duplicate vote")
        self.inserted_documents.append(document)
        return SimpleNamespace(inserted_id=ObjectId())

    async def delete_one(self, query: dict[str, Any]) -> SimpleNamespace:
        self.last_delete_query = query
        return SimpleNamespace(deleted_count=self.deleted_count)

    async def create_index(self, keys: Any, **kwargs: Any) -> str:
        self.create_index_calls.append((keys, kwargs))
        return "index-name"


def _new_repo(
    suggestion_docs: list[dict[str, Any]] | None = None,
    vote_docs: list[dict[str, Any]] | None = None,
    *,
    find_one_result: dict[str, Any] | None = None,
    raise_duplicate: bool = False,
    deleted_count: int = 1,
) -> tuple[SuggestionRepository, _SuggestionsCollectionSpy, _VotesCollectionSpy]:
    repo = SuggestionRepository.__new__(SuggestionRepository)
    suggestions_spy = _SuggestionsCollectionSpy(suggestion_docs, find_one_result=find_one_result)
    votes_spy = _VotesCollectionSpy(vote_docs, raise_duplicate=raise_duplicate, deleted_count=deleted_count)
    repo.suggestions = SimpleNamespace(collection=suggestions_spy)  # type: ignore[assignment]
    repo.votes = SimpleNamespace(collection=votes_spy)  # type: ignore[assignment]
    return repo, suggestions_spy, votes_spy


def _suggestion_doc(object_id: ObjectId | None = None, **overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "_id": object_id or ObjectId(),
        "title": "Add dark mode",
        "description": "Please add a dark mode option.",
        "author": "user:1",
        "status": "open",
        "vote_count": 2,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    document.update(overrides)
    return document


# --- list: preserve pre-existing behavior ---
async def test_list_returns_empty_when_no_documents() -> None:
    repo, _, _ = _new_repo(suggestion_docs=[])
    assert await repo.list(viewer="user:1", limit=10) == []


@pytest.mark.parametrize("limit,expected", [(500, 100), (10, 10)])
async def test_list_respects_limit_cap(limit: int, expected: int) -> None:
    repo, suggestions, _ = _new_repo(suggestion_docs=[])
    await repo.list(viewer="user:1", limit=limit)
    assert suggestions.last_cursor is not None
    assert suggestions.last_cursor.limit_value == expected


async def test_list_sorts_by_vote_count_and_created_at_descending() -> None:
    repo, suggestions, _ = _new_repo(suggestion_docs=[])
    await repo.list(viewer="user:1", limit=10)
    assert suggestions.last_cursor is not None
    assert suggestions.last_cursor.sort_spec == [("vote_count", -1), ("created_at", -1)]


@pytest.mark.parametrize("voted", [False, True])
async def test_list_sets_viewer_vote_state(voted: bool) -> None:
    identifier = ObjectId()
    votes = [{"suggestion_id": identifier, "user_id": "user:1"}] if voted else []
    repo, _, vote_collection = _new_repo([_suggestion_doc(identifier)], votes)
    result = await repo.list(viewer="user:1", limit=10)
    assert result[0]["viewer_has_voted"] is voted
    assert vote_collection.last_find_query == {"suggestion_id": {"$in": [identifier]}, "user_id": "user:1"}


async def test_list_anonymous_skips_vote_lookup() -> None:
    repo, _, votes = _new_repo([_suggestion_doc()])
    result = await repo.list(viewer=None, limit=10)
    assert result[0]["viewer_has_voted"] is False
    assert votes.last_find_query is None


async def test_list_normalizes_object_id_to_string() -> None:
    repo, _, _ = _new_repo([_suggestion_doc()])
    result = await repo.list(viewer="user:1", limit=10)
    assert isinstance(result[0]["id"], str)


# --- create ---
async def test_create_strips_whitespace_and_sets_defaults() -> None:
    repo, suggestions, _ = _new_repo()
    result = await repo.create(title="  Add dark mode  ", description="  Please add it.  ", author="user:1")
    inserted = suggestions.inserted_documents[0]
    assert inserted["title"] == "Add dark mode"
    assert inserted["description"] == "Please add it."
    assert inserted["status"] == "open"
    assert inserted["vote_count"] == 0
    assert inserted["author"] == "user:1"
    assert inserted["attachments"] == []
    assert isinstance(result["id"], str)
    assert result["viewer_has_voted"] is False


async def test_create_persists_attachment_metadata_and_explicit_id() -> None:
    repo, suggestions, _ = _new_repo()
    identifier = ObjectId()
    attachment = {"id": "file-1", "filename": "image.png", "content_type": "image/png", "size_bytes": 42, "object_name": "private/image.png"}
    result = await repo.create(title="Title", description="Description", author="Alex", attachments=[attachment], suggestion_id=identifier)
    assert suggestions.inserted_documents[0]["_id"] == identifier
    assert suggestions.inserted_documents[0]["attachments"] == [attachment]
    assert result["attachments"] == [{"id": "file-1", "filename": "image.png", "content_type": "image/png", "size_bytes": 42}]


# --- voting: preserve pre-existing behavior ---
@pytest.mark.parametrize("method", ["add_vote", "remove_vote"])
async def test_vote_raises_lookup_error_when_suggestion_missing(method: str) -> None:
    repo, _, _ = _new_repo(find_one_result=None)
    with pytest.raises(LookupError):
        await getattr(repo, method)(suggestion_id=str(ObjectId()), user_id="user:1")


@pytest.mark.parametrize("method", ["add_vote", "remove_vote"])
async def test_vote_rejects_invalid_suggestion_id(method: str) -> None:
    repo, _, _ = _new_repo(find_one_result={"_id": ObjectId()})
    with pytest.raises(LookupError):
        await getattr(repo, method)(suggestion_id="not-a-valid-object-id", user_id="user:1")


async def test_add_vote_increments_count_on_success() -> None:
    identifier = ObjectId()
    repo, suggestions, votes = _new_repo(find_one_result={"_id": identifier})
    assert await repo.add_vote(suggestion_id=str(identifier), user_id="user:1") is True
    assert votes.inserted_documents[0]["suggestion_id"] == identifier
    assert votes.inserted_documents[0]["user_id"] == "user:1"
    assert suggestions.update_calls[0][1]["$inc"] == {"vote_count": 1}


async def test_add_vote_does_not_increment_on_duplicate() -> None:
    identifier = ObjectId()
    repo, suggestions, _ = _new_repo(find_one_result={"_id": identifier}, raise_duplicate=True)
    assert await repo.add_vote(suggestion_id=str(identifier), user_id="user:1") is False
    assert suggestions.update_calls == []


@pytest.mark.parametrize("deleted,expected", [(1, True), (0, False)])
async def test_remove_vote_updates_only_when_deleted(deleted: int, expected: bool) -> None:
    identifier = ObjectId()
    repo, suggestions, votes = _new_repo(find_one_result={"_id": identifier}, deleted_count=deleted)
    assert await repo.remove_vote(suggestion_id=str(identifier), user_id="user:1") is expected
    assert votes.last_delete_query == {"suggestion_id": identifier, "user_id": "user:1"}
    if expected:
        assert suggestions.update_calls[0][1]["$inc"] == {"vote_count": -1}
    else:
        assert suggestions.update_calls == []


# --- ids and serialization ---
def test_object_id_accepts_valid_and_rejects_invalid_strings() -> None:
    identifier = ObjectId()
    assert SuggestionRepository._object_id(str(identifier)) == identifier
    with pytest.raises(LookupError):
        SuggestionRepository._object_id("not-valid")


def test_serialize_defaults_legacy_fields_and_hides_storage_paths() -> None:
    legacy = _suggestion_doc()
    result = SuggestionRepository._serialize(legacy, viewer_has_voted=True)
    assert result["status"] == "open"
    assert result["vote_count"] == 2
    assert result["viewer_has_voted"] is True
    assert result["attachments"] == []
    assert result["status_updated_at"] is None
    assert result["status_updated_by_id"] is None
    assert result["status_updated_by_name"] is None
    attachment = {"id": "a", "filename": "f.png", "content_type": "image/png", "size_bytes": 12, "object_name": "private/f.png"}
    result = SuggestionRepository._serialize(_suggestion_doc(attachments=[attachment]), viewer_has_voted=False)
    assert result["attachments"] == [{"id": "a", "filename": "f.png", "content_type": "image/png", "size_bytes": 12}]


# --- embedded attachment lookup ---
async def test_get_attachment_returns_exact_matching_metadata() -> None:
    identifier = ObjectId()
    attachment = {"id": "att-1", "filename": "photo.png", "content_type": "image/png", "size_bytes": 111, "object_name": "private/photo.png"}
    repo, suggestions, _ = _new_repo(find_one_result=_suggestion_doc(identifier, attachments=[attachment]))
    assert await repo.get_attachment(suggestion_id=str(identifier), attachment_id="att-1") == attachment
    assert suggestions.last_find_one_query == {"_id": identifier, "attachments.id": "att-1"}


async def test_get_attachment_missing_raises_lookup_error() -> None:
    repo, _, _ = _new_repo()
    with pytest.raises(LookupError):
        await repo.get_attachment(suggestion_id=str(ObjectId()), attachment_id="missing")


# --- status transitions ---
def _different_status() -> SuggestionStatus:
    return "planned"


async def test_update_status_writes_audit_and_history() -> None:
    original = _suggestion_doc()
    repo, suggestions, _ = _new_repo(find_one_result=original)
    changed = _different_status()
    result = await repo.update_status(suggestion_id=str(original["_id"]), status=changed, viewer="user:123", updated_by_name="Alex Smith")
    query, update, return_document = suggestions.find_one_and_update_calls[0]
    assert query == {"_id": original["_id"], "status": "open"}
    assert return_document == ReturnDocument.AFTER
    assert update["$set"]["status"] == changed
    assert update["$set"]["status_updated_by_id"] == "user:123"
    assert update["$set"]["status_updated_by_name"] == "Alex Smith"
    assert isinstance(update["$set"]["status_updated_at"], datetime)
    assert update["$set"]["updated_at"] == update["$set"]["status_updated_at"]
    history = update["$push"]["status_history"]
    assert history["from_status"] == "open"
    assert history["to_status"] == changed
    assert history["changed_by_id"] == "user:123"
    assert history["changed_by_name"] == "Alex Smith"
    assert history["changed_at"] == update["$set"]["status_updated_at"]
    assert result["status_updated_by_name"] == "Alex Smith"


async def test_same_status_does_not_change_audit_or_history() -> None:
    original = _suggestion_doc()
    repo, suggestions, votes = _new_repo(find_one_result=original)
    votes.find_one_result = {"_id": ObjectId()}
    result = await repo.update_status(suggestion_id=str(original["_id"]), status="open", viewer="user:123", updated_by_name="Alex")
    assert suggestions.find_one_and_update_calls == []
    assert result["viewer_has_voted"] is True
    assert result["status_updated_at"] is None


async def test_update_status_missing_raises_lookup_error() -> None:
    repo, _, _ = _new_repo()
    with pytest.raises(LookupError):
        await repo.update_status(suggestion_id=str(ObjectId()), status=_different_status(), viewer="user:123", updated_by_name="Alex")


async def test_update_status_conflict_raises_runtime_error() -> None:
    original = _suggestion_doc()
    repo, suggestions, _ = _new_repo(find_one_result=original)
    suggestions.force_update_conflict = True
    with pytest.raises(RuntimeError, match="changed by another request"):
        await repo.update_status(suggestion_id=str(original["_id"]), status=_different_status(), viewer="user:123", updated_by_name="Alex")
