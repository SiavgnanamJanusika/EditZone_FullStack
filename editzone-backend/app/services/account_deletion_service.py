import asyncio
import logging

import boto3
from bson import ObjectId
from fastapi import HTTPException

from app.config import settings
from app.core.utils import now_utc
from app.db.mongodb import (
    account_deletion_audit_logs_col,
    auth_rate_limits_col,
    auth_security_events_col,
    auth_sessions_col,
    db,
    disputes_col,
    editors_col,
    editor_statuses_col,
    identity_audit_logs_col,
    identity_rate_limits_col,
    media_agreements_col,
    messages_col,
    multipart_uploads_col,
    notifications_col,
    otps_col,
    payments_col,
    requests_col,
    reviews_col,
    status_likes_col,
    status_views_col,
    users_col,
    uploads_bucket,
)
from app.services.auth_throttle_service import throttle_keys

logger = logging.getLogger(__name__)
legacy_editor_profiles_col = db["editor_profiles"]

ACTIVE_PROJECT_STATUSES = {
    "accepted", "payment_failed", "in_progress", "overdue", "admin_review",
    "revision_requested", "delivered", "cancel_requested", "disputed",
    "refund_pending",
}
REMOVABLE_REQUEST_STATUSES = {"pending", "rejected", "expired"}


async def deletion_blockers(user: dict) -> list[str]:
    user_id = user["_id"]
    participant = {"$or": [{"user_id": user_id}, {"editor_user_id": user_id}]}
    blockers = []
    if await requests_col.count_documents({**participant, "status": {"$in": list(ACTIVE_PROJECT_STATUSES)}}):
        blockers.append("Complete or cancel all active projects")
    if await payments_col.count_documents({**participant, "status": {"$in": ["PENDING", "AUTHORIZED"]}}):
        blockers.append("Resolve pending or protected escrow payments")
    if await payments_col.count_documents({**participant, "dispute_status": "OPEN"}):
        blockers.append("Resolve open payment disputes")
    if await disputes_col.count_documents({"$or": [{"opened_by": user_id}, {"against_user_id": user_id}], "status": {"$in": ["open", "OPEN"]}}):
        blockers.append("Resolve open disputes")
    return blockers


def _s3_client():
    return boto3.client(
        "s3",
        region_name=settings.AWS_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
    )


async def _remove_owned_uploads(user_id: ObjectId, removed_request_ids: set[str]) -> dict:
    gridfs_deleted = 0
    gridfs_preserved = 0
    async for upload in db["uploads.files"].find({"metadata.owner_id": user_id}, {"metadata": 1}):
        metadata = upload.get("metadata") or {}
        request_id = metadata.get("request_id")
        personal_only = not request_id or request_id in removed_request_ids
        if personal_only:
            await uploads_bucket.delete(upload["_id"])
            gridfs_deleted += 1
        else:
            await db["uploads.files"].update_one(
                {"_id": upload["_id"], "metadata.owner_id": user_id},
                {"$set": {"metadata.owner_deleted": True}, "$unset": {"metadata.owner_id": ""}},
            )
            gridfs_preserved += 1

    s3_deleted = 0
    s3_preserved = 0
    s3 = None
    async for upload in multipart_uploads_col.find({"owner_id": user_id}):
        request_id = upload.get("request_id")
        personal_only = not request_id or str(request_id) in removed_request_ids
        if personal_only:
            s3 = s3 or _s3_client()
            if upload.get("state") == "uploading":
                await asyncio.to_thread(s3.abort_multipart_upload, Bucket=upload["bucket"], Key=upload["key"], UploadId=upload["upload_id"])
            else:
                await asyncio.to_thread(s3.delete_object, Bucket=upload["bucket"], Key=upload["key"])
            await multipart_uploads_col.delete_one({"_id": upload["_id"], "owner_id": user_id})
            s3_deleted += 1
        else:
            await multipart_uploads_col.update_one(
                {"_id": upload["_id"], "owner_id": user_id},
                {"$set": {"owner_deleted": True}, "$unset": {"owner_id": ""}},
            )
            s3_preserved += 1
    return {
        "gridfs_deleted": gridfs_deleted,
        "gridfs_preserved": gridfs_preserved,
        "s3_deleted": s3_deleted,
        "s3_preserved": s3_preserved,
    }


