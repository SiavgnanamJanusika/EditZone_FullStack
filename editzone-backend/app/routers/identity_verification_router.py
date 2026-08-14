import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.config import settings
from app.core.security import get_current_user, require_editor
from app.core.utils import now_utc
from app.db.mongodb import editors_col, users_col
from app.services.identity_verification_service import (
    IdentityServiceError,
    IdentityValidationError,
    OcrProviderError,
    analyze_nic_front,
    classify_nic_match,
    delete_identity_key,
    enforce_rate_limit,
    mask_nic,
    nic_hash,
    normalize_nic,
    probe_ocr_health,
    upload_identity_image,
    validate_nic_image,
    write_audit,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/editor/identity", tags=["Editor Identity Verification"])
nic_router = APIRouter(prefix="/api/v1/verification", tags=["Editor Identity Verification"])


@nic_router.get("/nic/ocr-health")
async def nic_ocr_health(current_user: dict = Depends(get_current_user)):
    if settings.ENV.lower() != "development" and current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return await probe_ocr_health()


def _require_https(request: Request) -> None:
    forwarded = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
    if settings.ENV.lower() != "development" and request.url.scheme != "https" and forwarded != "https":
        raise HTTPException(status_code=400, detail="Identity verification requires HTTPS")


@router.get("/status")
async def identity_status(current_user: dict = Depends(require_editor)):
    profile = await editors_col.find_one(
        {"user_id": current_user["_id"]},
        {
            "identity_verification_status": 1,
            "nic_ocr_verified": 1,
            "manual_review_reasons": 1,
            "nic_front_key": 1,
            "selfie_verified": 1,
            "liveness_status": 1,
            "face_match_score": 1,
            "selfie_verified_at": 1,
            "verification_attempt_count": 1,
            "last_verification_error": 1,
        },
    )
    if not profile:
        raise HTTPException(status_code=404, detail="Editor profile not found")
    status = profile.get("identity_verification_status", "not_started")
    nic_verified = bool(profile.get("nic_front_key") and profile.get("nic_ocr_verified"))
    return {
        "status": status,
        "nic_verified": nic_verified,
        "nic_front_verified": nic_verified,
        "manual_review": status == "manual_review",
        "manual_review_reasons": profile.get("manual_review_reasons", []),
        "nic_documents_uploaded": bool(profile.get("nic_front_key")),
        "selfie_verified": bool(profile.get("selfie_verified")),
        "liveness_status": profile.get("liveness_status", "waiting"),
        "registration_allowed": nic_verified and bool(profile.get("selfie_verified")) and status == "selfie_verified",
        "retention_days": settings.IDENTITY_DOCUMENT_RETENTION_DAYS,
        "privacy_policy_version": "2026-08",
    }


@nic_router.get("/status")
async def verification_status(current_user: dict = Depends(require_editor)):
    return await identity_status(current_user)


@nic_router.post("/nic")
async def verify_entered_nic_image(
    request: Request,
    nic_number: str = Form(...),
    nic_image: UploadFile = File(...),
    current_user: dict = Depends(require_editor),
):
    _require_https(request)
    logger.info(
        "NIC verification stage=file_received user=%s filename=%s content_type=%s",
        current_user.get("_id"), getattr(nic_image, "filename", None) or "unknown",
        nic_image.content_type or "unknown",
    )
    if current_user.get("registration_complete"):
        raise HTTPException(status_code=409, detail="Editor identity verification is already complete")
    try:
        normalized_nic = normalize_nic(nic_number)
        logger.info("NIC verification stage=nic_normalized user=%s nic=%s", current_user.get("_id"), mask_nic(normalized_nic))
    except IdentityValidationError as exc:
        return {"success": False, "matched": False, "status": "invalid_input", "message": str(exc)}

    try:
        await enforce_rate_limit(current_user["_id"], "nic_number_match")
    except IdentityValidationError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    max_bytes = settings.NIC_IMAGE_MAX_MB * 1024 * 1024
    contents = await nic_image.read(max_bytes + 1)
    await nic_image.close()
    content_type = (nic_image.content_type or "").split(";", 1)[0].strip().lower()
    try:
        extension, _, _ = validate_nic_image(contents, content_type, settings.NIC_IMAGE_MAX_MB)
    except IdentityValidationError as exc:
        await write_audit(current_user["_id"], "nic_number_match", "invalid_input")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("NIC verification stage=file_validated user=%s file_type=%s file_size=%d", current_user.get("_id"), content_type, len(contents))

    key = ""
    try:
        key = await upload_identity_image(str(current_user["_id"]), "front", contents, content_type, extension)
        logger.info("NIC verification stage=image_uploaded_to_s3 user=%s key_suffix=%s", current_user.get("_id"), key.rsplit("/", 1)[-1][-12:])
    except IdentityServiceError as exc:
        await write_audit(current_user["_id"], "nic_number_match", "s3_upload_failed")
        return JSONResponse(status_code=503, content={"success": False, "matched": False, "status": "storage_unavailable", "message": "AWS S3 upload failed."})

    try:
        analysis = await analyze_nic_front(contents, content_type)
    except IdentityValidationError:
        await delete_identity_key(key)
        await write_audit(current_user["_id"], "nic_number_match", "unreadable")
        return JSONResponse(status_code=422, content={"success": False, "matched": False, "status": "unreadable", "message": "Uploaded image quality is too low."})
    except OcrProviderError as exc:
        await delete_identity_key(key)
        await write_audit(current_user["_id"], "nic_number_match", exc.status, {"provider": "aws_textract", "aws_error_code": exc.aws_code})
        return JSONResponse(status_code=503 if exc.status == "ocr_unavailable" else 422, content={"success": False, "matched": False, "status": exc.status, "message": str(exc)})
    except IdentityServiceError:
        await delete_identity_key(key)
        await write_audit(current_user["_id"], "nic_number_match", "ocr_unavailable")
        return JSONResponse(status_code=503, content={"success": False, "matched": False, "status": "ocr_unavailable", "message": "NIC verification service is temporarily unavailable. Please try again later."})

    details = analysis["candidate_confidences"]
    decision = classify_nic_match(normalized_nic, details, analysis.get("detected_line_count"))
    status, message, confidence = decision["status"], decision["message"], decision["confidence"]
    reasons = [decision["reason"]] if decision["reason"] else []

    try:
        old_profile = await editors_col.find_one({"user_id": current_user["_id"]}, {"nic_front_key": 1})
        fields = {
            "nic_front_key": key,
            "nic_verification_status": status,
            "nic_ocr_verified": status == "verified",
            "nic_ocr_provider": analysis["ocr_provider"],
            "nic_ocr_confidence": round(confidence, 2),
            "nic_masked": mask_nic(normalized_nic),
            "nic_verified_at": now_utc() if status == "verified" else None,
            "identity_verification_status": "nic_verified" if status == "verified" else "manual_review",
            "manual_review_reasons": reasons,
            "identity_updated_at": now_utc(),
        }
        if status == "verified":
            protected_hash = nic_hash(normalized_nic)
            duplicate = await editors_col.find_one({
                "user_id": {"$ne": current_user["_id"]}, "nic_hash": protected_hash,
                "nic_ocr_verified": True, "deleted": {"$ne": True},
            }, {"_id": 1})
            if duplicate:
                raise IdentityValidationError("This identity cannot be used for another editor account")
            fields["nic_hash"] = protected_hash
            try:
                await users_col.update_one({"_id": current_user["_id"]}, {"$set": {"nic": normalized_nic}})
            except DuplicateKeyError as exc:
                raise IdentityValidationError("This identity cannot be used for another editor account") from exc
        result = await editors_col.update_one(
            {"user_id": current_user["_id"], "identity_verification_status": {"$nin": ["selfie_verified", "verified"]}},
            {"$set": fields},
        )
        if result.modified_count != 1:
            raise IdentityValidationError("Identity verification is already complete")
        old_key = (old_profile or {}).get("nic_front_key")
        if old_key and old_key != key:
            await delete_identity_key(old_key)
        logger.info("NIC verification stage=verification_result_saved user=%s status=%s nic=%s", current_user.get("_id"), status, mask_nic(normalized_nic))
    except (IdentityServiceError, IdentityValidationError, PyMongoError) as exc:
        await delete_identity_key(key)
        raise HTTPException(status_code=409 if isinstance(exc, IdentityValidationError) else 503, detail=str(exc)) from exc

    await write_audit(current_user["_id"], "nic_number_match", status, {
        "ocr_provider": analysis["ocr_provider"], "ocr_confidence": round(confidence, 2),
        "candidate_count": len(details),
    })
    return {
        "success": status == "verified",
        "matched": status == "verified",
        "status": status,
        "message": message,
        "nic_front_verified": status == "verified",
        "selfie_verified": False,
        "registration_allowed": False,
    }
