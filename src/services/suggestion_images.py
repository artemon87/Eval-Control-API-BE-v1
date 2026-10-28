from __future__ import annotations

import os
import re
import uuid
import warnings
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from io import BytesIO
from typing import BinaryIO

from fastapi import HTTPException, UploadFile, status
from google.cloud import storage
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener
from starlette.concurrency import run_in_threadpool


ALLOWED_CONTENT_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/heic", "image/heif"}
)
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
    bucket_name: str
    object_prefix: str = "suggestions"
    max_files: int = 3
    max_bytes: int = 10 * 1024 * 1024

class SuggestionImageStore:
    def __init__(
        self,
        settings: SuggestionImageSettings,
        client: storage.Client | None = None,
    ) -> None:
        self.settings = settings
        self.client = client or storage.Client()
        self.bucket = self.client.bucket(settings.bucket_name)

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
                object_name = (
                    f"{self.settings.object_prefix}/{suggestion_id}/"
                    f"{attachment_id}/{filename}"
                )

                blob = self.bucket.blob(object_name)
                await run_in_threadpool(
                    blob.upload_from_file,
                    prepared.stream,
                    content_type=prepared.content_type,
                    size=prepared.size_bytes,
                    rewind=True,
                    if_generation_match=0,
                    checksum="auto",
                )

                uploaded.append(
                    {
                        "id": attachment_id,
                        "filename": filename,
                        "content_type": prepared.content_type,
                        "size_bytes": prepared.size_bytes,
                        "object_name": object_name,
                    }
                )
        except Exception:
            await self.delete_many(uploaded)
            raise

        return uploaded

    async def delete_many(self, attachments: Sequence[dict[str, object]]) -> None:
        for attachment in attachments:
            object_name = attachment.get("object_name")
            if not isinstance(object_name, str):
                continue
            try:
                await run_in_threadpool(self.bucket.blob(object_name).delete)
            except Exception:
                # Cleanup is best effort. The original exception is more useful to callers.
                pass

    def iter_download(self, object_name: str, *, chunk_size: int = 256 * 1024) -> Iterator[bytes]:
        blob = self.bucket.blob(object_name)
        with blob.open("rb") as stream:
            while chunk := stream.read(chunk_size):
                yield chunk

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
                detail=f"{filename} exceeds {max_mb} MB",
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
