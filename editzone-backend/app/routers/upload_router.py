import uuid
import hashlib
import asyncio
import logging
import tempfile
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from bson import ObjectId
from fastapi import APIRouter, BackgroundTasks, UploadFile, File, Form, HTTPException, Depends, Query, Request
from fastapi.responses import RedirectResponse, StreamingResponse
import boto3
import jwt
from PIL import Image, UnidentifiedImageError
from pymongo import ReturnDocument

from app.config import settings
from app.core.security import get_current_user
from app.core.accounts import ACTIVE_ACCOUNT_FILTER
from app.core.utils import now_utc, oid, serialize_list
from app.core.validators import DANGEROUS_FILE_EXTENSIONS, get_file_category, upload_limit_bytes
from app.db.mongodb import (
    db, uploads_bucket, requests_col, messages_col, media_access_logs_col,
    media_agreements_col, media_reports_col, multipart_uploads_col,
    deliveries_col, users_col, editors_col,
)
from app.schemas.schemas import MediaPolicyUpdate, MediaReportBody, MultipartUploadInit, MultipartUploadComplete
from app.services.malware_scanner import ScannerUnavailable, scan_gridfs_upload, scan_pending_s3_uploads
from app.services.image_sanitizer import sanitize_image

router = APIRouter(prefix="/api/v1/uploads", tags=["Uploads"])
media_router = APIRouter(prefix="/api/v1/media", tags=["Media Processing"])
logger = logging.getLogger(__name__)


def _public_media_state(scan_status: str | None, media_state: str | None = None) -> str:
    if media_state in {"uploading", "uploaded", "scanning", "ready", "rejected", "failed", "cancelled"}:
        return media_state
    return {
        "pending": "uploaded", "scanning": "scanning", "safe": "ready",
        "infected": "rejected", "rejected": "rejected", "scan_failed": "failed",
        "cancelled": "cancelled",
    }.get(scan_status or "", "uploaded")


def _media_status_payload(media_id: str, scan_status: str | None, *, media_state: str | None = None, error_code: str | None = None, url: str | None = None):
    status = _public_media_state(scan_status, media_state)
    messages = {
        "uploading": "Uploading media…", "uploaded": "Upload completed. Processing media…",
        "scanning": "Upload completed. Processing media…", "ready": "Media is ready.",
        "rejected": "Media was rejected by security validation.",
        "failed": "The media scanner is temporarily unavailable. Please retry.",
        "cancelled": "Upload was cancelled.",
    }
    return {
        "media_id": media_id, "status": status,
        "progress": 100 if status != "uploading" else 0, "message": messages[status],
        "url": url if status == "ready" else None, "error_code": error_code,
        "retryable": status == "failed" and error_code != "stream_limit_exceeded",
        # Backward compatibility for existing upload callers.
        "upload_id": media_id, "scan_status": scan_status,
    }

PROFILE_IMAGE_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
PROFILE_IMAGE_MIME_TYPES = frozenset(PROFILE_IMAGE_FORMATS.values())
GENERIC_BINARY_MIME_TYPES = {"", "application/octet-stream"}


def _normalize_mime(value: str | None) -> str:
    """Return the canonical media type without optional MIME parameters."""
    return (value or "").split(";", 1)[0].strip().lower()


def _media_available(metadata: dict) -> bool:
    return metadata.get("scan_status") == "safe" or (
        metadata.get("purpose") == "profile_picture"
        and metadata.get("state") == "available"
        and metadata.get("security_policy") == "validated_profile_image"
    )


def _validate_profile_image(stream, content_type: str) -> str:
    """Decode the complete image and require its real format to match its MIME."""
    stream.seek(0)
    try:
        with Image.open(stream) as image:
            detected_mime = PROFILE_IMAGE_FORMATS.get(image.format)
            if not detected_mime or (content_type and detected_mime != content_type) or image.width * image.height > 100_000_000:
                raise HTTPException(status_code=415, detail="Unsupported or excessively large profile image")
            image.verify()
            return detected_mime
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(status_code=415, detail="Profile image content could not be decoded") from exc


async def _scan_gridfs_background(upload_id):
    # The API-side task supplements the durable scheduler so a correctly
    # configured single-process deployment does not leave an upload pending
    # merely because app.worker was not launched. Database locks keep this safe
    # when the scheduler is also running.
    for attempt in range(settings.MEDIA_SCAN_MAX_ATTEMPTS):
        try:
            status = await scan_gridfs_upload(upload_id)
        except ScannerUnavailable:
            status = "pending"
        if status != "pending":
            return status
        if attempt + 1 < settings.MEDIA_SCAN_MAX_ATTEMPTS:
            await asyncio.sleep(10)
    logger.warning("Immediate media scan exhausted upload_id=%s scanner_unavailable=true", upload_id)
    return "scan_failed"


async def _scan_s3_background(upload_id: str):
    for attempt in range(settings.MEDIA_SCAN_MAX_ATTEMPTS):
        await scan_pending_s3_uploads(max(2, settings.MEDIA_SCAN_MAX_ATTEMPTS))
        record = await multipart_uploads_col.find_one({"upload_id": upload_id}, {"scan_status": 1})
        status = (record or {}).get("scan_status", "missing")
        if status not in {"pending", "scanning"}:
            return status
        if attempt + 1 < settings.MEDIA_SCAN_MAX_ATTEMPTS:
            await asyncio.sleep(10)
    return "scan_failed"

ALLOWED_MIME_PREFIXES = {
    "image": ("image/",),
    "video": ("video/",),
    "document": ("application/pdf", "application/msword", "application/vnd.openxmlformats-officedocument", "text/plain"),
    "archive": ("application/zip", "application/x-rar", "application/vnd.rar", "application/x-7z-compressed"),
    "audio": ("audio/",),
}
CHAT_VIDEO_MIME_TYPES = {"video/mp4", "video/webm", "video/quicktime"}
CHAT_IMAGE_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
}
PURPOSES = {
    "profile_picture", "editor_portfolio", "chat_attachment", "project_source_file",
    "project_reference_file", "final_delivery", "identity_document", "dispute_evidence", "editor_status",
}
PROJECT_PURPOSES = PURPOSES - {"profile_picture", "editor_portfolio", "identity_document", "editor_status"}


def _s3_client():
    return boto3.client(
        "s3", region_name=settings.AWS_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
    )


