from __future__ import annotations

import logging
import os
import re
import uuid
import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from typing import Any, BinaryIO

from fastapi import HTTPException, UploadFile, status
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener
from pymongo.asynchronous.database import AsyncDatabase
from starlette.concurrency import run_in_threadpool

ALLOWED_CONTENT_TYPES = frozenset({"image/png", "image/jpeg", "image/heic", "image/heif"})
HEIC_CONTENT_TYPES = frozenset({"image/heic", "image/heif"})
IMAGE_FORMAT_TO_CONTENT_TYPE = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "HEIC": "image/heic",
    "HEIF": "image/heic",
}
# Allows current 48 MP phone photos while rejecting unusually large decode payloads.
Image.MAX_IMAGE_PIXELS = 60_000_000
register_heif_opener()


@dataclass(frozen=True, slots=True)
class PreparedImage:
    stream: BinaryIO
    source_content_type: str
    content_type: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class SuggestionImageSettings:
    collection_name: str = "image_attachments"
    namespace: str = "suggestions"
    max_files: int = 3
    max_bytes: int = 3 * 1024 * 1024


class SuggestionImageStore:
    def __init__(
        self,
        settings: SuggestionImageSettings,
        database: AsyncDatabase[dict[str, Any]],
    ) -> None:
        if not 0 < settings.max_bytes <= 3 * 1024 * 1024:
            raise ValueError("Image size limit must be between 1 byte and 3 MiB")
        self.settings = settings
        self.collection = database[settings.collection_name]

    async def upload_many(
        self,
        *,
        suggestion_id: str,
        files: Sequence[UploadFile],
    ) -> list[dict[str, object]]:
        if len(files) > self.settings.max_files:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"At most {self.settings.max_files} images are allowed",
            )

        uploaded: list[dict[str, object]] = []
        try:
            for image in files:
                prepared = await self._prepare_image(image)
                attachment_id = uuid.uuid4().hex
                extension = ".png" if prepared.content_type == "image/png" else ".jpg"
                filename = self._safe_filename(image.filename, extension)
                storage_id = f"{self.settings.namespace}/{suggestion_id}/{attachment_id}"
                try:
                    content = prepared.stream.read()
                    await self.collection.insert_one(
                        {
                            "_id": storage_id,
                            "namespace": self.settings.namespace,
                            "parent_upload_id": suggestion_id,
                            "filename": filename,
                            "content_type": prepared.content_type,
                            "data": content,
                        }
                    )
                finally:
                    if prepared.stream is not image.file:
                        prepared.stream.close()

                uploaded.append(
                    {
                        "id": attachment_id,
                        "filename": filename,
                        "content_type": prepared.content_type,
                        "size_bytes": prepared.size_bytes,
                        "storage_id": storage_id,
                    }
                )
        except Exception:
            await self.delete_many(uploaded)
            raise

        return uploaded

    async def delete_many(self, attachments: Sequence[dict[str, object]]) -> None:
        for attachment in attachments:
            storage_id = attachment.get("storage_id")
            if not isinstance(storage_id, str):
                continue
            try:
                await self.collection.delete_one(
                    {
                        "_id": storage_id,
                        "namespace": self.settings.namespace,
                    }
                )
            except Exception:
                # Preserve submission errors when rollback also fails.
                logging.getLogger(__name__).exception("Could not clean up image %s", storage_id)

    async def download(self, attachment: dict[str, object]) -> bytes:
        storage_id = attachment.get("storage_id")
        if not isinstance(storage_id, str):
            raise HTTPException(status_code=404, detail="Image must be re-uploaded")
        document = await self.collection.find_one(
            {
                "_id": storage_id,
                "namespace": self.settings.namespace,
            }
        )
        if document is None:
            raise HTTPException(status_code=404, detail="Image not found")
        return bytes(document["data"])

    async def _prepare_image(self, image: UploadFile) -> PreparedImage:
        declared_type = (image.content_type or "").lower()
        filename = image.filename or "Image"
        extension = os.path.splitext(filename)[1].lower()
        is_heic_filename = extension in {".heic", ".heif"}
        if declared_type not in ALLOWED_CONTENT_TYPES and not (
            is_heic_filename and declared_type in {"", "application/octet-stream"}
        ):
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"{filename} must be PNG, JPEG, HEIC, or HEIF",
            )

        input_size = await run_in_threadpool(self._file_size, image)
        self._validate_size(filename, input_size)

        try:
            prepared = await run_in_threadpool(self._decode_and_prepare, image)
        except (
            EOFError,
            OSError,
            RuntimeError,
            SyntaxError,
            ValueError,
            Image.DecompressionBombWarning,
            Image.DecompressionBombError,
        ) as error:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"{filename} is not a valid PNG, JPEG, HEIC, or HEIF image",
            ) from error

        detected_type = prepared.source_content_type
        declared_matches = declared_type == detected_type or (
            detected_type == "image/heic"
            and (
                declared_type in HEIC_CONTENT_TYPES
                or (is_heic_filename and declared_type in {"", "application/octet-stream"})
            )
        )
        if not declared_matches:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"{filename} content does not match its file type",
            )

        self._validate_size(filename, prepared.size_bytes)
        prepared.stream.seek(0)
        return prepared

    def _validate_size(self, filename: str, size_bytes: int) -> None:
        if size_bytes == 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{filename} is empty",
            )
        if size_bytes > self.settings.max_bytes:
            max_mb = self.settings.max_bytes // (1024 * 1024)
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"{filename} exceeds {max_mb} MiB",
            )

    @staticmethod
    def _file_size(image: UploadFile) -> int:
        image.file.seek(0, os.SEEK_END)
        size = image.file.tell()
        image.file.seek(0)
        return size

    @staticmethod
    def _decode_and_prepare(image: UploadFile) -> PreparedImage:
        image.file.seek(0)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(image.file) as parsed:
                detected_type = IMAGE_FORMAT_TO_CONTENT_TYPE.get(parsed.format or "")
                if detected_type is None:
                    raise ValueError("Unsupported image format")

                if detected_type == "image/heic":
                    converted = ImageOps.exif_transpose(parsed).convert("RGB")
                    output = BytesIO()
                    converted.save(output, format="JPEG", quality=90, optimize=True)
                    output.seek(0)
                    return PreparedImage(
                        stream=output,
                        source_content_type="image/heic",
                        content_type="image/jpeg",
                        size_bytes=output.getbuffer().nbytes,
                    )

                parsed.verify()

        input_size = SuggestionImageStore._file_size(image)
        image.file.seek(0)
        return PreparedImage(
            stream=image.file,
            source_content_type=detected_type,
            content_type=detected_type,
            size_bytes=input_size,
        )

    @staticmethod
    def _safe_filename(original: str | None, extension: str) -> str:
        stem = os.path.splitext(os.path.basename(original or "image"))[0]
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._")[:80] or "image"
        return f"{stem}{extension}"
