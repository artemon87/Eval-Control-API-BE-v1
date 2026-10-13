"""Additional coverage for the Suggestions response contract introduced in October 2026.

Keep existing SuggestionCreate boundary tests in test_suggestions_models.py.
"""

import datetime as dt

import pytest
from pydantic import ValidationError

from src.models.suggestions import (
    SuggestionAttachmentResponse,
    SuggestionListResponse,
    SuggestionResponse,
    SuggestionStatusUpdate,
)


def response_data(**changes: object) -> dict[str, object]:
    data: dict[str, object] = {
        "id": "abc123",
        "title": "Add dark mode",
        "description": "Please add a dark mode option for the application.",
        "author": "Alex Smith",
        "status": "open",
        "vote_count": 2,
        "created_at": dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        "viewer_has_voted": False,
    }
    data.update(changes)
    return data


def attachment_data(**changes: object) -> dict[str, object]:
    data: dict[str, object] = {
        "id": "image123", "filename": "example.png",
        "content_type": "image/png", "size_bytes": 1234,
    }
    data.update(changes)
    return data


def test_legacy_response_defaults_new_fields() -> None:
    result = SuggestionResponse.model_validate(response_data())
    assert result.attachments == []
    assert result.status_updated_at is None
    assert result.status_updated_by_id is None
    assert result.status_updated_by_name is None


def test_status_audit_and_attachments_round_trip() -> None:
    updated = dt.datetime(2026, 10, 12, 18, 30, tzinfo=dt.UTC)
    result = SuggestionResponse.model_validate(
        response_data(
            status_updated_at=updated,
            status_updated_by_id="user:42",
            status_updated_by_name="Alex Smith",
            attachments=[attachment_data()],
        )
    )
    assert result.status_updated_at == updated
    assert result.status_updated_by_id == "user:42"
    assert result.status_updated_by_name == "Alex Smith"
    assert result.attachments[0].filename == "example.png"
    encoded = result.model_dump(mode="json")
    assert encoded["attachments"][0]["content_type"] == "image/png"
    assert encoded["status_updated_by_name"] == "Alex Smith"


def test_attachment_lists_are_independent() -> None:
    a = SuggestionResponse.model_validate(response_data())
    b = SuggestionResponse.model_validate(response_data(id="other"))
    a.attachments.append(SuggestionAttachmentResponse.model_validate(attachment_data()))
    assert b.attachments == []


@pytest.mark.parametrize("content_type", ["image/png", "image/jpeg"])
def test_attachment_accepts_supported_stored_content_types(content_type: str) -> None:
    result = SuggestionAttachmentResponse.model_validate(
        attachment_data(content_type=content_type)
    )
    assert result.content_type == content_type


@pytest.mark.parametrize("content_type", ["image/heic", "image/gif", "text/plain"])
def test_attachment_rejects_non_stored_content_types(content_type: str) -> None:
    with pytest.raises(ValidationError):
        SuggestionAttachmentResponse.model_validate(
            attachment_data(content_type=content_type)
        )


def test_status_update_requires_a_status() -> None:
    with pytest.raises(ValidationError):
        SuggestionStatusUpdate.model_validate({})


def test_status_update_accepts_open() -> None:
    assert SuggestionStatusUpdate.model_validate({"status": "open"}).status == "open"


def test_status_update_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        SuggestionStatusUpdate.model_validate({"status": "not-a-real-status"})


def test_list_response_validates_nested_suggestions() -> None:
    result = SuggestionListResponse.model_validate({"items": [response_data()]})
    assert len(result.items) == 1
    assert result.items[0].id == "abc123"


def test_empty_list_response() -> None:
    assert SuggestionListResponse(items=[]).items == []