def _matches_magic(category: str, header: bytes) -> bool:
    signatures = {
        "image": (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a", b"RIFF"),
        "document": (b"%PDF-", b"PK\x03\x04", b"\xd0\xcf\x11\xe0"),
        "archive": (b"PK\x03\x04", b"Rar!\x1a\x07", b"7z\xbc\xaf\x27\x1c"),
        "audio": (b"ID3", b"OggS", b"RIFF", b"\x1aE\xdf\xa3"),
        "video": (b"\x1aE\xdf\xa3", b"RIFF"),
    }
    if category == "video" and len(header) >= 12 and header[4:8] == b"ftyp":
        return True
    # Safari records voice messages in an ISO-BMFF container (audio/mp4).
    # The ftyp marker identifies that container; the trusted MIME/category and
    # chat-message metadata keep it classified as audio rather than video.
    if category == "audio" and len(header) >= 12 and header[4:8] == b"ftyp":
        return True
    if category == "audio" and header[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return True
    if category == "document" and header and all(byte in b"\t\n\r" or 32 <= byte < 127 for byte in header[:512]):
        return True
    if category == "image" and header.startswith(b"RIFF"):
        return len(header) >= 12 and header[8:12] == b"WEBP"
    return any(header.startswith(signature) for signature in signatures.get(category, ()))

async def _project(request_id: str, user: dict) -> dict:
    project = await requests_col.find_one({"_id": oid(request_id)})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    members = (project["user_id"], project["editor_user_id"])
    if user["_id"] not in members and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not authorized for this project")
    if project.get("media_access_revoked_at") and user.get("role") != "admin":
        raise HTTPException(status_code=410, detail="Project media access was revoked after payment completion")
    return project


async def _accepted(request_id: str, user: dict) -> bool:
    if user.get("role") in ("user", "admin"):
        return True
    return bool(await media_agreements_col.find_one({"request_id": request_id, "user_id": user["_id"]}))


async def _authorize_final_delivery(upload_id: str, request_id: str, user: dict):
    """Final video is private until the canonical delivery is released."""
    delivery = await deliveries_col.find_one({"project_id": request_id, "upload_id": upload_id})
    if not delivery:
        raise HTTPException(status_code=403, detail="Final delivery has not been submitted")
    if user.get("role") == "admin" or user["_id"] == delivery.get("editor_id"):
        return delivery
    if user["_id"] != delivery.get("client_id"):
        raise HTTPException(status_code=403, detail="Not authorized for this final delivery")
    if delivery.get("delivery_status") != "RELEASED":
        raise HTTPException(status_code=403, detail="Final video is waiting for admin approval")
    return delivery


async def _log(record: dict, user: dict, action: str, request: Request):
    await media_access_logs_col.insert_one({
        "request_id": record.get("metadata", {}).get("request_id"),
        "filename": record["filename"], "user_id": user["_id"], "role": user.get("role"),
        "action": action, "ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent", "")[:300], "created_at": now_utc(),
    })


@router.post("")
async def upload_file(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    request_id: str | None = Form(default=None),
    purpose: str = Form(...),
    category: str | None = Form(default=None),
    view_once: bool = Form(default=False),
    current_user: dict = Depends(get_current_user),
):
    filename = file.filename or ""
    request_content_type = request.headers.get("content-type", "")
    file_content_type = _normalize_mime(file.content_type)

    def reject_profile_415(reason: str, detail: str):
        logger.warning(
            "PROFILE_UPLOAD_REJECTED_415 endpoint=%s request_content_type=%s filename=%s "
            "file_content_type=%s file_size=%s reason=%s",
            request.url.path, request_content_type[:200], filename[:255],
            file_content_type or "missing", file.size, reason,
        )
        raise HTTPException(status_code=415, detail=detail)

    if purpose == "profile_picture":
        logger.info(
            "PROFILE_UPLOAD_REQUEST endpoint=%s request_content_type=%s filename=%s "
            "file_content_type=%s file_size=%s user_id=%s",
            request.url.path, request_content_type[:200], filename[:255],
            file_content_type or "missing", file.size, current_user["_id"],
        )
    if purpose not in PURPOSES:
        raise HTTPException(status_code=422, detail="An explicit supported upload purpose is required")
    if purpose == "editor_status" and current_user.get("role") != "editor":
        raise HTTPException(status_code=403, detail="Only editors can upload status media")
    if "\x00" in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if len(filename.rsplit(".", 1)[0].split(".")) > 1:
        raise HTTPException(status_code=400, detail="Double-extension filenames are not allowed")
    if purpose in PROJECT_PURPOSES and not request_id:
        raise HTTPException(status_code=422, detail="This upload purpose requires a project")
    extension = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension in DANGEROUS_FILE_EXTENSIONS:
        if purpose == "profile_picture":
            reject_profile_415("dangerous_extension", "Dangerous executable file types are not allowed")
        raise HTTPException(status_code=415, detail="Dangerous executable file types are not allowed")
    actual_category = get_file_category(filename)
    # MediaRecorder commonly produces audio/webm. The shared .webm extension is
    # disambiguated by its MIME plus EBML signature below, never by the claim alone.
    if category == "voice" and extension == ".webm" and (file.content_type or "").lower().startswith("audio/webm"):
        actual_category = "audio"
    if not actual_category:
        if purpose == "profile_picture":
            reject_profile_415("unsupported_extension", "Unsupported image format. Use JPG, PNG or WEBP.")
        raise HTTPException(status_code=415, detail="Unsupported file type")
    is_voice = category == "voice"
    if category and category not in {actual_category, "zip", "voice", "viewOnceVideo"}:
        if purpose == "profile_picture":
            reject_profile_415("category_mismatch", "Profile image category does not match its file extension")
        raise HTTPException(status_code=415, detail="File category does not match its content")
    if is_voice and actual_category != "audio":
        raise HTTPException(status_code=415, detail="Voice messages must contain supported browser audio")
    category = actual_category
    if purpose == "profile_picture" and (category != "image" or extension not in CHAT_IMAGE_TYPES):
        reject_profile_415("profile_extension_not_allowed", "Unsupported image format. Use JPG, PNG or WEBP.")
    if purpose == "editor_status" and category not in {"image", "video"}:
        raise HTTPException(status_code=415, detail="Statuses must be JPG, JPEG, PNG, WebP, MP4, MOV, or WebM")
    if purpose == "editor_portfolio" and category not in {"image", "video"}:
        raise HTTPException(status_code=415, detail="Reels must be JPG, JPEG, PNG, WebP, MP4, or WebM")
    content_type = file_content_type
    public_image_generic_mime = purpose in {"profile_picture", "editor_status", "editor_portfolio"} and category == "image" and content_type in GENERIC_BINARY_MIME_TYPES
    profile_generic_mime = purpose == "profile_picture" and public_image_generic_mime
    if content_type and not public_image_generic_mime and not any(content_type.startswith(prefix) for prefix in ALLOWED_MIME_PREFIXES[category]):
        if purpose == "profile_picture":
            reject_profile_415("mime_category_mismatch", "Uploaded file MIME does not match an image")
        raise HTTPException(status_code=415, detail="File content type does not match its extension")
    if purpose == "profile_picture" and content_type not in PROFILE_IMAGE_MIME_TYPES and not profile_generic_mime:
        reject_profile_415("profile_mime_not_allowed", "Unsupported image format. Use JPG, PNG or WEBP.")
    if purpose == "chat_attachment" and category == "video" and content_type not in CHAT_VIDEO_MIME_TYPES:
        raise HTTPException(status_code=415, detail="Chat videos must be MP4, WebM, or MOV")
    if purpose == "chat_attachment" and category == "image":
        expected_mime = CHAT_IMAGE_TYPES.get(extension)
        if not expected_mime or content_type != expected_mime:
            raise HTTPException(status_code=415, detail="Chat images must be JPG, JPEG, PNG, or WebP")
    if purpose == "editor_status":
        allowed = CHAT_VIDEO_MIME_TYPES if category == "video" else set(CHAT_IMAGE_TYPES.values())
        if content_type not in allowed and not public_image_generic_mime:
            raise HTTPException(status_code=415, detail="Unsupported status media type")
    if purpose == "editor_portfolio":
        allowed = CHAT_VIDEO_MIME_TYPES if category == "video" else set(CHAT_IMAGE_TYPES.values())
        if content_type not in allowed and not public_image_generic_mime:
            raise HTTPException(status_code=415, detail="Unsupported reel media type")

    if request_id:
        project = await _project(request_id, current_user)
        if project["status"] not in ("accepted", "in_progress", "overdue", "revision_requested", "admin_review", "delivered", "cancel_requested", "disputed"):
            raise HTTPException(status_code=409, detail="Project media is unavailable in its current state")
        if not await _accepted(request_id, current_user):
            raise HTTPException(status_code=403, detail="Accept the editor media agreement before uploading files")
    logger.info(
        "Starting upload user=%s request=%s purpose=%s category=%s voice=%s view_once=%s",
        current_user["_id"], request_id, purpose, category, is_voice, view_once,
    )
    logger.info(
        "MEDIA_UPLOAD_STARTED user_id=%s room_id=%s purpose=%s media_type=%s mime=%s declared_size=%s",
        current_user["_id"], request_id, purpose, "audio" if is_voice else category,
        content_type, file.size,
    )
    if purpose == "chat_attachment":
        logger.info(
            "CHAT_MEDIA_UPLOAD_STARTED user_id=%s room_id=%s type=%s mime=%s declared_size=%s",
            current_user["_id"], request_id, "audio" if is_voice else category,
            content_type, file.size,
        )
    ext = filename.rsplit(".", 1)[-1].lower()
    header = await file.read(8192)
    if not header or not _matches_magic(category, header):
        raise HTTPException(status_code=400, detail="File content does not match the declared file type")
    profile_image = purpose == "profile_picture"
    # Status images are normalized before publishing. Profile images retain
    # their supported source encoding after complete Pillow decode/verify so
    # existing JPEG/PNG/WebP records and clients remain compatible.
    sanitized_public_image = category == "image" and purpose in {"editor_status", "editor_portfolio"}
    normalized_image = None
    if sanitized_public_image:
        source = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
        source.write(header)
        source_total = len(header)
        source_limit = upload_limit_bytes(category, purpose=purpose)
        while chunk := await file.read(1024 * 1024):
            source_total += len(chunk)
            if source_total > source_limit:
                source.close()
                raise HTTPException(status_code=413, detail=f"Image exceeds the {source_limit // 1048576} MB limit.")
            source.write(chunk)
        normalized_image, content_type, ext = sanitize_image(
            source,
            max_dimension=settings.PROFILE_IMAGE_MAX_DIMENSION if profile_image else settings.STATUS_IMAGE_MAX_DIMENSION,
            max_pixels=settings.IMAGE_MAX_PIXELS,
        )
        source.close()
        header = normalized_image.read(8192)
    unique_name = f"{uuid.uuid4().hex}.{ext}"
    metadata = {
            "content_type": content_type or "application/octet-stream",
            "category": category,
            "original_name": filename,
            "owner_id": current_user["_id"],
            "request_id": request_id,
            "purpose": purpose,
            "state": "uploading" if profile_image else "quarantined",
            "created_at": now_utc(),
            "updated_at": now_utc(),
            "scan_attempts": 0,
            "safe_filename": unique_name,
            "declared_size": file.size,
            "voice": is_voice,
            "view_once": view_once,
        }
    scanner_bypassed = not settings.MEDIA_SCANNER_ENABLED
    if sanitized_public_image:
        metadata.update({
            "scan_status": "safe", "media_state": "ready", "state": "safe",
            "security_policy": "decoded_reencoded_image",
            "sanitized_at": now_utc(),
        })
    elif not profile_image:
        metadata["scan_status"] = "safe" if scanner_bypassed else "pending"
        metadata["media_state"] = "ready" if scanner_bypassed else "uploaded"
        if scanner_bypassed:
            metadata["state"] = "safe"
    upload_stream = uploads_bucket.open_upload_stream(
        unique_name,
        metadata=metadata,
    )
    profile_spool = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) if profile_image else None
    stream_closed = False
    total = len(header)
    limit_bytes = upload_limit_bytes(category, purpose=purpose, voice=is_voice, view_once=view_once)
    limit_label = "Voice message" if is_voice else ("Chat image" if purpose == "chat_attachment" and category == "image" else category.title())
    if total > limit_bytes:
        await upload_stream.abort()
        raise HTTPException(status_code=413, detail=f"{limit_label} exceeds the {limit_bytes // 1048576} MB limit.")
    try:
        await upload_stream.write(header)
        if profile_spool:
            profile_spool.write(header)
        chunk_source = normalized_image if normalized_image else file
        while chunk := await (file.read(1024 * 1024) if chunk_source is file else asyncio.to_thread(chunk_source.read, 1024 * 1024)):
            total += len(chunk)
            if total > limit_bytes:
                raise HTTPException(status_code=413, detail=f"{limit_label} exceeds the {limit_bytes // 1048576} MB limit.")
            await upload_stream.write(chunk)
            if profile_spool:
                profile_spool.write(chunk)
        if total == 0:
            raise HTTPException(status_code=400, detail="File cannot be empty")
        if profile_spool:
            try:
                detected_profile_mime = _validate_profile_image(
                    profile_spool,
                    "" if profile_generic_mime else content_type,
                )
                content_type = detected_profile_mime
                metadata["content_type"] = detected_profile_mime
            except HTTPException as exc:
                if exc.status_code == 415:
                    reject_profile_415("decoded_image_mismatch", str(exc.detail))
                raise
        await upload_stream.close()
        stream_closed = True
        update_fields = {"metadata.size": total}
        if profile_image:
            update_fields.update({
                "metadata.content_type": content_type,
                "metadata.scan_status": "safe",
                "metadata.state": "available",
                "metadata.security_policy": "validated_profile_image",
                "metadata.validated_at": now_utc(),
            })
        await db["uploads.files"].update_one({"_id": upload_stream._id}, {"$set": update_fields})
    except Exception:
        logger.exception(
            "MEDIA_UPLOAD_FAILED user_id=%s request_id=%s purpose=%s mime=%s size=%s failure_layer=gridfs reason=storage_or_validation_error",
            current_user["_id"], request_id, purpose, content_type, total,
        )
        if not stream_closed:
            await upload_stream.abort()
        raise
    finally:
        if profile_spool:
            profile_spool.close()
        if normalized_image:
            normalized_image.close()
    size_mb = total / (1024 * 1024)

    logger.info(
        "Upload stored upload_id=%s user=%s request=%s bytes=%s security_state=%s",
        upload_stream._id, current_user["_id"], request_id, total,
        "validated_profile_image" if profile_image else ("scan_skipped" if scanner_bypassed else "pending_scan"),
    )
    logger.info(
        "MEDIA_SCAN enabled=%s status=%s media_id=%s",
        settings.MEDIA_SCANNER_ENABLED,
        "skipped" if scanner_bypassed else ("safe" if sanitized_public_image else "pending"),
        upload_stream._id,
    )
    logger.info("MEDIA_UPLOAD_COMPLETE media_id=%s purpose=%s size=%s mime=%s", upload_stream._id, purpose, total, content_type)
    file_url = f"/api/v1/uploads/file/{unique_name}"
    if profile_image:
        updated = await users_col.update_one({"_id": current_user["_id"]}, {"$set": {"profile_picture": file_url, "updated_at": now_utc()}})
        if not updated.matched_count:
            raise HTTPException(status_code=404, detail="Profile account no longer exists")
        if current_user.get("role") == "editor":
            await editors_col.update_one({"user_id": current_user["_id"]}, {"$set": {"profile_picture": file_url}})
        return {"success": True, "id": str(upload_stream._id), "media_type": "image", "url": file_url,
                "thumbnail_url": None, "mime_type": content_type, "size": total, "duration": None,
                "created_at": metadata["created_at"], "profile_image_url": file_url,
                "profile_picture": file_url, "file_url": file_url, "upload_id": str(upload_stream._id),
                "message": "Profile image updated successfully"}
    # Start scanning immediately in the API process so chat uploads do not
    # depend solely on the periodic worker. The scanner's atomic lock makes
    # this safe when the worker sees the same object concurrently.
    if not sanitized_public_image and settings.MEDIA_SCANNER_ENABLED:
        background_tasks.add_task(_scan_gridfs_background, upload_stream._id)
        logger.info("MEDIA_SCAN_QUEUED media_id=%s purpose=%s", upload_stream._id, purpose)
    return {
        "success": True,
        "message": "File uploaded successfully",
        "id": str(upload_stream._id),
        "media_type": "audio" if is_voice else category,
        "url": file_url,
        "thumbnail_url": None,
        "duration": None,
        "created_at": metadata["created_at"],
        "file_url": file_url,
        "secure_url": file_url,
        "file_type": category,
        "original_name": filename,
        "original_filename": filename,
        "filename": unique_name,
        "content_type": content_type or "application/octet-stream",
        "size_mb": round(size_mb, 2),
        "upload_id": str(upload_stream._id),
        "file_id": str(upload_stream._id),
        "mime_type": content_type or "application/octet-stream",
        "file_name": filename,
        "size": total,
        "scan_status": "safe" if sanitized_public_image or scanner_bypassed else "pending",
        "status": "ready" if sanitized_public_image or scanner_bypassed else "uploaded",
        "status_url": f"/api/v1/media/{upload_stream._id}/status",
        "storage": "mongodb_gridfs",
    }


