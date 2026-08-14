import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from urllib.parse import urlparse
from pymongo.errors import PyMongoError

from app.db.mongodb import db, users_col, editors_col, requests_col, messages_col, payments_col, reviews_col, notifications_col, identity_audit_logs_col
from app.core.security import get_current_user, verify_password
from app.core.utils import serialize_doc, serialize_list, now_utc
from app.core.validators import get_file_category, is_valid_upload_url
from app.schemas.schemas import UserProfileUpdate, AccountDeletionBody
from app.services.identity_verification_service import delete_identity_key, write_audit
from app.config import settings
from app.routers.auth_router import _verify_google_credential
from app.services.account_deletion_service import deletion_blockers, hard_delete_account
from app.services.auth_throttle_service import get_scope_counts, increment_counter
from app.services.email_service import send_account_deletion_email
from app.services.otp_service import purge_otp_cache
from app.sockets.socket_manager import disconnect_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/users", tags=["Users"])
account_router = APIRouter(prefix="/api/v1", tags=["Account"])


@router.get("/privacy-policy")
async def privacy_policy():
    return {
        "version": "2026-08",
        "identity_data": {
            "purpose": "NIC front images are used only for identity verification and fraud prevention.",
            "public_visibility": "Identity documents and NIC numbers are never displayed publicly.",
            "authorized_viewers": "Only authorized administrators handling manual verification can view identity images.",
            "access_security": "Every admin review is audited and uses a private URL that expires after 5 minutes.",
            "retention_days_after_verification": settings.IDENTITY_DOCUMENT_RETENTION_DAYS,
            "deletion": "NIC images are permanently deleted after the retention period. Verification outcome and minimal audit records are retained.",
        },
        "rights": ["Download my data", "Request account deletion", "Delete my account"],
    }


@router.get("/me")
async def get_my_profile(current_user: dict = Depends(get_current_user)):
    return serialize_doc(current_user)


