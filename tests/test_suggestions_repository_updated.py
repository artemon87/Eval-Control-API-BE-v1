"""Focused regression tests for attachment storage and status-history writes.

These are ADDITIONAL tests; retain existing repository voting, listing, and index tests.
"""

import datetime as dt
from types import SimpleNamespace
from typing import Any

import pytest
from bson import ObjectId
from pymongo import ReturnDocument

from src.models.suggestions import SuggestionStatus
from src.repositories.suggestions import SuggestionNotFoundError, SuggestionRepository


class CollectionSpy:
    def __init__(self, document: dict[str, Any] | None = None) -> None:
        self.document = document
        self.inserted: dict[str, Any] | None = None
        self.find_queries: list[tuple[dict[str, Any], Any]] = []
        self.updated: tuple[dict[str, Any], dict[str, Any], Any] | None = None
        self.next_update_result: dict[str, Any] | None = document

    async def insert_one(self, document: dict[str, Any]) -> SimpleNamespace:
        self.inserted = document.copy()
        return SimpleNamespace(inserted_id=document["_id"])

    async def find_one(self, query: dict[str, Any], projection: Any = None) -> dict[str, Any] | None:
        self.find_queries.append((query, projection))
        if self.document is None:
            return None
        if "attachments.id" in query:
            matches = [a for a in self.document.get("attachments", []) if a["id"] == query["attachments.id"]]
            return {"attachments": matches} if matches else None
        return self.document

    async def find_one_and_update(
        self, query: dict[str, Any], update: dict[str, Any], *, return_document: Any
    ) -> dict[str, Any] | None:
        self.updated = (query, update, return_document)
        return self.next_update_result


def repository_with_spies(
    document: dict[str, Any] | None = None,
) -> tuple[SuggestionRepository, CollectionSpy, CollectionSpy]:
    repo = object.__new__(SuggestionRepository)
    suggestions = CollectionSpy(document)
    votes = CollectionSpy()
    repo.suggestions = SimpleNamespace(collection=suggestions)
    repo.votes = SimpleNamespace(collection=votes)
    return repo, suggestions, votes


def raw_suggestion(**changes: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "_id": ObjectId(), "title": "Add dark mode",
        "description": "Please add a dark mode option.", "author": "Alex Smith",
        "status": "open", "vote_count": 1,
        "created_at": dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
    }
    document.update(changes)
    return document


def next_status() -> SuggestionStatus:
    for value in SuggestionStatus:
        if str(value) != "open" and value.value != "open":
            return value
    pytest.skip("No second valid suggestion status exists")


@pytest.mark.asyncio
async def test_create_persists_attachment_metadata_and_explicit_id() -> None:
    repo, collection, _ = repository_with_spies()
    identifier = ObjectId()
    attachment = {
        "id": "file-1", "filename": "image.png", "content_type": "image/png",
        "size_bytes": 42, "object_name": "suggestions/abc/file-1/image.png",
    }
    result = await repo.create(
        title="  Add dark mode  ", description="  Add a toggle  ",
        author="Alex Smith", attachments=[attachment], suggestion_id=identifier,
    )
    assert collection.inserted is not None
    assert collection.inserted["_id"] == identifier
    assert collection.inserted["title"] == "Add dark mode"
    assert collection.inserted["description"] == "Add a toggle"
    assert collection.inserted["status"] == "open"
    assert collection.inserted["vote_count"] == 0
    assert collection.inserted["attachments"] == [attachment]
    assert result["attachments"] == [{
        "id": "file-1", "filename": "image.png", "content_type": "image/png",
        "size_bytes": 42,
    }]
    assert "object_name" not in result["attachments"][0]


@pytest.mark.asyncio
async def test_create_without_images_persists_empty_list() -> None:
    repo, collection, _ = repository_with_spies()
    result = await repo.create(
        title="Add dark mode", description="A toggle in settings", author="Alex"
    )
    assert collection.inserted is not None
    assert collection.inserted["attachments"] == []
    assert result["attachments"] == []