@router.get("/status/{upload_id}")
async def upload_scan_status(upload_id: str, current_user: dict = Depends(get_current_user)):
    multipart = await multipart_uploads_col.find_one({"upload_id": upload_id})
    if multipart:
        if multipart.get("owner_id") != current_user["_id"] and current_user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Not authorized to inspect this upload")
        return {"media_id": upload_id, "upload_id": upload_id, "upload_status": "uploaded", "media_state": multipart.get("media_state"), "scan_status": multipart.get("scan_status", "pending"), "scan_error": multipart.get("scan_error"), "scan_attempts": multipart.get("scan_attempts", 0), "media_url": f"/api/v1/uploads/s3/file/{upload_id}" if multipart.get("scan_status") == "safe" else None}
    if not ObjectId.is_valid(upload_id):
        raise HTTPException(status_code=400, detail="Invalid upload ID")
    record = await db["uploads.files"].find_one({"_id": ObjectId(upload_id)})
    if not record:
        raise HTTPException(status_code=404, detail="Upload not found")
    metadata = record.get("metadata") or {}
    if metadata.get("owner_id") != current_user["_id"] and current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not authorized to inspect this upload")
    # Status polling must remain read-only. The background task/worker owns the
    # scan lifecycle; trying to scan here creates a race where that worker can
    # reset an unavailable scan to ``pending`` between these reads, causing a
    # ScannerUnavailable exception (and a 500 response) from a harmless GET.
    return {"media_id": upload_id, "upload_id": upload_id, "upload_status": "uploaded", "media_state": metadata.get("media_state"), "scan_status": metadata.get("scan_status", "pending"), "scan_error": metadata.get("scan_error") or metadata.get("scan_error_code"), "scan_attempts": metadata.get("scan_attempts", 0), "media_url": f'/api/v1/uploads/file/{record["filename"]}' if metadata.get("scan_status") == "safe" else None}


