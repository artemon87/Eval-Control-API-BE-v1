"""Regression tests for the new Suggestions route behavior.

Keep legacy voting/listing tests, but remove tests for the removed current_user
helper if it is no longer exported by src.api.api_v1.routes.suggestions.
"""

import datetime as dt
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.responses import StreamingResponse

from src.api.api_v1.routes.suggestions import (
    add_vote,
    create_suggestion,
    list_suggestions,
    remove_vote,
    get_suggestion_attachment,
    update_suggestion_status,
)
from src.models.suggestions import SuggestionListResponse, SuggestionStatusUpdate
from src.security.permissions import Permission


def document(**changes: Any) -> dict[str, Any]:
    payload = {
        "id": "abc123", "title": "Add dark mode",
        "description": "A dark mode option for all pages",
        "author": "Alex Smith", "status": "open", "vote_count": 0,
        "created_at": dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        "viewer_has_voted": False, "attachments": [],
    }
    payload.update(changes)
    return payload


def user(**changes: Any) -> SimpleNamespace:
    fields = {
        "object_id": "123", "subject": "subject-123", "email": "alex@example.com",
        "display_name": "Alex Smith", "name": None,
    }
    fields.update(changes)
    return SimpleNamespace(**fields)


def image_store() -> SimpleNamespace:
    return SimpleNamespace(
        upload_many=AsyncMock(return_value=[]),
        delete_many=AsyncMock(),
        iter_download=lambda name: iter([b"hello", b"world"]),
    )


def repository(**changes: Any) -> SimpleNamespace:
    fields = {
        "create": AsyncMock(return_value=document()),
        "get_attachment": AsyncMock(return_value={
            "id": "att1", "filename": "picture.png", "content_type": "image/png",
            "size_bytes": 10, "object_name": "private/object.png",
        }),
        "update_status": AsyncMock(return_value=document()),
    }
    fields.update(changes)
    return SimpleNamespace(**fields)


async def test_create_without_images_uses_authenticated_display_name() -> None:
    repo = repository()
    store = image_store()
    result = await create_suggestion(
        user=user(), viewer="user:123", repository=repo, image_store=store,
        title="Add dark mode", description="A dark mode option for all pages",
        images=None,
    )
    assert result.id == "abc123"
    store.upload_many.assert_awaited_once()
    assert store.upload_many.await_args.kwargs["files"] == []
    repo.create.assert_awaited_once_with(
        title="Add dark mode", description="A dark mode option for all pages",
        author="Alex Smith", attachments=[],
    )
    store.delete_many.assert_not_awaited()


async def test_create_falls_back_to_email() -> None:
    repo = repository()
    await create_suggestion(
        user=user(display_name=None), viewer="user:123", repository=repo,
        image_store=image_store(), title="Add dark mode",
        description="A dark mode option for all pages", images=None,
    )
    assert repo.create.await_args.kwargs["author"] == "alex@example.com"


async def test_create_passes_uploaded_attachment_metadata_to_repository() -> None:
    attachments = [{
        "id": "att1", "filename": "image.png", "content_type": "image/png",
        "size_bytes": 10, "object_name": "private/image.png",
    }]
    repo = repository()
    store = image_store()
    store.upload_many.return_value = attachments
    image = object()  # upload_many is mocked; no actual UploadFile needed.
    await create_suggestion(
        user=user(), viewer="user:123", repository=repo,
        image_store=store, title="Add dark mode",
        description="A dark mode option for all pages", images=[image],
    )
    assert repo.create.await_args.kwargs["attachments"] == attachments
    assert store.upload_many.await_args.kwargs["files"] == [image]


async def test_create_rolls_back_uploaded_images_if_repository_fails() -> None:
    images = [{"id": "image", "object_name": "private/image.png"}]
    repo = repository(create=AsyncMock(side_effect=RuntimeError("mongo failure")))
    store = image_store()
    store.upload_many.return_value = images
    with pytest.raises(RuntimeError, match="mongo failure"):
        await create_suggestion(
            user=user(), viewer="user:123", repository=repo,
            image_store=store, title="Add dark mode",
            description="A dark mode option for all pages", images=None,
        )
    store.delete_many.assert_awaited_once_with(images)