@pytest.mark.asyncio
async def test_get_attachment_looks_up_only_requested_embedded_image() -> None:
    identifier = ObjectId()
    attachment = {
        "id": "att-1", "filename": "photo.png", "content_type": "image/png",
        "size_bytes": 111, "object_name": "suggestions/x/att-1/photo.png",
    }
    repo, collection, _ = repository_with_spies(
        raw_suggestion(_id=identifier, attachments=[attachment])
    )
    result = await repo.get_attachment(suggestion_id=str(identifier), attachment_id="att-1")
    assert result == attachment
    assert collection.find_queries[-1][0] == {
        "_id": identifier, "attachments.id": "att-1",
    }


@pytest.mark.asyncio
async def test_get_attachment_missing_raises_lookup_error() -> None:
    repo, _, _ = repository_with_spies()
    with pytest.raises(SuggestionNotFoundError):
        await repo.get_attachment(suggestion_id=str(ObjectId()), attachment_id="missing")


@pytest.mark.asyncio
async def test_update_status_writes_history_and_audit_metadata() -> None:
    original = raw_suggestion(attachments=[])
    repo, collection, votes = repository_with_spies(original)
    votes.document = None
    new_status = next_status()
    result = await repo.update_status(
        suggestion_id=str(original["_id"]), status=new_status,
        viewer="user:123", updated_by_name="Alex Smith",
    )
    assert collection.updated is not None
    query, update, return_document = collection.updated
    assert query == {"_id": original["_id"], "status": "open"}
    assert return_document == ReturnDocument.AFTER
    assert update["$set"]["status"] == new_status
    assert update["$set"]["status_updated_by_id"] == "user:123"
    assert update["$set"]["status_updated_by_name"] == "Alex Smith"
    assert isinstance(update["$set"]["status_updated_at"], dt.datetime)
    assert update["$set"]["updated_at"] == update["$set"]["status_updated_at"]
    history = update["$push"]["status_history"]
    assert history["from_status"] == "open"
    assert history["to_status"] == new_status
    assert history["changed_by_id"] == "user:123"
    assert history["changed_by_name"] == "Alex Smith"
    assert history["changed_at"] == update["$set"]["status_updated_at"]
    assert result["id"] == str(original["_id"])


@pytest.mark.asyncio
async def test_same_status_does_not_write_audit_or_history() -> None:
    original = raw_suggestion(status="open")
    repo, collection, votes = repository_with_spies(original)
    votes.document = {"_id": ObjectId()}
    result = await repo.update_status(
        suggestion_id=str(original["_id"]), status=SuggestionStatus("open"),
        viewer="user:123", updated_by_name="Alex Smith",
    )
    assert collection.updated is None
    assert result["viewer_has_voted"] is True
    assert result["status_updated_at"] is None


@pytest.mark.asyncio
async def test_update_status_missing_suggestion_raises_lookup_error() -> None:
    repo, _, _ = repository_with_spies()
    with pytest.raises(SuggestionNotFoundError):
        await repo.update_status(
            suggestion_id=str(ObjectId()), status=next_status(),
            viewer="user:123", updated_by_name="Alex Smith",
        )


@pytest.mark.asyncio
async def test_update_status_conflict_raises_runtime_error() -> None:
    original = raw_suggestion()
    repo, collection, _ = repository_with_spies(original)
    collection.next_update_result = None
    with pytest.raises(RuntimeError, match="changed by another request"):
        await repo.update_status(
            suggestion_id=str(original["_id"]), status=next_status(),
            viewer="user:123", updated_by_name="Alex Smith",
        )


def test_serialize_legacy_document_defaults_new_fields() -> None:
    result = SuggestionRepository._serialize(raw_suggestion(), viewer_has_voted=False)
    assert result["attachments"] == []
    assert result["status_updated_at"] is None
    assert result["status_updated_by_id"] is None
    assert result["status_updated_by_name"] is None


def test_serialize_hides_gcs_object_name() -> None:
    attachment = {
        "id": "one", "filename": "foo.png", "content_type": "image/png",
        "size_bytes": 12, "object_name": "private/path/foo.png",
    }
    result = SuggestionRepository._serialize(
        raw_suggestion(attachments=[attachment]), viewer_has_voted=True,
    )
    assert result["attachments"][0] == {
        "id": "one", "filename": "foo.png", "content_type": "image/png",
        "size_bytes": 12,
    }
    assert result["viewer_has_voted"] is True
