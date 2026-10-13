"""GCS-free tests for suggestion image validation, conversion and cleanup."""

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException, UploadFile
from PIL import Image

from src.services.suggestion_images import SuggestionImageSettings, SuggestionImageStore


def upload(name: str, content_type: str, payload: bytes) -> UploadFile:
    return UploadFile(filename=name, file=BytesIO(payload), headers={"content-type": content_type})


def image_bytes(fmt: str = "PNG") -> bytes:
    out = BytesIO()
    Image.new("RGB", (2, 2), "white").save(out, format=fmt)
    return out.getvalue()


class BlobSpy:
    def __init__(self, content: bytes = b"image bytes", fail_upload: bool = False) -> None:
        self.upload_calls: list[dict[str, object]] = []
        self.deleted = False
        self.content = content
        self.fail_upload = fail_upload

    def upload_from_file(self, stream: object, **kwargs: object) -> None:
        if self.fail_upload:
            raise RuntimeError("GCS unavailable")
        self.upload_calls.append({"content": stream.read(), **kwargs})  # type: ignore[attr-defined]

    def delete(self) -> None:
        self.deleted = True

    def open(self, mode: str) -> BytesIO:
        assert mode == "rb"
        return BytesIO(self.content)


class BucketSpy:
    def __init__(self) -> None:
        self.blobs: dict[str, BlobSpy] = {}
        self.fail_after: int | None = None

    def blob(self, name: str) -> BlobSpy:
        if name not in self.blobs:
            self.blobs[name] = BlobSpy(
                fail_upload=self.fail_after is not None and len(self.blobs) >= self.fail_after
            )
        return self.blobs[name]


@pytest.fixture
def store() -> tuple[SuggestionImageStore, BucketSpy]:
    bucket = BucketSpy()
    client = Mock()
    client.bucket.return_value = bucket
    service = SuggestionImageStore(
        SuggestionImageSettings(
            bucket_name="test-bucket", object_prefix="suggestions",
            max_files=3, max_bytes=1024 * 1024,
        ),
        client=client,
    )
    return service, bucket


@pytest.mark.asyncio
async def test_empty_upload_list_returns_empty_result(store: tuple[SuggestionImageStore, BucketSpy]) -> None:
    service, bucket = store
    assert await service.upload_many(suggestion_id="abc", files=[]) == []
    assert bucket.blobs == {}


@pytest.mark.asyncio
async def test_png_upload_records_private_object_metadata(store: tuple[SuggestionImageStore, BucketSpy]) -> None:
    service, bucket = store
    result = await service.upload_many(
        suggestion_id="abc", files=[upload("my photo.png", "image/png", image_bytes())],
    )
    assert len(result) == 1
    attachment = result[0]
    assert attachment["id"]
    assert attachment["filename"].endswith(".png")
    assert attachment["content_type"] == "image/png"
    assert attachment["size_bytes"] == len(image_bytes())
    assert str(attachment["object_name"]).startswith("suggestions/abc/")
    assert bucket.blobs[str(attachment["object_name"])].upload_calls[0]["content_type"] == "image/png"


@pytest.mark.asyncio
async def test_jpeg_upload_keeps_jpeg_type(store: tuple[SuggestionImageStore, BucketSpy]) -> None:
    service, _ = store
    result = await service.upload_many(
        suggestion_id="abc", files=[upload("photo.jpg", "image/jpeg", image_bytes("JPEG"))],
    )
    assert result[0]["content_type"] == "image/jpeg"
    assert str(result[0]["filename"]).endswith(".jpg")


@pytest.mark.asyncio
async def test_heic_filename_with_octet_stream_is_not_rejected_as_type_before_decoding(
    store: tuple[SuggestionImageStore, BucketSpy],
) -> None:
    service, _ = store
    # Deliberately invalid HEIC bytes: accepted declaration, rejected during decoding.
    with pytest.raises(HTTPException) as error:
        await service.upload_many(
            suggestion_id="abc", files=[upload("photo.heic", "application/octet-stream", b"invalid heic")],
        )
    assert error.value.status_code == 415
    assert "not a valid" in str(error.value.detail)


@pytest.mark.parametrize("kind", ["image/gif", "application/pdf", "text/plain"])
@pytest.mark.asyncio
async def test_unsupported_content_type_is_415(
    store: tuple[SuggestionImageStore, BucketSpy], kind: str,
) -> None:
    service, bucket = store
    with pytest.raises(HTTPException) as error:
        await service.upload_many(
            suggestion_id="abc", files=[upload("file.dat", kind, b"irrelevant")],
        )
    assert error.value.status_code == 415
    assert bucket.blobs == {}


