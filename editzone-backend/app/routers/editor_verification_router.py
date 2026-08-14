import hashlib
import json
import logging
import random
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from pymongo import ReturnDocument
from pymongo.errors import PyMongoError

from app.config import settings
from app.core.security import require_editor
from app.core.utils import ensure_utc, now_utc
from app.db.mongodb import editors_col, selfie_sessions_col
from app.services.live_selfie_service import (
    SelfieServiceError,
    SelfieValidationError,
    delete_selfie,
    log_face_verification_configuration,
    upload_selfie,
    validate_selfie,
    verify_one_clear_face,
)
from app.services.aws_face_errors import AwsFaceVerificationError
from app.services.identity_verification_service import (
    IdentityServiceError,
    IdentityValidationError,
    compare_identity_faces,
    enforce_rate_limit,
    write_audit,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/editor/selfie", tags=["Editor Registration"])
SELFIE_UPLOAD_GRACE = timedelta(minutes=2)


def _require_secure_request(request: Request) -> None:
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    is_https = request.url.scheme == "https" or forwarded_proto.split(",", 1)[0].strip() == "https"
    if settings.ENV.lower() != "development" and not is_https:
        raise HTTPException(status_code=400, detail="Live selfie verification requires HTTPS")


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def _release_session(session_id, error: str) -> None:
    # Cleanup must never mask the useful validation/provider error that caused
    # it. A temporary Mongo failure used to turn those errors into a generic 500.
    try:
        session = await selfie_sessions_col.find_one({"_id": session_id}, {"user_id": 1})
        await selfie_sessions_col.update_one(
            {"_id": session_id, "status": "uploading"},
            {
                "$set": {"status": "pending", "last_error": error, "updated_at": now_utc()},
                "$inc": {"attempts": 1},
            },
        )
        if session:
            await editors_col.update_one(
                {"user_id": session["user_id"]},
                {
                    "$set": {
                        "liveness_status": "failed",
                        "last_verification_error": error,
                        "updated_at": now_utc(),
                    },
                    "$inc": {"verification_attempt_count": 1},
                },
            )
    except PyMongoError:
        logger.exception("Could not release live-selfie session after error=%s", error)


@router.post("/retry")
async def retry_live_selfie(
    request: Request,
    current_user: dict = Depends(require_editor),
):
    _require_secure_request(request)
    now = now_utc()
    try:
        await selfie_sessions_col.update_many(
            {"user_id": current_user["_id"], "status": {"$in": ["pending", "uploading"]}},
            {"$set": {"status": "superseded", "updated_at": now}},
        )
        await editors_col.update_one(
            {"user_id": current_user["_id"], "selfie_verified": {"$ne": True}},
            {"$set": {"liveness_status": "waiting", "last_verification_error": None, "updated_at": now}},
        )
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="The camera session could not be reset. Please try again") from exc
    logger.info("Identity verification stage=camera_session_closed user=%s", str(current_user["_id"])[-6:])
    return {"message": "Camera session reset. Try the camera again"}


@router.post("/session", status_code=201)
async def create_capture_session(
    request: Request,
    current_user: dict = Depends(require_editor),
):
    _require_secure_request(request)
    log_face_verification_configuration()
    if current_user.get("registration_complete"):
        raise HTTPException(status_code=409, detail="Editor registration is already complete")

    try:
        profile = await editors_col.find_one(
            {"user_id": current_user["_id"]},
            {
                "identity_verification_status": 1,
                "nic_front_key": 1,
                "nic_ocr_verified": 1,
                "selfie_verified": 1,
            },
        )
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="The identity verification service is temporarily unavailable. Please try again") from exc
    if not profile or not profile.get("nic_front_key") or not profile.get("nic_ocr_verified"):
        raise HTTPException(status_code=409, detail="Verify the NIC front image before starting the camera")
    if profile.get("selfie_verified"):
        raise HTTPException(status_code=409, detail="Identity verification is already complete")
    try:
        await enforce_rate_limit(current_user["_id"], "selfie_session")
    except IdentityValidationError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="The camera session service is temporarily unavailable. Please try again") from exc

    now = now_utc()
    # LIVE_SELFIE_SESSION_MINUTES is the intended user-facing session lifetime.
    # The old 20-second liveness timeout was also the TTL field, so Mongo could
    # delete the session while the user was still reviewing their photo.
    expires_at = now + timedelta(minutes=settings.LIVE_SELFIE_SESSION_MINUTES)
    capture_token = secrets.token_urlsafe(32)
    challenge = random.SystemRandom().sample(["blink", "turn_left", "turn_right"], k=1)
    logger.info("Identity verification stage=liveness_challenge_started user=%s", str(current_user["_id"])[-6:])
    try:
        await selfie_sessions_col.update_many(
            {"user_id": current_user["_id"], "status": "pending"},
            {"$set": {"status": "superseded", "updated_at": now}},
        )
        result = await selfie_sessions_col.insert_one({
            "user_id": current_user["_id"],
            "token_hash": _token_hash(capture_token),
            "status": "pending",
            "attempts": 0,
            "challenge": challenge,
            "created_at": now,
            "expires_at": expires_at,
        })
    except PyMongoError as exc:
        logger.exception("Could not create live-selfie session")
        raise HTTPException(status_code=503, detail="The camera session service is temporarily unavailable. Please try again") from exc
    return {
        "session_id": str(result.inserted_id),
        "capture_token": capture_token,
        "expires_at": expires_at,
        "max_file_size_bytes": settings.SELFIE_MAX_UPLOAD_MB * 1024 * 1024,
        "challenge": challenge,
    }


