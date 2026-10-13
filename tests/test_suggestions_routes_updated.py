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
    create_suggestion,
    get_suggestion_attachment,
    update_suggestion_status,
)
from src.models.suggestions import SuggestionStatusUpdate
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_create_falls_back_to_email() -> None:
    repo = repository()
    await create_suggestion(
        user=user(display_name=None), viewer="user:123", repository=repo,
        image_store=image_store(), title="Add dark mode",
        description="A dark mode option for all pages", images=None,
    )
    assert repo.create.await_args.kwargs["author"] == "alex@example.com"


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_update_status_falls_back_to_email() -> None:
    repo = repository()
    await update_suggestion_status(
        suggestion_id="abc123", payload=SuggestionStatusUpdate(status="open"),
        current_user=user(name=None, display_name=None), viewer="user:123",
        repository=repo,
        authorization_service=authorization_service({Permission.SUGGESTION_MANAGE}),
    )
    assert repo.update_status.await_args.kwargs["updated_by_name"] == "alex@example.com"
