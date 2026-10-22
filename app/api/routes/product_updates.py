from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from starlette.responses import StreamingResponse

from src.api.dependencies import (
    ProductUpdateImageStoreDependency,
    ProductUpdateRepositoryDependency,
)
from src.models.product_updates import ProductUpdateListResponse, ProductUpdateResponse
from src.security.require_permission import EvalHubAdminDependency


router = APIRouter(prefix="/product-updates", tags=["Product updates"])


def _highlights(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="highlights must be a JSON array",
        ) from exc
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="highlights must be a JSON array of strings",
        )
    cleaned = [item.strip() for item in parsed if item.strip()]
    if len(cleaned) > 8 or any(len(item) > 160 for item in cleaned):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Use at most 8 highlights of 160 characters",
        )
    return cleaned


@router.get("")
async def list_product_updates(
    repository: ProductUpdateRepositoryDependency,
    limit: int = 100,
) -> ProductUpdateListResponse:
    return ProductUpdateListResponse(items=await repository.list(limit=limit))


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Invalid highlights or product update form data"},
    },
)
async def create_product_update(
    _admin: EvalHubAdminDependency,
    repository: ProductUpdateRepositoryDependency,
    image_store: ProductUpdateImageStoreDependency,
    status_value: Annotated[str, Form(alias="status", pattern="^(shipped|coming|planned)$")],
    release_date: Annotated[str, Form(pattern=r"^\d{4}-\d{2}-\d{2}$")],
    category: Annotated[str, Form(min_length=2, max_length=80)],
    title: Annotated[str, Form(min_length=3, max_length=120)],
    description: Annotated[str, Form(min_length=10, max_length=2000)],
    highlights: Annotated[str, Form()],
    image: Annotated[UploadFile | None, File()] = None,
) -> ProductUpdateResponse:
    attachments = await image_store.upload_many(
        suggestion_id=uuid.uuid4().hex,
        files=[image] if image else [],
    )
    try:
        return ProductUpdateResponse.model_validate(await repository.create(
            status=status_value,
            release_date=date.fromisoformat(release_date),
            category=category,
            title=title,
            description=description,
            highlights=_highlights(highlights),
            image=attachments[0] if attachments else None,
        ))
    except Exception:
        await image_store.delete_many(attachments)
        raise


@router.patch(
    "/{update_id}",
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Product update not found"},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Invalid highlights or product update form data"},
    },
)
async def update_product_update(
    update_id: str,
    _admin: EvalHubAdminDependency,
    repository: ProductUpdateRepositoryDependency,
    status_value: Annotated[str, Form(alias="status", pattern="^(shipped|coming|planned)$")],
    release_date: Annotated[str, Form(pattern=r"^\d{4}-\d{2}-\d{2}$")],
    category: Annotated[str, Form(min_length=2, max_length=80)],
    title: Annotated[str, Form(min_length=3, max_length=120)],
    description: Annotated[str, Form(min_length=10, max_length=2000)],
    highlights: Annotated[str, Form()],
) -> ProductUpdateResponse:
    document = await repository.update(
        update_id,
        {
            "status": status_value,
            "release_date": release_date,
            "category": category.strip(),
            "title": title.strip(),
            "description": description.strip(),
            "highlights": _highlights(highlights),
        },
    )
    return ProductUpdateResponse.model_validate(document)


@router.delete(
    "/{update_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Product update not found"},
    },
)
async def delete_product_update(
    update_id: str,
    _admin: EvalHubAdminDependency,
    repository: ProductUpdateRepositoryDependency,
    image_store: ProductUpdateImageStoreDependency,
) -> None:
    document = await repository.delete(update_id)
    if document.get("image"):
        await image_store.delete_many([document["image"]])


@router.get(
    "/{update_id}/image",
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Product update image not found"},
    },
)
async def get_product_update_image(
    update_id: str,
    repository: ProductUpdateRepositoryDependency,
    image_store: ProductUpdateImageStoreDependency,
) -> StreamingResponse:
    image = await repository.get_image(update_id)
    filename = str(image["filename"])
    return StreamingResponse(
        image_store.iter_download(str(image["object_name"])),
        media_type=str(image["content_type"]),
        headers={
            "Cache-Control": "private, max-age=300",
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
            "X-Content-Type-Options": "nosniff",
        },
    )