async def test_get_attachment_returns_private_stream_with_safe_headers() -> None:
    repo = repository()
    store = image_store()
    result = await get_suggestion_attachment(
        suggestion_id="abc123", attachment_id="att1", viewer="user:123",
        repository=repo, image_store=store,
    )
    assert isinstance(result, StreamingResponse)
    assert result.media_type == "image/png"
    assert result.headers["cache-control"] == "private, max-age=300"
    assert result.headers["x-content-type-options"] == "nosniff"
    assert "inline;" in result.headers["content-disposition"]
    repo.get_attachment.assert_awaited_once_with(
        suggestion_id="abc123", attachment_id="att1",
    )
    assert b"".join(result.body_iterator) == b"helloworld"


async def test_get_attachment_missing_returns_404() -> None:
    repo = repository(get_attachment=AsyncMock(side_effect=LookupError("not found")))
    with pytest.raises(HTTPException) as error:
        await get_suggestion_attachment(
            suggestion_id="abc123", attachment_id="missing", viewer="user:123",
            repository=repo, image_store=image_store(),
        )
    assert error.value.status_code == 404


def authorization_service(permissions: set[Permission]) -> SimpleNamespace:
    return SimpleNamespace(resolve=AsyncMock(return_value=SimpleNamespace(permissions=permissions)))


async def test_update_status_denies_user_without_manage_permission() -> None:
    repo = repository()
    service = authorization_service(set())
    with pytest.raises(HTTPException) as error:
        await update_suggestion_status(
            suggestion_id="abc123", payload=SuggestionStatusUpdate(status="open"),
            current_user=user(), viewer="user:123", repository=repo,
            authorization_service=service,
        )
    assert error.value.status_code == 403
    repo.update_status.assert_not_awaited()
    service.resolve.assert_awaited_once()


async def test_update_status_uses_authenticated_name_and_viewer() -> None:
    repo = repository()
    service = authorization_service({Permission.SUGGESTION_MANAGE})
    result = await update_suggestion_status(
        suggestion_id="abc123", payload=SuggestionStatusUpdate(status="open"),
        current_user=user(name="Alex Full Name"), viewer="user:123",
        repository=repo, authorization_service=service,
    )
    assert result.id == "abc123"
    repo.update_status.assert_awaited_once_with(
        suggestion_id="abc123", status="open", viewer="user:123",
        updated_by_name="Alex Full Name",
    )


async def test_update_status_missing_suggestion_returns_404() -> None:
    repo = repository(update_status=AsyncMock(side_effect=LookupError("missing")))
    service = authorization_service({Permission.SUGGESTION_MANAGE})
    with pytest.raises(HTTPException) as error:
        await update_suggestion_status(
            suggestion_id="missing", payload=SuggestionStatusUpdate(status="open"),
            current_user=user(), viewer="user:123", repository=repo,
            authorization_service=service,
        )
    assert error.value.status_code == 404


async def test_update_status_falls_back_to_email() -> None:
    repo = repository()
    await update_suggestion_status(
        suggestion_id="abc123", payload=SuggestionStatusUpdate(status="open"),
        current_user=user(name=None, display_name=None), viewer="user:123",
        repository=repo,
        authorization_service=authorization_service({Permission.SUGGESTION_MANAGE}),
    )
    assert repo.update_status.await_args.kwargs["updated_by_name"] == "alex@example.com"


# ---------------------------------------------------------------------------
# Existing listing/voting coverage, adapted to the updated route module.
# The old current_user() helper tests are intentionally removed: identity is
# now resolved by shared authentication dependencies rather than this route.
# ---------------------------------------------------------------------------