@media_router.get("/{media_id}/status")
async def media_processing_status(media_id: str, current_user: dict = Depends(get_current_user)):
    legacy = await upload_scan_status(media_id, current_user)
    return _media_status_payload(
        media_id, legacy.get("scan_status"),
        media_state=legacy.get("media_state"), error_code=legacy.get("scan_error"),
        url=legacy.get("media_url"),
    )


@media_router.delete("/{media_id}")
async def cancel_media_processing(media_id: str, current_user: dict = Depends(get_current_user)):
    multipart = await multipart_uploads_col.find_one({"upload_id": media_id})
    if multipart:
        if multipart.get("owner_id") != current_user["_id"] and current_user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Not authorized to cancel this upload")
        if multipart.get("scan_status") == "safe":
            raise HTTPException(status_code=409, detail="Ready media cannot be cancelled")
        client = _s3_client()
        if multipart.get("state") == "uploading":
            await asyncio.to_thread(client.abort_multipart_upload, Bucket=multipart["bucket"], Key=multipart["key"], UploadId=media_id)
        else:
            await asyncio.to_thread(client.delete_object, Bucket=multipart["bucket"], Key=multipart["key"])
        await multipart_uploads_col.update_one({"_id": multipart["_id"]}, {"$set": {"state": "cancelled", "media_state": "cancelled", "scan_status": "cancelled", "updated_at": now_utc()}})
        return {"media_id": media_id, "status": "cancelled", "message": "Upload was cancelled."}
    if not ObjectId.is_valid(media_id):
        raise HTTPException(status_code=400, detail="Invalid media ID")
    record = await db["uploads.files"].find_one({"_id": ObjectId(media_id)})
    if not record:
        return {"media_id": media_id, "status": "cancelled", "message": "Upload was cancelled."}
    metadata = record.get("metadata") or {}
    if metadata.get("owner_id") != current_user["_id"] and current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not authorized to cancel this upload")
    if metadata.get("scan_status") == "safe":
        raise HTTPException(status_code=409, detail="Ready media cannot be cancelled")
    await db["uploads.files"].update_one({"_id": record["_id"]}, {"$set": {"metadata.state": "cancelled", "metadata.media_state": "cancelled", "metadata.scan_status": "cancelled", "metadata.updated_at": now_utc()}})
    await uploads_bucket.delete(record["_id"])
    return {"media_id": media_id, "status": "cancelled", "message": "Upload was cancelled."}