async def hard_delete_account(user: dict, *, method: str, reason: str | None, ip_address: str) -> dict:
    """Permanently remove one user/editor's personal data without touching admins."""
    if user.get("role") not in {"user", "editor"}:
        raise HTTPException(status_code=403, detail="Admin accounts cannot be deleted by this workflow")
    user_id = user["_id"]
    email = str(user.get("email", "")).strip().lower()
    now = now_utc()

    pending = await requests_col.find(
        {"$or": [{"user_id": user_id}, {"editor_user_id": user_id}], "status": {"$in": list(REMOVABLE_REQUEST_STATUSES)}},
        {"_id": 1},
    ).to_list(10000)
    removed_request_ids = {str(item["_id"]) for item in pending}
    storage = await _remove_owned_uploads(user_id, removed_request_ids)
    owned_status_ids = [item["_id"] for item in await editor_statuses_col.find({"editor_id": user_id}, {"_id": 1}).to_list(10000)]
    if owned_status_ids:
        await status_likes_col.delete_many({"status_id": {"$in": owned_status_ids}})
        await status_views_col.delete_many({"status_id": {"$in": owned_status_ids}})
    await editor_statuses_col.delete_many({"editor_id": user_id})
    await status_likes_col.delete_many({"user_id": user_id})
    await status_views_col.delete_many({"viewer_id": user_id})

    # Revoke first. Even if a later cleanup operation fails, old refresh tokens
    # and authenticated sessions stop working immediately.
    await auth_sessions_col.update_many(
        {"user_id": user_id, "revoked_at": None},
        {"$set": {"revoked_at": now, "revoke_reason": "account_deleted"}},
    )
    await auth_sessions_col.delete_many({"user_id": user_id})

    if removed_request_ids:
        await messages_col.delete_many({"request_id": {"$in": list(removed_request_ids)}})
        await media_agreements_col.delete_many({"request_id": {"$in": list(removed_request_ids)}})
        await requests_col.delete_many({"_id": {"$in": [ObjectId(value) for value in removed_request_ids]}})

    # Preserve completed conversation/project history while removing personal display data.
    await messages_col.update_many(
        {"receiver_id": str(user_id)},
        {
            "$set": {"receiver_deleted": True, "receiver_display_name": "Deleted User"},
            "$unset": {"receiver_id": ""},
        },
    )
    await messages_col.update_many(
        {"sender_id": str(user_id)},
        {
            "$set": {"sender_deleted": True, "sender_display_name": "Deleted User"},
            "$unset": {"sender_id": "", "client_message_id": ""},
        },
    )
    await requests_col.update_many(
        {"user_id": user_id},
        {
            "$set": {"user_id": None, "user_deleted": True, "user_display_name": "Deleted User"},
            "$unset": {"proposal.created_by": "", "cancel_requested_by": ""},
        },
    )
    await requests_col.update_many(
        {"editor_user_id": user_id},
        {
            "$set": {
                "editor_id": None,
                "editor_user_id": None,
                "editor_deleted": True,
                "editor_display_name": "Deleted User",
            },
            "$unset": {"proposal.created_by": "", "cancel_requested_by": ""},
        },
    )
    await reviews_col.update_many(
        {"user_id": user_id},
        {"$set": {"user_id": None, "reviewer_deleted": True, "reviewer_display_name": "Deleted User"}},
    )
    await reviews_col.update_many(
        {"editor_user_id": user_id},
        {"$set": {"editor_user_id": None, "editor_deleted": True, "editor_display_name": "Deleted User"}},
    )

    deletion_counts = {}
    personal_collections = {
        "editor_profiles": (editors_col, {"user_id": user_id}),
        "notifications": (notifications_col, {"user_id": user_id}),
        "otps": (otps_col, {"email": email}),
        "identity_rate_limits": (identity_rate_limits_col, {"user_id": user_id}),
        "identity_audit_logs": (identity_audit_logs_col, {"user_id": user_id}),
        "auth_security_events": (auth_security_events_col, {"user_id": user_id}),
        "media_agreements": (media_agreements_col, {"user_id": user_id}),
        "legacy_editor_profiles": (legacy_editor_profiles_col, {"user_id": {"$in": [user_id, str(user_id)]}}),
    }
    for name, (collection, query) in personal_collections.items():
        result = await collection.delete_many(query)
        deletion_counts[name] = result.deleted_count

    email_key, _ = throttle_keys(email, ip_address)
    account_key, _ = throttle_keys(str(user_id), ip_address)
    await auth_rate_limits_col.delete_many({"key": {"$in": [email_key, account_key]}})

    # This minimal system audit contains no email, NIC, profile data, reason, or credentials.
    await account_deletion_audit_logs_col.insert_one({
        "account_id": user_id,
        "role": user["role"],
        "deleted_at": now,
        "deletion_method": method,
        "personal_data_purged": True,
    })
    deleted = await users_col.delete_one({"_id": user_id, "role": {"$in": ["user", "editor"]}})
    if deleted.deleted_count != 1:
        raise HTTPException(status_code=410, detail="Account is already deleted or unavailable")
    logger.info("Personal account data permanently deleted user_id=%s role=%s counts=%s storage=%s", user_id, user["role"], deletion_counts, storage)
    return {"deleted": True, "collections": deletion_counts, "storage": storage}


# Kept as a compatibility import for older callers while using hard deletion.
soft_delete_account = hard_delete_account
