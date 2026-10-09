from __future__ import annotations

import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from starlette.responses import StreamingResponse

from src.api.dependencies import SuggestionImageStoreDependency, SuggestionRepositoryDependency
from src.models.auth import CurrentUser
from src.models.suggestions import SuggestionListResponse, SuggestionResponse
from src.security.auth import get_authenticated_user


router = APIRouter(prefix="/suggestions", tags=["Suggestions"])


def current_user(
    user: Annotated[CurrentUser, Depends(get_authenticated_user)],
) -> str:
    identity = user.object_id or user.subject or user.email
    if not identity:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated user has no usable identity",
        )

    return f"user:{identity}"


Viewer = Annotated[str, Depends(current_user)]


@router.get("")
async def list_suggestions(
    viewer: Viewer,
    repository: SuggestionRepositoryDependency,
    limit: int = 100,
) -> SuggestionListResponse:
    items = await repository.list(
        viewer=viewer,
        limit=limit,
    )

    return SuggestionListResponse(
        items=[SuggestionResponse.model_validate(item) for item in items]
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_suggestion(
    viewer: Viewer,
    repository: SuggestionRepositoryDependency,
    image_store: SuggestionImageStoreDependency,
    title: Annotated[str, Form(min_length=3, max_length=120)],
    description: Annotated[str, Form(min_length=10, max_length=2000)],
    images: Annotated[list[UploadFile] | None, File()] = None,
) -> SuggestionResponse:
    attachments = await image_store.upload_many(
        suggestion_id=uuid.uuid4().hex,
        files=images or [],
    )

    try:
        document = await repository.create(
            title=title,
            description=description,
            author=viewer,
            attachments=attachments,
        )
    except Exception:
        await image_store.delete_many(attachments)
        raise

    return SuggestionResponse.model_validate(document)


@router.get("/{suggestion_id}/attachments/{attachment_id}")
async def get_suggestion_attachment(
    suggestion_id: str,
    attachment_id: str,
    _viewer: Viewer,
    repository: SuggestionRepositoryDependency,
    image_store: SuggestionImageStoreDependency,
) -> StreamingResponse:
    try:
        attachment = await repository.get_attachment(
            suggestion_id=suggestion_id,
            attachment_id=attachment_id,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

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
    viewer: Viewer,
    repository: SuggestionRepositoryDependency,
) -> None:
    try:
        await repository.add_vote(
            suggestion_id=suggestion_id,
            user_id=viewer,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.delete("/{suggestion_id}/vote", status_code=status.HTTP_204_NO_CONTENT)
async def remove_vote(
    suggestion_id: str,
    viewer: Viewer,
    repository: SuggestionRepositoryDependency,
) -> None:
    try:
        await repository.remove_vote(
            suggestion_id=suggestion_id,
            user_id=viewer,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