@router.post("/status/{upload_id}/retry")
async def retry_upload_scan(upload_id: str, background_tasks: BackgroundTasks, current_user: dict = Depends(get_current_user)):
    multipart = await multipart_uploads_col.find_one({"upload_id": upload_id})
    if multipart:
        if multipart.get("owner_id") != current_user["_id"] and current_user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Not authorized to retry this scan")
        if multipart.get("scan_status") == "safe":
            return {"upload_id": upload_id, "scan_status": "safe"}
        if multipart.get("scan_status") != "scan_failed":
            raise HTTPException(status_code=409, detail="Security scan is already pending or running")
        cutoff = now_utc() - timedelta(seconds=10)
        updated = await multipart_uploads_col.update_one({"_id": multipart["_id"], "scan_status": "scan_failed", "$or": [{"scan_retry_requested_at": {"$exists": False}}, {"scan_retry_requested_at": {"$lte": cutoff}}]}, {"$set": {"scan_status": "pending", "media_state": "uploaded", "scan_attempts": 0, "scan_retry_requested_at": now_utc(), "updated_at": now_utc()}, "$unset": {"scan_error": "", "scan_error_code": "", "scan_completed_at": "", "scan_failed_at": ""}})
        if not updated.modified_count:
            raise HTTPException(status_code=429, detail="Wait 10 seconds before retrying the security scan")
        background_tasks.add_task(_scan_s3_background, upload_id)
        return {"upload_id": upload_id, "scan_status": "pending"}
    if not ObjectId.is_valid(upload_id):
        raise HTTPException(status_code=400, detail="Invalid upload ID")
    record = await db["uploads.files"].find_one({"_id": ObjectId(upload_id)})
    if not record:
        raise HTTPException(status_code=404, detail="Upload not found")
    metadata = record.get("metadata") or {}
    if metadata.get("owner_id") != current_user["_id"] and current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not authorized to retry this scan")
    if metadata.get("scan_status") == "safe":
        return {"upload_id": upload_id, "scan_status": "safe"}
    if metadata.get("scan_status") != "scan_failed":
        raise HTTPException(status_code=409, detail="Security scan is already pending or running")
    cutoff = now_utc() - timedelta(seconds=10)
    updated = await db["uploads.files"].update_one({"_id": record["_id"], "metadata.scan_status": "scan_failed", "$or": [{"metadata.scan_retry_requested_at": {"$exists": False}}, {"metadata.scan_retry_requested_at": {"$lte": cutoff}}]}, {"$set": {"metadata.scan_status": "pending", "metadata.media_state": "uploaded", "metadata.scan_attempts": 0, "metadata.scan_retry_requested_at": now_utc(), "metadata.updated_at": now_utc()}, "$unset": {"metadata.scan_error": "", "metadata.scan_error_code": "", "metadata.scan_completed_at": "", "metadata.scan_failed_at": ""}})
    if not updated.modified_count:
        raise HTTPException(status_code=429, detail="Wait 10 seconds before retrying the security scan")
    background_tasks.add_task(_scan_gridfs_background, record["_id"])
    return {"upload_id": upload_id, "scan_status": "pending"}


