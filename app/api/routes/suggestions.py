from __future__ import annotations

import datetime as dt
from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import quote

from bson import ObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from src.models.auth import CurrentUser
from src.repositories.suggestions import SUGGESTION_NOT_FOUND, SuggestionRepository
from src.security.auth import get_authenticated_user
from src.services.suggestion_images import SuggestionImageSettings, SuggestionImageStore

router = APIRouter(prefix="/suggestions", tags=["suggestions"])


class SuggestionAttachmentResponse(BaseModel):
    id: str
    filename: str
    content_type: Literal["image/png", "image/jpeg"]
    size_bytes: int


class SuggestionResponse(BaseModel):
    id: str
    title: str
    description: str
    author: str
    status: Literal["open", "planned", "completed", "declined"]
    vote_count: int
    created_at: dt.datetime
    attachments: list[SuggestionAttachmentResponse]
    viewer_has_voted: bool


def suggestion_repository(request: Request) -> SuggestionRepository:
    # Register this once during startup, alongside the other repositories.
    return request.app.state.suggestion_repository


@lru_cache(maxsize=1)
def suggestion_image_store() -> SuggestionImageStore:
    return SuggestionImageStore(SuggestionImageSettings.from_environment())


def _principal_id(user: CurrentUser) -> str:
    for attribute in ("principal_id", "object_id", "subject", "email"):
        value = getattr(user, attribute, None)
        if value:
            return str(value)
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authenticated user required")


def _author_name(user: CurrentUser) -> str:
    return str(getattr(user, "display_name", None) or getattr(user, "email", None) or _principal_id(user))


@router.get("", response_model=dict[str, list[SuggestionResponse]])
async def list_suggestions(
    repository: Annotated[SuggestionRepository, Depends(suggestion_repository)],
    current_user: Annotated[CurrentUser, Depends(get_authenticated_user)],
    limit: int = 100,
) -> dict[str, list[dict[str, object]]]:
    return {"items": await repository.list(viewer=_principal_id(current_user), limit=limit)}


@router.post("", response_model=SuggestionResponse, status_code=status.HTTP_201_CREATED)
async def create_suggestion(
    repository: Annotated[SuggestionRepository, Depends(suggestion_repository)],
    image_store: Annotated[SuggestionImageStore, Depends(suggestion_image_store)],
    current_user: Annotated[CurrentUser, Depends(get_authenticated_user)],
    title: Annotated[str, Form(min_length=3, max_length=120)],
    description: Annotated[str, Form(min_length=10, max_length=2000)],
    images: Annotated[list[UploadFile] | None, File()] = None,
) -> dict[str, object]:
    suggestion_id = ObjectId()
    attachments = await image_store.upload_many(
        suggestion_id=str(suggestion_id),
        files=images or [],
    )
    try:
        return await repository.create(
            suggestion_id=suggestion_id,
            title=title,
            description=description,
            author=_author_name(current_user),
            attachments=attachments,
        )
    except Exception:
        await image_store.delete_many(attachments)
        raise


@router.get("/{suggestion_id}/attachments/{attachment_id}")
async def get_suggestion_attachment(
    suggestion_id: str,
    attachment_id: str,
    repository: Annotated[SuggestionRepository, Depends(suggestion_repository)],
    image_store: Annotated[SuggestionImageStore, Depends(suggestion_image_store)],
    _current_user: Annotated[CurrentUser, Depends(get_authenticated_user)],
) -> StreamingResponse:
    try:
        attachment = await repository.get_attachment(
            suggestion_id=suggestion_id,
            attachment_id=attachment_id,
        )
    except LookupError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=SUGGESTION_NOT_FOUND) from error

    filename = str(attachment["filename"])
    return StreamingResponse(
        image_store.iter_download(str(attachment["object_name"])),
        media_type=str(attachment["content_type"]),
        headers={
            "Cache-Control": "private, max-age=300",
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.put("/{suggestion_id}/vote", status_code=status.HTTP_204_NO_CONTENT)
async def add_vote(
    suggestion_id: str,
    repository: Annotated[SuggestionRepository, Depends(suggestion_repository)],
    current_user: Annotated[CurrentUser, Depends(get_authenticated_user)],
) -> None:
    try:
        await repository.add_vote(suggestion_id=suggestion_id, user_id=_principal_id(current_user))
    except LookupError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=SUGGESTION_NOT_FOUND) from error


@router.delete("/{suggestion_id}/vote", status_code=status.HTTP_204_NO_CONTENT)
async def remove_vote(
    suggestion_id: str,
    repository: Annotated[SuggestionRepository, Depends(suggestion_repository)],
    current_user: Annotated[CurrentUser, Depends(get_authenticated_user)],
) -> None:
    try:
        await repository.remove_vote(suggestion_id=suggestion_id, user_id=_principal_id(current_user))
    except LookupError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=SUGGESTION_NOT_FOUND) from error
