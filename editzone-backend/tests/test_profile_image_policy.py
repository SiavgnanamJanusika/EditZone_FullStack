from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bson import ObjectId
from fastapi import HTTPException
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image

from app.core.security import get_current_user
from app.routers.upload_router import _media_available, _normalize_mime, _validate_profile_image, router


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("image/jpeg", "image/jpeg"),
        (" IMAGE/JPEG ", "image/jpeg"),
        ("image/jpeg; charset=binary", "image/jpeg"),
        ("image/png; boundary=ignored-part-parameter", "image/png"),
    ],
)
def test_multipart_image_mime_is_normalized(raw, expected):
    assert _normalize_mime(raw) == expected


@pytest.mark.parametrize(
    ("format_name", "mime"),
    [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")],
)
def test_real_profile_images_are_decoded_and_accepted(format_name, mime):
    stream = BytesIO()
    Image.new("RGB", (16, 16), "navy").save(stream, format=format_name)
    assert _validate_profile_image(stream, mime) == mime


def test_real_webp_with_generic_browser_mime_is_detected_safely():
    stream = BytesIO()
    Image.new("RGB", (16, 16), "navy").save(stream, format="WEBP")
    assert _validate_profile_image(stream, "") == "image/webp"


def test_fake_jpeg_is_rejected_even_with_an_image_mime():
    with pytest.raises(HTTPException) as exc:
        _validate_profile_image(BytesIO(b"#!/bin/sh\necho unsafe\n"), "image/jpeg")
    assert exc.value.status_code == 415


def test_profile_mime_must_match_decoded_format():
    stream = BytesIO()
    Image.new("RGB", (8, 8), "red").save(stream, format="PNG")
    with pytest.raises(HTTPException) as exc:
        _validate_profile_image(stream, "image/jpeg")
    assert exc.value.status_code == 415


def test_only_explicitly_validated_profile_images_bypass_scan_gate():
    assert _media_available({
        "purpose": "profile_picture", "state": "available",
        "security_policy": "validated_profile_image",
    })
    assert not _media_available({"purpose": "profile_picture", "state": "available"})
    assert not _media_available({
        "purpose": "chat_attachment", "state": "available",
        "security_policy": "validated_profile_image",
    })


def test_profile_branch_returns_before_malware_queue_and_other_media_still_queues():
    source = (Path(__file__).parents[1] / "app/routers/upload_router.py").read_text()
    profile_branch = source.index("if profile_image:", source.index("file_url ="))
    profile_return = source.index("return {\"success\": True", profile_branch)
    scan_queue = source.index("background_tasks.add_task(_scan_gridfs_background", profile_return)
    assert profile_branch < profile_return < scan_queue
    assert "elif not profile_image:" in source
    assert 'metadata["scan_status"] = "safe" if scanner_bypassed else "pending"' in source


def test_portfolio_images_use_decoded_reencoded_ready_path():
    source = (Path(__file__).parents[1] / "app/routers/upload_router.py").read_text()
    assert 'purpose in {"editor_status", "editor_portfolio"}' in source
    assert '"security_policy": "decoded_reencoded_image"' in source


class _UploadStream:
    def __init__(self):
        self._id = ObjectId()
        self.contents = bytearray()

    async def write(self, chunk):
        self.contents.extend(chunk)

    async def close(self):
        return None

    async def abort(self):
        return None


@pytest.mark.parametrize(
    ("filename", "format_name", "mime"),
    [
        ("photo.jpg", "JPEG", "image/jpeg"),
        ("photo.jpeg", "JPEG", "image/jpeg"),
        ("photo.png", "PNG", "image/png"),
        ("photo.webp", "WEBP", "image/webp"),
        ("generic-webp.webp", "WEBP", "application/octet-stream"),
    ],
)
@pytest.mark.asyncio
async def test_profile_multipart_endpoint_accepts_supported_real_images(filename, format_name, mime):
    image_bytes = BytesIO()
    Image.new("RGB", (20, 20), "green").save(image_bytes, format=format_name)
    stream = _UploadStream()
    bucket = MagicMock()
    bucket.open_upload_stream.return_value = stream
    files_collection = MagicMock()
    files_collection.update_one = AsyncMock()
    database = MagicMock()
    database.__getitem__.return_value = files_collection
    user_id = ObjectId()
    app = FastAPI()
    app.include_router(router)
    async def current_user():
        return {"_id": user_id, "role": "user"}
    app.dependency_overrides[get_current_user] = current_user

    with (
        patch("app.routers.upload_router.uploads_bucket", bucket),
        patch("app.routers.upload_router.db", database),
        patch("app.routers.upload_router.users_col.update_one", AsyncMock(return_value=MagicMock(matched_count=1))),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/uploads",
                data={"purpose": "profile_picture"},
                files={"file": (filename, image_bytes.getvalue(), mime)},
            )

    assert response.status_code == 200, response.text
    assert response.json()["profile_image_url"].startswith("/api/v1/uploads/file/")
    assert bytes(stream.contents) == image_bytes.getvalue()


@pytest.mark.asyncio
async def test_profile_multipart_endpoint_rejects_fake_jpeg_with_useful_415():
    stream = _UploadStream()
    bucket = MagicMock()
    bucket.open_upload_stream.return_value = stream
    app = FastAPI()
    app.include_router(router)
    async def current_user():
        return {"_id": ObjectId(), "role": "user"}
    app.dependency_overrides[get_current_user] = current_user
    with patch("app.routers.upload_router.uploads_bucket", bucket):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/uploads",
                data={"purpose": "profile_picture"},
                files={"file": ("fake-image.jpg", b"#!/bin/sh\necho not-an-image", "image/jpeg")},
            )
    assert response.status_code == 400
    assert response.json()["detail"] == "File content does not match the declared file type"


@pytest.mark.asyncio
async def test_profile_multipart_endpoint_rejects_pdf_with_415():
    app = FastAPI()
    app.include_router(router)
    async def current_user():
        return {"_id": ObjectId(), "role": "user"}
    app.dependency_overrides[get_current_user] = current_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/uploads",
            data={"purpose": "profile_picture"},
            files={"file": ("document.pdf", b"%PDF-1.7\n", "application/pdf")},
        )
    assert response.status_code == 415
    assert response.json()["detail"] == "Unsupported image format. Use JPG, PNG or WEBP."