@router.post("/multipart/initiate", status_code=201)
async def initiate_multipart_upload(body: MultipartUploadInit, current_user: dict = Depends(get_current_user)):
    if not settings.AWS_S3_BUCKET:
        raise HTTPException(status_code=503, detail="Private S3 upload storage is not configured")
    upload_limit = upload_limit_bytes("video", purpose=body.purpose, view_once=body.view_once)
    if body.size > upload_limit:
        raise HTTPException(status_code=413, detail="Maximum attachment size is 100 MB." if body.purpose == "chat_attachment" else f"Video size must be {upload_limit // 1048576} MB or less")
    project = await _project(body.request_id, current_user)
    if body.purpose == "final_delivery" and (
        current_user.get("role") != "editor" or project.get("editor_user_id") != current_user["_id"]
    ):
        raise HTTPException(status_code=403, detail="Only the assigned editor can upload the final output")
    if project["status"] not in ("accepted", "in_progress", "overdue", "revision_requested", "admin_review", "delivered", "cancel_requested", "disputed"):
        raise HTTPException(status_code=409, detail="Project media is unavailable in its current state")
    if not await _accepted(body.request_id, current_user):
        raise HTTPException(status_code=403, detail="Accept the editor media agreement before uploading files")
    category = get_file_category(body.filename)
    if not category or category != "video":
        raise HTTPException(status_code=415, detail="Direct multipart upload is limited to supported video files")
    if body.content_type.lower() not in CHAT_VIDEO_MIME_TYPES:
        raise HTTPException(status_code=415, detail="Chat videos must be MP4, WebM, or MOV")
    if "\x00" in body.filename or "/" in body.filename or "\\" in body.filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    key = f"{settings.S3_UPLOAD_PREFIX.strip('/')}/{body.request_id}/{uuid.uuid4().hex}.{body.filename.rsplit('.', 1)[-1].lower()}"
    client = _s3_client()
    created = await asyncio.to_thread(
        client.create_multipart_upload,
        Bucket=settings.AWS_S3_BUCKET, Key=key, ContentType=body.content_type,
        ServerSideEncryption="AES256",
        Metadata={"owner-id": str(current_user["_id"]), "request-id": body.request_id, "purpose": body.purpose},
    )
    upload_id = created["UploadId"]
    part_size = max(5 * 1024 * 1024, min(25 * 1024 * 1024, (body.size + 9999) // 10000))
    part_count = (body.size + part_size - 1) // part_size
    expires_at = now_utc() + timedelta(hours=settings.S3_MULTIPART_EXPIRE_HOURS)
    await multipart_uploads_col.insert_one({
        "upload_id": upload_id, "key": key, "bucket": settings.AWS_S3_BUCKET,
        "owner_id": current_user["_id"], "request_id": body.request_id,
        "purpose": body.purpose, "original_name": body.filename,
        "content_type": body.content_type, "category": category, "size": body.size,
        "part_size": part_size, "part_count": part_count, "state": "uploading",
        "media_state": "uploading", "scan_status": "not_uploaded", "scan_attempts": 0,
        "created_at": now_utc(), "updated_at": now_utc(), "expires_at": expires_at,
        "safe_filename": key.rsplit("/", 1)[-1], "view_once": body.view_once,
    })
    urls = []
    for part_number in range(1, part_count + 1):
        url = await asyncio.to_thread(client.generate_presigned_url, "upload_part", Params={
            "Bucket": settings.AWS_S3_BUCKET, "Key": key, "UploadId": upload_id,
            "PartNumber": part_number,
        }, ExpiresIn=settings.S3_PRESIGNED_URL_EXPIRE_SECONDS)
        urls.append({"part_number": part_number, "url": url})
    return {"upload_id": upload_id, "part_size": part_size, "parts": urls, "expires_at": expires_at}


@router.post("/multipart/{upload_id}/complete")
async def complete_multipart_upload(upload_id: str, body: MultipartUploadComplete, background_tasks: BackgroundTasks, current_user: dict = Depends(get_current_user)):
    record = await multipart_uploads_col.find_one({"upload_id": upload_id})
    if not record:
        raise HTTPException(status_code=404, detail="Multipart upload not found")
    if record["owner_id"] != current_user["_id"]:
        raise HTTPException(status_code=403, detail="Not authorized to complete this upload")
    if record.get("state") in {"quarantined", "safe"}:
        return {
            "file_url": f"/api/v1/uploads/s3/file/{upload_id}", "file_type": "video",
            "original_name": record["original_name"], "size_mb": round(record["size"] / 1_000_000, 2),
            "upload_id": upload_id, "scan_status": record.get("scan_status", "pending"), "storage": "aws_s3",
            "duplicate": True,
        }
    if record.get("state") != "uploading" or len(body.parts) != record["part_count"]:
        raise HTTPException(status_code=409, detail="Multipart upload is incomplete or already finalized")
    parts = sorted(
        [{"PartNumber": part.part_number, "ETag": part.etag.strip()} for part in body.parts],
        key=lambda part: part["PartNumber"],
    )
    if [part["PartNumber"] for part in parts] != list(range(1, record["part_count"] + 1)):
        raise HTTPException(status_code=400, detail="Multipart part list is invalid")
    client = _s3_client()
    await asyncio.to_thread(client.complete_multipart_upload, Bucket=record["bucket"], Key=record["key"], UploadId=upload_id, MultipartUpload={"Parts": parts})
    completed_object = await asyncio.to_thread(client.head_object, Bucket=record["bucket"], Key=record["key"])
    if int(completed_object.get("ContentLength", -1)) != record["size"]:
        await asyncio.to_thread(client.delete_object, Bucket=record["bucket"], Key=record["key"])
        await multipart_uploads_col.update_one({"_id": record["_id"]}, {"$set": {"state": "rejected", "scan_status": "rejected", "rejection_reason": "size_mismatch"}})
        raise HTTPException(status_code=400, detail="Completed upload size does not match the initiated file")
    prefix_response = await asyncio.to_thread(client.get_object, Bucket=record["bucket"], Key=record["key"], Range="bytes=0-8191")
    prefix = await asyncio.to_thread(prefix_response["Body"].read)
    await asyncio.to_thread(prefix_response["Body"].close)
    if not _matches_magic(record["category"], prefix):
        await asyncio.to_thread(client.delete_object, Bucket=record["bucket"], Key=record["key"])
        await multipart_uploads_col.update_one({"_id": record["_id"]}, {"$set": {"state": "rejected", "scan_status": "rejected", "rejection_reason": "file_signature_mismatch"}})
        raise HTTPException(status_code=400, detail="Uploaded content does not match the declared video type")
    ready_without_scanner = not settings.MEDIA_SCANNER_ENABLED
    await multipart_uploads_col.update_one({"_id": record["_id"], "state": "uploading"}, {"$set": {
        "state": "safe" if ready_without_scanner else "quarantined",
        "media_state": "ready" if ready_without_scanner else "uploaded",
        "scan_status": "safe" if ready_without_scanner else "pending", "completed_at": now_utc(), "updated_at": now_utc(),
        "expires_at": now_utc() + timedelta(days=settings.MEDIA_RETENTION_DAYS),
    }})
    if settings.MEDIA_SCANNER_ENABLED:
        background_tasks.add_task(_scan_s3_background, upload_id)
    return {
        "file_url": f"/api/v1/uploads/s3/file/{upload_id}", "file_type": "video",
        "original_name": record["original_name"], "size_mb": round(record["size"] / 1048576, 2),
        "upload_id": upload_id, "scan_status": "safe" if ready_without_scanner else "pending",
        "status": "ready" if ready_without_scanner else "uploaded",
        "status_url": f"/api/v1/media/{upload_id}/status", "storage": "aws_s3",
    }


@router.delete("/multipart/{upload_id}")
async def abort_multipart_upload(upload_id: str, current_user: dict = Depends(get_current_user)):
    record = await multipart_uploads_col.find_one({"upload_id": upload_id})
    if not record:
        return {"aborted": True}
    if record["owner_id"] != current_user["_id"] and current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not authorized to abort this upload")
    await asyncio.to_thread(_s3_client().abort_multipart_upload, Bucket=record["bucket"], Key=record["key"], UploadId=upload_id)
    await multipart_uploads_col.delete_one({"_id": record["_id"]})
    return {"aborted": True}


@router.get("/s3/file/{upload_id}")
async def get_s3_file(upload_id: str, current_user: dict = Depends(get_current_user)):
    record = await multipart_uploads_col.find_one({"upload_id": upload_id})
    if not record:
        raise HTTPException(status_code=404, detail="File not found")
    if record.get("scan_status") != "safe":
        raise HTTPException(status_code=423, detail="File is quarantined until security scanning succeeds")
    if await messages_col.find_one({"upload_id": upload_id, "view_once": True}, {"_id": 1}):
        raise HTTPException(status_code=409, detail="Use the one-time chat control to open this video")
    await _project(record["request_id"], current_user)
    if record.get("purpose") == "final_delivery":
        await _authorize_final_delivery(upload_id, record["request_id"], current_user)
    url = await asyncio.to_thread(_s3_client().generate_presigned_url, "get_object", Params={
        "Bucket": record["bucket"], "Key": record["key"],
        "ResponseContentDisposition": f'inline; filename="{record["original_name"].replace(chr(34), "")}"',
    }, ExpiresIn=settings.S3_PRESIGNED_URL_EXPIRE_SECONDS)
    return RedirectResponse(url, status_code=307, headers={"Cache-Control": "private, no-store"})


@router.post("/s3/access/{upload_id}")
async def create_s3_media_link(
    upload_id: str,
    mode: str = Query(default="preview", pattern="^(preview|download)$"),
    current_user: dict = Depends(get_current_user),
):
    record = await multipart_uploads_col.find_one({"upload_id": upload_id})
    if not record:
        raise HTTPException(status_code=404, detail="File not found")
    if record.get("scan_status") != "safe":
        raise HTTPException(status_code=423, detail="File is quarantined until security scanning succeeds")
    project = await _project(record["request_id"], current_user)
    if record.get("purpose") == "final_delivery":
        await _authorize_final_delivery(upload_id, record["request_id"], current_user)
    elif mode == "download" and current_user["_id"] == project["editor_user_id"] and not project.get("media_policy", {}).get("editor_download_allowed", False):
        raise HTTPException(status_code=403, detail="The client has disabled editor downloads")
    disposition = "attachment" if mode == "download" else "inline"
    clean_name = record["original_name"].replace(chr(34), "")
    url = await asyncio.to_thread(_s3_client().generate_presigned_url, "get_object", Params={
        "Bucket": record["bucket"], "Key": record["key"],
        "ResponseContentDisposition": f'{disposition}; filename="{clean_name}"',
    }, ExpiresIn=settings.S3_PRESIGNED_URL_EXPIRE_SECONDS)
    return {
        "url": url,
        "expires_in": settings.S3_PRESIGNED_URL_EXPIRE_SECONDS,
        "mode": mode,
    }


@router.post("/projects/{request_id}/agreement")
async def accept_agreement(request_id: str, current_user: dict = Depends(get_current_user)):
    await _project(request_id, current_user)
    await media_agreements_col.update_one(
        {"request_id": request_id, "user_id": current_user["_id"]},
        {"$set": {"accepted_at": now_utc(), "version": "2026-01", "role": current_user.get("role")}},
        upsert=True,
    )
    return {"accepted": True}


@router.get("/projects/{request_id}/security")
async def security_status(request_id: str, current_user: dict = Depends(get_current_user)):
    project = await _project(request_id, current_user)
    return {
        "agreement_accepted": await _accepted(request_id, current_user),
        "editor_download_allowed": project.get("media_policy", {}).get("editor_download_allowed", False),
        "retention_days": project.get("media_policy", {}).get("retention_days", settings.MEDIA_RETENTION_DAYS),
        "max_chat_video_mb": settings.MAX_CHAT_ATTACHMENT_MB,
        "limits_mb": {
            "image": settings.MAX_CHAT_IMAGE_MB, "voice": settings.MAX_CHAT_AUDIO_MB,
            "audio": settings.MAX_CHAT_AUDIO_MB, "document": settings.MAX_CHAT_FILE_MB,
            "zip": settings.MAX_CHAT_FILE_MB, "video": settings.MAX_CHAT_VIDEO_MB,
            "viewOnceVideo": settings.MAX_CHAT_VIDEO_MB,
        },
        "max_files_per_message": settings.MAX_FILES_PER_MESSAGE,
        "max_text_message_length": settings.MAX_TEXT_MESSAGE_LENGTH,
        "chat_video_mime_types": sorted(CHAT_VIDEO_MIME_TYPES),
        "direct_upload_min_mb": settings.DIRECT_UPLOAD_MIN_MB,
    }


@router.put("/projects/{request_id}/policy")
async def update_policy(request_id: str, body: MediaPolicyUpdate, current_user: dict = Depends(get_current_user)):
    project = await _project(request_id, current_user)
    if project["user_id"] != current_user["_id"] and current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only the client can change media permissions")
    policy = body.model_dump()
    await requests_col.update_one({"_id": project["_id"]}, {"$set": {"media_policy": policy}})
    return policy


@router.post("/projects/{request_id}/report")
async def report_misuse(request_id: str, body: MediaReportBody, current_user: dict = Depends(get_current_user)):
    await _project(request_id, current_user)
    await media_reports_col.insert_one({"request_id": request_id, "reporter_id": current_user["_id"], **body.model_dump(), "status": "open", "created_at": now_utc()})
    return {"message": "Misuse report submitted for admin review"}


@router.get("/projects/{request_id}/access-logs")
async def access_logs(request_id: str, current_user: dict = Depends(get_current_user)):
    project = await _project(request_id, current_user)
    if project["user_id"] != current_user["_id"] and current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only the client can view media access logs")
    logs = await media_access_logs_col.find({"request_id": request_id}).sort("created_at", -1).limit(100).to_list(100)
    return {"logs": serialize_list(logs)}


@router.post("/access/{filename}")
async def create_media_link(filename: str, mode: str = Query(default="preview", pattern="^(preview|download)$"), current_user: dict = Depends(get_current_user)):
    record = await db["uploads.files"].find_one({"filename": filename}, sort=[("uploadDate", -1)])
    if not record:
        raise HTTPException(status_code=404, detail="File not found")
    metadata = record.get("metadata") or {}
    view_once_message = await messages_col.find_one(
        {"upload_id": str(record["_id"]), "view_once": True}, {"_id": 1},
    )
    if view_once_message:
        raise HTTPException(status_code=409, detail="Use the one-time chat control to open this video")
    if not _media_available(metadata):
        raise HTTPException(status_code=423, detail="File is quarantined until security scanning succeeds")
    request_id = metadata.get("request_id")
    if request_id:
        project = await _project(request_id, current_user)
        if metadata.get("purpose") == "final_delivery":
            await _authorize_final_delivery(str(record["_id"]), request_id, current_user)
        if not await _accepted(request_id, current_user):
            raise HTTPException(status_code=403, detail="Accept the media agreement to access project files")
        if mode == "download" and current_user["_id"] == project["editor_user_id"] and not project.get("media_policy", {}).get("editor_download_allowed", False):
            raise HTTPException(status_code=403, detail="The client has disabled editor downloads")
    elif metadata.get("purpose") == "editor_status":
        from app.db.mongodb import editor_statuses_col
        if not await editor_statuses_col.find_one({"upload_id": record["_id"], "is_active": True, "expires_at": {"$gt": now_utc()}}, {"_id": 1}):
            raise HTTPException(status_code=404, detail="Status is unavailable or has expired")
    elif metadata.get("owner_id") != current_user["_id"] and current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not authorized to access this file")
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.MEDIA_LINK_EXPIRE_MINUTES)
    token = jwt.encode({
        "sub": str(current_user["_id"]), "file": filename, "mode": mode,
        "type": "media", "exp": expires, "iss": settings.JWT_ISSUER,
        "aud": "editzone-media",
    }, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return {"url": f"/api/v1/uploads/file/{quote(filename)}?token={quote(token)}", "expires_at": expires.isoformat(), "mode": mode}


@router.get("/view-once/{message_id}")
async def stream_view_once(message_id: str, token: str = Query(...)):
    """Atomically redeem one capability and proxy bytes so a URL cannot be replayed."""
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM],
            issuer=settings.JWT_ISSUER, audience="editzone-view-once",
        )
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="View-once link is invalid or expired")
    if payload.get("type") != "view_once" or payload.get("message_id") != message_id:
        raise HTTPException(status_code=401, detail="Invalid view-once capability")
    user_id = payload.get("sub", "")
    capability = payload.get("cap", "")
    if not ObjectId.is_valid(user_id) or not ObjectId.is_valid(message_id) or not capability:
        raise HTTPException(status_code=401, detail="Invalid view-once capability")
    capability_hash = hashlib.sha256(capability.encode()).hexdigest()
    reserved = await messages_col.find_one({
        "_id": ObjectId(message_id), "view_once": True,
        "receiver_id": user_id, "view_once_status": "reserved",
        "view_once_capability_hash": capability_hash,
        "view_once_capability_expires_at": {"$gt": now_utc()},
        "view_once_delivered_at": None,
    })
    if not reserved:
        raise HTTPException(status_code=410, detail={"code": "VIEW_ONCE_ALREADY_OPENED", "message": "This media has already been viewed."})
    upload_id = reserved.get("upload_id")
    if not upload_id:
        raise HTTPException(status_code=410, detail="View-once media is unavailable")

    gridfs_record = None
    s3_record = None
    if ObjectId.is_valid(upload_id):
        gridfs_record = await db["uploads.files"].find_one({"_id": ObjectId(upload_id)})
        metadata = (gridfs_record or {}).get("metadata") or {}
        if not gridfs_record or metadata.get("scan_status") != "safe":
            raise HTTPException(status_code=410, detail="View-once media is unavailable")
    else:
        s3_record = await multipart_uploads_col.find_one({"upload_id": upload_id, "scan_status": "safe"})
        if not s3_record:
            raise HTTPException(status_code=410, detail="View-once media is unavailable")

    opened_at = now_utc()
    message = await messages_col.find_one_and_update(
        {
            "_id": ObjectId(message_id), "view_once": True,
            "receiver_id": user_id, "view_once_status": "reserved",
            "view_once_capability_hash": capability_hash,
            "view_once_capability_expires_at": {"$gt": now_utc()},
            "view_once_delivered_at": None,
        },
        {"$set": {
            "view_once_status": "opened", "view_once_delivered_at": opened_at,
            "viewed_at": opened_at, "viewed_by": ObjectId(user_id),
            "consumed": True, "consumed_at": opened_at,
         },
         "$unset": {"view_once_capability_hash": "", "view_once_capability_expires_at": ""}},
        return_document=ReturnDocument.AFTER,
    )
    if not message:
        raise HTTPException(status_code=410, detail={"code": "VIEW_ONCE_ALREADY_OPENED", "message": "This media has already been viewed."})
    # Import locally to avoid coupling upload-router initialization to the
    # Socket.IO module while still updating both participants immediately.
    from app.sockets.socket_manager import sio
    await sio.emit("view_once_opened", {
        "request_id": message["request_id"], "message_id": message_id,
        "viewed_at": opened_at.isoformat(),
    }, room=f'chat_{message["request_id"]}')
    if gridfs_record:
        metadata = gridfs_record.get("metadata") or {}
        stream = await uploads_bucket.open_download_stream(gridfs_record["_id"])

        async def gridfs_chunks():
            while data := await stream.readchunk():
                yield data

        return StreamingResponse(gridfs_chunks(), media_type=metadata.get("content_type", "video/mp4"), headers={"Cache-Control": "private, no-store", "Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"})
    response = await asyncio.to_thread(_s3_client().get_object, Bucket=s3_record["bucket"], Key=s3_record["key"])
    body = response["Body"]

    async def s3_chunks():
        try:
            while chunk := await asyncio.to_thread(body.read, 1024 * 1024):
                yield chunk
        finally:
            await asyncio.to_thread(body.close)

    return StreamingResponse(s3_chunks(), media_type=s3_record.get("content_type", "video/mp4"), headers={"Cache-Control": "private, no-store", "Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"})