@router.post("", status_code=201)
async def submit_live_selfie(
    request: Request,
    file: UploadFile = File(...),
    session_id: str = Form(...),
    capture_token: str = Form(...),
    captured_at: str = Form(...),
    face_count: int = Form(...),
    liveness_events: str = Form(...),
    capture_source: str = Header(..., alias="X-Capture-Source"),
    current_user: dict = Depends(require_editor),
):
    _require_secure_request(request)
    if current_user.get("registration_complete"):
        raise HTTPException(status_code=409, detail="Editor registration is already complete")
    if capture_source != "camera":
        logger.warning("Rejected non-camera selfie from editor %s", current_user["_id"])
        raise HTTPException(status_code=400, detail="Capture the selfie using the live camera")
    if face_count != 1:
        logger.warning(
            "Rejected client face count %s from editor %s", face_count, current_user["_id"]
        )
        detail = "No face detected" if face_count < 1 else "Multiple faces detected"
        raise HTTPException(status_code=400, detail=f"{detail}. Capture a new selfie")
    try:
        events = json.loads(liveness_events)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid liveness verification data") from exc

    from bson import ObjectId
    if not ObjectId.is_valid(session_id):
        raise HTTPException(status_code=400, detail="Invalid or expired camera session")

    try:
        capture_time = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        if capture_time.tzinfo is None:
            capture_time = capture_time.replace(tzinfo=timezone.utc)
        capture_time = capture_time.astimezone(timezone.utc)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid camera capture timestamp") from exc

    now = now_utc()
    try:
        session = await selfie_sessions_col.find_one_and_update(
            {
                "_id": ObjectId(session_id),
                "user_id": current_user["_id"],
                "token_hash": _token_hash(capture_token),
                "status": "pending",
                "attempts": {"$lt": settings.SELFIE_VERIFICATION_MAX_ATTEMPTS},
                "created_at": {"$lte": capture_time},
                "expires_at": {"$gte": capture_time, "$gt": now - SELFIE_UPLOAD_GRACE},
            },
            {"$set": {"status": "uploading", "updated_at": now}},
            return_document=ReturnDocument.AFTER,
        )
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="The selfie verification service is temporarily unavailable. Please try again") from exc
    if not session:
        raise HTTPException(
            status_code=409,
            detail="Camera session expired or is already uploading. Start the camera again",
        )
    if (
        not isinstance(events, list)
        or not all(isinstance(event, dict) for event in events)
        or [event.get("action") for event in events] != session.get("challenge")
    ):
        await _release_session(session["_id"], "invalid liveness challenge")
        await write_audit(current_user["_id"], "liveness", "invalid_sequence")
        raise HTTPException(status_code=400, detail="Liveness challenge was not completed in order")
    event_times = []
    try:
        for event in events:
            event_time = datetime.fromisoformat(event["completed_at"].replace("Z", "+00:00"))
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)
            event_times.append(event_time.astimezone(timezone.utc))
            if float(event.get("confidence", 0)) < 0.65:
                raise ValueError("low confidence")
    except (KeyError, TypeError, ValueError) as exc:
        await _release_session(session["_id"], "invalid liveness evidence")
        raise HTTPException(status_code=400, detail="Liveness evidence was not strong enough") from exc
    # Motor decodes BSON datetimes as naive UTC unless tz_aware is enabled.
    # Browser timestamps are aware UTC; normalize before Python comparisons to
    # prevent `can't compare offset-naive and offset-aware datetimes` 500s.
    session_created_at = ensure_utc(session.get("created_at"))
    if (
        not event_times
        or session_created_at is None
        or event_times != sorted(event_times)
        or event_times[0] < session_created_at
        or event_times[-1] > now + timedelta(seconds=15)
        or event_times[-1] - event_times[0] > timedelta(minutes=2)
    ):
        await _release_session(session["_id"], "invalid liveness timing")
        raise HTTPException(status_code=400, detail="Liveness challenge timing was invalid")
    if now - capture_time > timedelta(minutes=2) or capture_time > now + timedelta(seconds=15):
        await _release_session(session["_id"], "stale capture timestamp")
        raise HTTPException(status_code=400, detail="The selfie expired. Capture a new one")

    max_bytes = settings.SELFIE_MAX_UPLOAD_MB * 1024 * 1024
    try:
        contents = await file.read(max_bytes + 1)
    except Exception as exc:
        await _release_session(session["_id"], "camera upload could not be read")
        raise HTTPException(status_code=400, detail="The camera image could not be read. Capture a new selfie") from exc
    finally:
        try:
            await file.close()
        except Exception:
            logger.warning("Could not close live-selfie upload stream")
    content_type = (file.content_type or "").split(";", 1)[0].strip().lower()
    logger.info(
        "Live selfie received endpoint=%s bytes_received=%s mime_type=%s byte_length=%s",
        request.url.path,
        bool(contents),
        content_type or "missing",
        len(contents),
    )
    try:
        extension, width, height = validate_selfie(contents, content_type)
        await verify_one_clear_face(contents)
        profile = await editors_col.find_one(
            {"user_id": current_user["_id"]},
            {
                "nic_front_key": 1,
                "identity_verification_status": 1,
                "manual_review_reasons": 1,
                "nic_ocr_verified": 1,
            },
        )
        if not profile or not profile.get("nic_front_key"):
            raise SelfieValidationError("NIC verification must be completed first")
        logger.info(
            "Live selfie reference check endpoint=%s reference_exists_in_profile=%s",
            request.url.path,
            True,
        )
        similarity = await compare_identity_faces(profile["nic_front_key"], contents)
        selfie_url = await upload_selfie(
            str(current_user["_id"]), contents, content_type, extension
        )
    except SelfieValidationError as exc:
        logger.warning("Live selfie validation failed for %s: %s", current_user["_id"], exc)
        await _release_session(session["_id"], str(exc))
        raise HTTPException(
            status_code=400,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except (SelfieServiceError, IdentityServiceError) as exc:
        await _release_session(session["_id"], str(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AwsFaceVerificationError as exc:
        await _release_session(session["_id"], exc.code)
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    except PyMongoError as exc:
        await _release_session(session["_id"], "database lookup failed")
        raise HTTPException(status_code=503, detail="The identity verification service is temporarily unavailable. Please try again") from exc

    threshold = settings.AWS_REKOGNITION_SIMILARITY_THRESHOLD
    face_matched = similarity >= threshold
    reasons = list(profile.get("manual_review_reasons") or [])
    if not face_matched:
        reasons.append("NIC portrait and live selfie face match was uncertain")
    identity_status = (
        "selfie_verified"
        if profile.get("nic_ocr_verified") and face_matched and not reasons
        else ("manual_review" if similarity >= threshold - settings.SELFIE_MANUAL_REVIEW_MARGIN else "failed")
    )
    try:
        result = await editors_col.update_one(
            {
                "user_id": current_user["_id"],
                "identity_verification_status": {"$nin": ["selfie_verified", "verified"]},
            },
            {
                "$set": {
                    "selfie_s3_key": selfie_url,
                    "selfie_verified": identity_status == "selfie_verified",
                    "liveness_passed": True,
                    "liveness_status": "passed",
                    "face_match_score": similarity,
                    "face_match_similarity": similarity,
                    "identity_verification_status": identity_status,
                    "manual_review_reasons": list(dict.fromkeys(reasons)),
                    "selfie_verified_at": now_utc(),
                    "last_verification_error": None if identity_status == "selfie_verified" else "face_similarity_below_threshold",
                    "selfie_dimensions": {"width": width, "height": height},
                    "updated_at": now_utc(),
                }
            },
        )
        if result.modified_count != 1:
            await delete_selfie(selfie_url)
            await _release_session(session["_id"], "duplicate upload")
            raise HTTPException(
                status_code=409, detail="Live selfie verification is already complete"
            )
        await selfie_sessions_col.update_one(
            {"_id": session["_id"], "status": "uploading"},
            {
                "$set": {
                    "status": "completed",
                    "completed_at": now_utc(),
                    "object_key": selfie_url,
                }
            },
        )
    except PyMongoError as exc:
        logger.exception("Could not link live selfie to editor %s", current_user["_id"])
        await delete_selfie(selfie_url)
        await _release_session(session["_id"], "database update failed")
        raise HTTPException(
            status_code=503,
            detail="The selfie uploaded but registration could not be updated. Please try again",
        ) from exc

    await write_audit(
        current_user["_id"],
        "selfie_face_match",
        identity_status,
        {
            "face_match_similarity": round(similarity, 2),
            "liveness_steps": len(events),
        },
    )
    return {
        "message": (
            "Identity verification completed successfully"
            if identity_status == "selfie_verified"
            else (
                "Selfie received. Identity verification requires manual admin review"
                if identity_status == "manual_review"
                else "Your selfie could not be matched with your verified identity. Please try again"
            )
        ),
        "selfie_verified": identity_status == "selfie_verified",
        "liveness_passed": True,
        "identity_status": identity_status,
        "similarity_score": round(similarity, 2),
        "face_detected": True,
        "registration_allowed": identity_status == "selfie_verified",
    }


@router.get("/status")
async def selfie_status(current_user: dict = Depends(require_editor)):
    profile = await editors_col.find_one(
        {"user_id": current_user["_id"]},
        {
            "selfie_verified": 1,
            "selfie_verified_at": 1,
            "identity_verification_status": 1,
        },
    )
    if not profile:
        raise HTTPException(status_code=404, detail="Editor profile not found")
    return {
        "selfie_verified": bool(profile.get("selfie_verified")),
        "verified_at": profile.get("selfie_verified_at"),
        "identity_status": profile.get("identity_verification_status", "not_started"),
    }