class _ListRepository:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items
        self.captured_kwargs: dict[str, Any] = {}

    async def list(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.captured_kwargs = kwargs
        return self.items


async def test_list_suggestions_returns_items_from_repository() -> None:
    repo = _ListRepository([document()])
    result = await list_suggestions("user:1", repo, limit=100)
    assert isinstance(result, SuggestionListResponse)
    assert len(result.items) == 1
    assert result.items[0].id == "abc123"


async def test_list_suggestions_returns_empty_list_when_no_suggestions() -> None:
    repo = _ListRepository([])
    result = await list_suggestions("user:1", repo, limit=100)
    assert result.items == []


async def test_list_suggestions_passes_viewer_and_limit_to_repository() -> None:
    repo = _ListRepository([])
    await list_suggestions("user:1", repo, limit=25)
    assert repo.captured_kwargs == {"viewer": "user:1", "limit": 25}


async def test_list_suggestions_preserves_vote_information() -> None:
    repo = _ListRepository([document(vote_count=4, viewer_has_voted=True)])
    result = await list_suggestions("user:1", repo, limit=100)
    assert result.items[0].vote_count == 4
    assert result.items[0].viewer_has_voted is True


async def test_list_suggestions_preserves_repository_order() -> None:
    repo = _ListRepository([document(id="first"), document(id="second")])
    result = await list_suggestions("user:1", repo, limit=100)
    assert [suggestion.id for suggestion in result.items] == ["first", "second"]


class _VoteRepository:
    def __init__(self, *, raise_lookup_error: bool = False) -> None:
        error = LookupError("Suggestion not found")
        side_effect = error if raise_lookup_error else None
        self.add_vote = AsyncMock(side_effect=side_effect)
        self.remove_vote = AsyncMock(side_effect=side_effect)


async def test_add_vote_passes_suggestion_id_and_viewer() -> None:
    repo = _VoteRepository()
    await add_vote("suggestion-1", "user:1", repo)
    repo.add_vote.assert_awaited_once_with(suggestion_id="suggestion-1", user_id="user:1")


async def test_add_vote_returns_404_when_suggestion_missing() -> None:
    repo = _VoteRepository(raise_lookup_error=True)
    with pytest.raises(HTTPException) as exc_info:
        await add_vote("missing", "user:1", repo)
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Suggestion not found"


async def test_remove_vote_passes_suggestion_id_and_viewer() -> None:
    repo = _VoteRepository()
    await remove_vote("suggestion-1", "user:1", repo)
    repo.remove_vote.assert_awaited_once_with(suggestion_id="suggestion-1", user_id="user:1")


async def test_remove_vote_returns_404_when_suggestion_missing() -> None:
    repo = _VoteRepository(raise_lookup_error=True)
    with pytest.raises(HTTPException) as exc_info:
        await remove_vote("missing", "user:1", repo)
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Suggestion not found"


async def test_create_falls_back_to_viewer_identity_without_name_or_email() -> None:
    repo = repository()
    await create_suggestion(
        user=user(name=None, display_name=None, email=None),
        viewer="user:123", repository=repo, image_store=image_store(),
        title="Add dark mode", description="A dark mode option for all pages", images=None,
    )
    assert repo.create.await_args.kwargs["author"] == "123"


async def test_status_update_falls_back_to_viewer_without_name_or_email() -> None:
    repo = repository()
    await update_suggestion_status(
        suggestion_id="abc123", payload=SuggestionStatusUpdate(status="open"),
        current_user=user(name=None, display_name=None, email=None),
        viewer="user:123", repository=repo,
        authorization_service=authorization_service({Permission.SUGGESTION_MANAGE}),
    )
    assert repo.update_status.await_args.kwargs["updated_by_name"] == "user:123"


async def test_status_update_propagates_concurrency_conflict() -> None:
    repo = repository(update_status=AsyncMock(side_effect=RuntimeError("Suggestion status was changed by another request")))
    with pytest.raises(RuntimeError, match="changed by another request"):
        await update_suggestion_status(
            suggestion_id="abc123", payload=SuggestionStatusUpdate(status="planned"),
            current_user=user(), viewer="user:123", repository=repo,
            authorization_service=authorization_service({Permission.SUGGESTION_MANAGE}),
        )