@router.get("/file/{filename}")
async def get_uploaded_file(filename: str, request: Request, token: str | None = Query(default=None)):
    if "/" in filename or "\\" in filename or filename in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid filename")
    record = await db["uploads.files"].find_one(
        {"filename": filename},
        sort=[("uploadDate", -1)],
    )
    if not record:
        raise HTTPException(status_code=404, detail="File not found")
    metadata = record.get("metadata") or {}
    if not _media_available(metadata):
        raise HTTPException(status_code=423, detail="File is quarantined until security scanning succeeds")
    request_id = metadata.get("request_id")
    # Legacy profile and portfolio assets were intentionally public. Project-bound
    # uploads never take this path and always require a short-lived capability.
    if not request_id and metadata.get("purpose") != "editor_status" and not token:
        stream = await uploads_bucket.open_download_stream(record["_id"])
        async def public_chunks():
            while data := await stream.readchunk():
                yield data
        return StreamingResponse(public_chunks(), media_type=metadata.get("content_type", "application/octet-stream"), headers={"Cache-Control": "public, max-age=3600", "Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"})
    try:
        payload = jwt.decode(
            token or "", settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM],
            issuer=settings.JWT_ISSUER, audience="editzone-media",
        )
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Media link is invalid or expired")
    if payload.get("type") != "media" or payload.get("file") != filename or not ObjectId.is_valid(payload.get("sub", "")):
        raise HTTPException(status_code=401, detail="Invalid media link")
    user = await db.users.find_one({"_id": ObjectId(payload["sub"]), **ACTIVE_ACCOUNT_FILTER})
    if not user:
        raise HTTPException(status_code=401, detail="Media user is unavailable")
    if metadata.get("purpose") == "editor_status":
        from app.db.mongodb import editor_statuses_col
        if not await editor_statuses_col.find_one({"upload_id": record["_id"], "is_active": True, "expires_at": {"$gt": now_utc()}}, {"_id": 1}):
            raise HTTPException(status_code=404, detail="Status is unavailable or has expired")
    if request_id:
        project = await _project(request_id, user)
        if not await _accepted(request_id, user):
            raise HTTPException(status_code=403, detail="Media agreement is required")
        if payload.get("mode") == "download" and user["_id"] == project["editor_user_id"] and not project.get("media_policy", {}).get("editor_download_allowed", False):
            raise HTTPException(status_code=403, detail="Download permission was revoked")
    await _log(record, user, payload.get("mode", "preview"), request)
    stream = await uploads_bucket.open_download_stream(record["_id"])

    async def chunks():
        while data := await stream.readchunk():
            yield data

    disposition = "attachment" if payload.get("mode") == "download" else "inline"
    safe_name = metadata.get("original_name", filename).replace('"', "")
    return StreamingResponse(
        chunks(),
        media_type=metadata.get("content_type", "application/octet-stream"),
        headers={"Cache-Control": "private, no-store", "Content-Disposition": f'{disposition}; filename="{safe_name}"', "X-Content-Type-Options": "nosniff"},
    )