@router.put("/me")
async def update_my_profile(body: UserProfileUpdate, current_user: dict = Depends(get_current_user)):
    update_data = {key: value for key, value in body.model_dump().items() if value is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")
    profile_picture = update_data.get("profile_picture")
    if profile_picture and (
        not is_valid_upload_url(profile_picture)
        or get_file_category(urlparse(profile_picture).path) != "image"
    ):
        raise HTTPException(status_code=400, detail="Profile photo must be a valid uploaded image")
    await users_col.update_one({"_id": current_user["_id"]}, {"$set": update_data})
    updated = await users_col.find_one({"_id": current_user["_id"]})
    return serialize_doc(updated)


@router.put("/me/profile-picture")
async def update_my_profile_picture(upload_id: str, current_user: dict = Depends(get_current_user)):
    """Backward-compatible attachment for an owned, validated profile upload."""
    from bson import ObjectId
    if not ObjectId.is_valid(upload_id):
        raise HTTPException(status_code=400, detail="Invalid profile upload ID")
    upload = await db["uploads.files"].find_one({
        "_id": ObjectId(upload_id),
        "metadata.owner_id": current_user["_id"],
        "metadata.purpose": "profile_picture",
        "metadata.category": "image",
        "metadata.state": {"$in": ["safe", "available"]},
    })
    if not upload:
        raise HTTPException(status_code=422, detail="Profile image is unavailable or failed validation")
    file_url = f'/api/v1/uploads/file/{upload["filename"]}'
    await users_col.update_one({"_id": current_user["_id"]}, {"$set": {"profile_picture": file_url, "updated_at": now_utc()}})
    if current_user.get("role") == "editor":
        await editors_col.update_one({"user_id": current_user["_id"]}, {"$set": {"profile_picture": file_url}})
    return {"success": True, "profile_image_url": file_url, "profile_picture": file_url, "message": "Profile image updated"}


@router.get("/me/data-export")
async def download_my_data(current_user: dict = Depends(get_current_user)):
    user_id = current_user["_id"]
    account = {k: v for k, v in current_user.items() if k not in {"password_hash", "refresh_token"}}
    editor = await editors_col.find_one({"user_id": user_id})
    if editor:
        for field in ("nic_front_key", "nic_ocr_confidence"):
            editor.pop(field, None)
    projects = await requests_col.find({"$or": [{"user_id": user_id}, {"editor_user_id": user_id}]}).to_list(1000)
    project_ids = [str(item["_id"]) for item in projects]
    data = {
        "generated_at": now_utc(), "account": account, "editor_profile": editor,
        "projects": projects,
        "messages": await messages_col.find({"request_id": {"$in": project_ids}, "sender_id": str(user_id)}).to_list(5000),
        "payments": await payments_col.find({"$or": [{"user_id": user_id}, {"editor_user_id": user_id}]}, {"authorization_token": 0}).to_list(1000),
        "reviews": await reviews_col.find({"user_id": user_id}).to_list(1000),
        "identity_audit_events": await identity_audit_logs_col.find({"user_id": user_id}, {"metadata": 0}).to_list(1000),
    }
    await write_audit(user_id, "data_export", "completed")
    return serialize_doc(data)


@router.post("/me/deletion-request")
async def request_account_deletion(body: AccountDeletionBody, current_user: dict = Depends(get_current_user)):
    await users_col.update_one({"_id": current_user["_id"]}, {"$set": {"deletion_requested_at": now_utc(), "deletion_reason": body.reason}})
    await write_audit(current_user["_id"], "account_deletion_request", "received")
    return {"message": "Account deletion request recorded. Confirm deletion when you are ready."}


@account_router.delete("/account")
@router.delete("/me")
async def delete_my_account(
    body: AccountDeletionBody,
    request: Request,
    response: Response,
    current_user: dict = Depends(get_current_user),
):
    logger.info("Account deletion request received endpoint=/api/v1/account user_id=%s role=%s", current_user.get("_id"), current_user.get("role"))
    if current_user.get("role") not in {"user", "editor"}:
        logger.warning("Account deletion rejected stage=role_validation user_id=%s role=%s", current_user.get("_id"), current_user.get("role"))
        raise HTTPException(status_code=403, detail={"code": "ADMIN_SELF_DELETE_FORBIDDEN", "message": "Admin accounts cannot be self-deleted."})
    if current_user.get("is_deleted"):
        raise HTTPException(status_code=410, detail={"code": "ALREADY_DELETED", "message": "Account is already deleted."})

    ip = request.client.host if request.client else "unknown"
    identity = str(current_user["_id"])
    try:
        counts = await get_scope_counts(identity, ip, "delete_account", 15)
    except PyMongoError as exc:
        logger.exception("Account deletion failed stage=rate_limit_database_query user_id=%s exception_type=%s exception=%s", current_user["_id"], type(exc).__name__, exc)
        raise HTTPException(status_code=503, detail={"code": "DATABASE_ERROR", "message": "Account deletion is temporarily unavailable. Please try again."}) from exc
    if counts["email"] >= 5 or counts["ip"] >= 20:
        raise HTTPException(status_code=429, detail={"code": "RATE_LIMITED", "message": "Too many account deletion attempts. Try again later."}, headers={"Retry-After": "900"})
    try:
        await increment_counter(identity, ip, "delete_account", 15)
    except PyMongoError as exc:
        logger.exception("Account deletion failed stage=rate_limit_update user_id=%s exception_type=%s exception=%s", current_user["_id"], type(exc).__name__, exc)
        raise HTTPException(status_code=503, detail={"code": "DATABASE_ERROR", "message": "Account deletion is temporarily unavailable. Please try again."}) from exc

    if current_user.get("password_hash"):
        supplied_password = body.current_password or body.password
        if not supplied_password or not verify_password(supplied_password, current_user["password_hash"]):
            logger.warning("Account deletion rejected stage=password_validation user_id=%s", current_user["_id"])
            raise HTTPException(status_code=401, detail={"code": "INVALID_PASSWORD", "message": "The current password is incorrect."})
        method = "password"
    else:
        if not body.google_credential:
            raise HTTPException(status_code=401, detail={"code": "GOOGLE_REAUTH_REQUIRED", "message": "Fresh Google authentication is required."})
        claims = _verify_google_credential(body.google_credential)
        if claims.get("sub") != current_user.get("google_id") or str(claims.get("email", "")).lower() != str(current_user.get("email", "")).lower():
            raise HTTPException(status_code=401, detail={"code": "GOOGLE_REAUTH_FAILED", "message": "Google re-authentication failed."})
        method = "google"

    logger.info("Account deletion validation passed stage=reauthentication user_id=%s method=%s", current_user["_id"], method)
    try:
        blockers = await deletion_blockers(current_user)
    except PyMongoError as exc:
        logger.exception("Account deletion failed stage=blocker_database_query user_id=%s exception_type=%s exception=%s", current_user["_id"], type(exc).__name__, exc)
        raise HTTPException(status_code=503, detail={"code": "DATABASE_ERROR", "message": "Account deletion is temporarily unavailable. Please try again."}) from exc
    if blockers:
        blocker_text = " ".join(blockers).lower()
        code = "ACTIVE_PROJECT" if "project" in blocker_text else "PENDING_PAYMENT" if "payment" in blocker_text else "ESCROW_HOLD" if "fund" in blocker_text or "payout" in blocker_text else "UNRESOLVED_DISPUTE" if "dispute" in blocker_text else "DELETION_BLOCKED"
        logger.info("Account deletion blocked stage=business_validation user_id=%s code=%s blockers=%s", current_user["_id"], code, len(blockers))
        raise HTTPException(status_code=409, detail={"code": code, "message": blockers[0], "blockers": blockers})

    original_email = current_user.get("email", "")
    editor = await editors_col.find_one({"user_id": current_user["_id"]})
    if editor:
        for key in filter(None, [editor.get("nic_front_key")]):
            if not await delete_identity_key(key):
                logger.error("Account deletion blocked stage=identity_storage_cleanup user_id=%s s3_key=%s", current_user["_id"], key)
                raise HTTPException(status_code=503, detail={"code": "STORAGE_ERROR", "message": "Private identity files could not be deleted. Please try again."})
    try:
        logger.info("Account deletion database operation started user_id=%s", current_user["_id"])
        await purge_otp_cache(original_email)
        await hard_delete_account(current_user, method=method, reason=body.reason, ip_address=ip)
        logger.info("Account deletion database operation completed user_id=%s", current_user["_id"])
    except HTTPException:
        raise
    except PyMongoError as exc:
        logger.exception("Account deletion failed stage=hard_delete user_id=%s exception_type=%s exception=%s", current_user["_id"], type(exc).__name__, exc)
        raise HTTPException(status_code=503, detail={"code": "DATABASE_ERROR", "message": "The account could not be deleted because the database is temporarily unavailable."}) from exc
    except Exception as exc:
        logger.exception("Account deletion failed stage=hard_delete user_id=%s exception_type=%s exception=%s", current_user["_id"], type(exc).__name__, exc)
        raise HTTPException(status_code=500, detail={"code": "ACCOUNT_DELETION_FAILED", "message": "The account could not be deleted. Please try again."}) from exc

    try:
        await disconnect_user(str(current_user["_id"]))
    except Exception as exc:
        logger.exception("Account deleted but Socket.IO disconnect failed user_id=%s exception_type=%s exception=%s", current_user["_id"], type(exc).__name__, exc)
    response.delete_cookie("ez_access_token", path="/")
    response.delete_cookie("ez_refresh_token", path="/")
    try:
        await send_account_deletion_email(original_email)
    except Exception:
        logger.exception("Account deleted but confirmation email failed user_id=%s", current_user["_id"])
    return {"success": True, "message": "Your account has been deleted successfully."}
