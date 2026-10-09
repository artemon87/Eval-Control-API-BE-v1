from __future__ import annotations

from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile
from PIL import Image

from src.services.suggestion_images import SuggestionImageSettings, SuggestionImageStore


def _store(*, max_bytes: int = 1024) -> SuggestionImageStore:
    store = object.__new__(SuggestionImageStore)
    store.settings = SuggestionImageSettings(
        bucket_name="unit-test-bucket",
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