@pytest.mark.asyncio
async def test_mismatched_declared_format_is_415(store: tuple[SuggestionImageStore, BucketSpy]) -> None:
    service, _ = store
    with pytest.raises(HTTPException) as error:
        await service.upload_many(
            suggestion_id="abc", files=[upload("fake.png", "image/png", image_bytes("JPEG"))],
        )
    assert error.value.status_code == 415
    assert "does not match" in str(error.value.detail)


@pytest.mark.asyncio
async def test_corrupt_image_is_415(store: tuple[SuggestionImageStore, BucketSpy]) -> None:
    service, _ = store
    with pytest.raises(HTTPException) as error:
        await service.upload_many(
            suggestion_id="abc", files=[upload("broken.png", "image/png", b"not PNG")],
        )
    assert error.value.status_code == 415


@pytest.mark.asyncio
async def test_empty_file_is_422(store: tuple[SuggestionImageStore, BucketSpy]) -> None:
    service, _ = store
    with pytest.raises(HTTPException) as error:
        await service.upload_many(suggestion_id="abc", files=[upload("empty.png", "image/png", b"")])
    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_too_many_files_is_422(store: tuple[SuggestionImageStore, BucketSpy]) -> None:
    service, bucket = store
    with pytest.raises(HTTPException) as error:
        await service.upload_many(
            suggestion_id="abc",
            files=[upload(f"{i}.png", "image/png", image_bytes()) for i in range(4)],
        )
    assert error.value.status_code == 422
    assert bucket.blobs == {}


@pytest.mark.asyncio
async def test_oversized_image_is_413(store: tuple[SuggestionImageStore, BucketSpy]) -> None:
    service, _ = store
    object.__setattr__(service.settings, "max_bytes", 5)
    with pytest.raises(HTTPException) as error:
        await service.upload_many(
            suggestion_id="abc", files=[upload("big.png", "image/png", image_bytes())],
        )
    assert error.value.status_code == 413


@pytest.mark.asyncio
async def test_second_upload_failure_deletes_first_image(store: tuple[SuggestionImageStore, BucketSpy]) -> None:
    service, bucket = store
    bucket.fail_after = 1
    with pytest.raises(RuntimeError, match="GCS unavailable"):
        await service.upload_many(
            suggestion_id="abc",
            files=[upload("first.png", "image/png", image_bytes()),
                   upload("second.png", "image/png", image_bytes())],
        )
    first = next(iter(bucket.blobs.values()))
    assert first.deleted is True


@pytest.mark.asyncio
async def test_delete_many_ignores_non_string_object_names(store: tuple[SuggestionImageStore, BucketSpy]) -> None:
    service, bucket = store
    await service.delete_many([{"object_name": None}, {"id": "missing"}])
    assert bucket.blobs == {}


@pytest.mark.asyncio
async def test_delete_many_deletes_valid_object(store: tuple[SuggestionImageStore, BucketSpy]) -> None:
    service, bucket = store
    await service.delete_many([{"object_name": "suggestions/abc/a.png"}])
    assert bucket.blobs["suggestions/abc/a.png"].deleted


@pytest.mark.asyncio
async def test_delete_many_is_best_effort(store: tuple[SuggestionImageStore, BucketSpy]) -> None:
    service, bucket = store
    failing = bucket.blob("suggestions/abc/failure.png")
    failing.delete = Mock(side_effect=RuntimeError("cannot delete"))  # type: ignore[method-assign]
    await service.delete_many([{"object_name": "suggestions/abc/failure.png"}])
    failing.delete.assert_called_once()  # type: ignore[attr-defined]


def test_iter_download_yields_exact_bytes(store: tuple[SuggestionImageStore, BucketSpy]) -> None:
    service, bucket = store
    bucket.blob("private/foo.png").content = b"a" * 20
    assert b"".join(service.iter_download("private/foo.png", chunk_size=7)) == b"a" * 20


@pytest.mark.parametrize(
    ("original", "expected"),
    [("../../danger.png", "danger.png"), ("hello world.png", "hello-world.png"),
     ("", "image.png")],
)
def test_safe_filename(original: str, expected: str) -> None:
    assert SuggestionImageStore._safe_filename(original, ".png") == expected
