from __future__ import annotations

from io import BytesIO
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, UploadFile
from PIL import Image

from src.services.suggestion_images import SuggestionImageSettings, SuggestionImageStore


def _store(*, max_bytes: int = 1024) -> SuggestionImageStore:
    store = object.__new__(SuggestionImageStore)
    store.settings = SuggestionImageSettings(
        max_bytes=max_bytes,
    )
    return store


def _image_bytes(image_format: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 2), color="white").save(output, format=image_format)
    return output.getvalue()


@pytest.mark.asyncio
async def test_accepts_png_magic_bytes() -> None:
    image = UploadFile(
        filename="screen.png",
        file=BytesIO(_image_bytes("PNG")),
        headers={"content-type": "image/png"},
    )

    result = await _store()._prepare_image(image)

    assert result.source_content_type == "image/png"
    assert result.content_type == "image/png"
    assert result.size_bytes > 0


@pytest.mark.asyncio
async def test_converts_heic_to_browser_safe_jpeg() -> None:
    image = UploadFile(
        filename="iphone-photo.heic",
        file=BytesIO(_image_bytes("HEIF")),
        headers={"content-type": "image/heic"},
    )

    result = await _store(max_bytes=4096)._prepare_image(image)

    assert result.source_content_type == "image/heic"
    assert result.content_type == "image/jpeg"
    assert result.stream.read(3) == b"\xff\xd8\xff"


@pytest.mark.asyncio
async def test_rejects_extension_spoofing() -> None:
    image = UploadFile(
        filename="not-really.png",
        file=BytesIO(b"not an image"),
        headers={"content-type": "image/png"},
    )

    with pytest.raises(HTTPException) as error:
        await _store()._prepare_image(image)

    assert error.value.status_code == 415


@pytest.mark.asyncio
async def test_rejects_oversized_image() -> None:
    image = UploadFile(
        filename="large.jpg",
        file=BytesIO(_image_bytes("JPEG") + b"x" * 20),
        headers={"content-type": "image/jpeg"},
    )

    with pytest.raises(HTTPException) as error:
        await _store(max_bytes=10)._prepare_image(image)

    assert error.value.status_code == 413


def mongo_store(*, namespace: str = "suggestions") -> tuple[SuggestionImageStore, Any]:
    collection = MagicMock()
    collection.insert_one = AsyncMock()
    collection.delete_one = AsyncMock()
    collection.find_one = AsyncMock()
    database = MagicMock()
    database.__getitem__.return_value = collection
    return SuggestionImageStore(SuggestionImageSettings(namespace=namespace), database), collection


def png_upload() -> UploadFile:
    return UploadFile(
        filename="screen.png",
        file=BytesIO(_image_bytes("PNG")),
        headers={"content-type": "image/png"},
    )


@pytest.mark.asyncio
async def test_mongo_roundtrip_keeps_binary_out_of_parent_metadata() -> None:
    store, collection = mongo_store()
    attachments = await store.upload_many(suggestion_id="parent", files=[png_upload()])
    document = collection.insert_one.call_args.args[0]
    assert document["data"] == _image_bytes("PNG")
    assert isinstance(document["data"], bytes)
    assert "data" not in attachments[0]
    assert "object_name" not in attachments[0]
    collection.find_one.return_value = document
    assert await store.download(attachments[0]) == document["data"]
    assert collection.find_one.call_args.args[0]["namespace"] == "suggestions"


@pytest.mark.asyncio
async def test_rolls_back_first_file_when_second_image_is_invalid() -> None:
    store, collection = mongo_store()
    invalid = UploadFile(
        filename="bad.png", file=BytesIO(b"not an image"), headers={"content-type": "image/png"}
    )
    with pytest.raises(HTTPException):
        await store.upload_many(suggestion_id="parent", files=[png_upload(), invalid])
    collection.delete_one.assert_awaited_once_with(
        {
            "_id": collection.insert_one.call_args.args[0]["_id"],
            "namespace": "suggestions",
        }
    )


@pytest.mark.asyncio
async def test_rejects_too_many_files_before_any_write() -> None:
    store, collection = mongo_store()
    with pytest.raises(HTTPException) as error:
        await store.upload_many(suggestion_id="parent", files=[png_upload() for _ in range(4)])
    assert error.value.status_code == 422
    collection.insert_one.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("attachment", [{"storage_id": "missing"}, {"object_name": "old-gcp"}])
async def test_missing_and_legacy_images_return_404(attachment: dict[str, object]) -> None:
    store, collection = mongo_store()
    collection.find_one.return_value = None
    with pytest.raises(HTTPException) as error:
        await store.download(attachment)
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_product_update_cleanup_is_scoped_and_ignores_legacy_gcp() -> None:
    store, collection = mongo_store(namespace="product-updates")
    await store.delete_many([{"storage_id": "new"}, {"object_name": "old"}])
    collection.delete_one.assert_awaited_once_with({"_id": "new", "namespace": "product-updates"})


def test_settings_no_longer_require_gcp_and_enforce_three_mib() -> None:
    from pydantic import ValidationError

    from src.config import Settings

    assert Settings(_env_file=None).suggestion_image_max_bytes == 3 * 1024 * 1024
    with pytest.raises(ValidationError):
        Settings(_env_file=None, suggestion_image_max_bytes=10 * 1024 * 1024)


@pytest.mark.asyncio
async def test_three_mib_boundary_is_inclusive() -> None:
    store, _ = mongo_store()
    store._validate_size("image.png", 3 * 1024 * 1024)
    with pytest.raises(HTTPException) as error:
        store._validate_size("image.png", 3 * 1024 * 1024 + 1)
    assert error.value.status_code == 413
