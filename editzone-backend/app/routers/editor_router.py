from fastapi import APIRouter, Depends, HTTPException, Query
from bson import ObjectId
from typing import Optional
import re
from urllib.parse import urlparse

from app.db.mongodb import db, uploads_bucket, editors_col, editor_portfolio_items_col, users_col, requests_col, payments_col
from app.config import settings
from app.schemas.schemas import EditorProfileUpdate, PortfolioItemBody, PortfolioItemUpdate
from app.core.security import get_current_user, require_editor
from app.core.validators import get_file_category, is_valid_upload_url
from app.core.utils import now_utc, serialize_doc, serialize_list, oid
from app.core.accounts import ACTIVE_ACCOUNT_FILTER, ACTIVE_EDITOR_FILTER, account_not_available, is_deleted_account
from app.services.media_metadata import MediaMetadataError, gridfs_video_duration

router = APIRouter(prefix="/api/v1/editors", tags=["Editors"])


async def _attach_user_info(editor_doc: dict) -> dict:
    user = await users_col.find_one({"_id": editor_doc["user_id"], **ACTIVE_ACCOUNT_FILTER})
    if not user or user.get("role") != "editor" or is_deleted_account(editor_doc):
        return None
    out = serialize_doc(editor_doc)
    for sensitive_field in (
        "nic_front_key",
        "nic_ocr_confidence",
        "manual_review_reasons",
        "identity_review_note",
        "identity_reviewed_by",
    ):
        out.pop(sensitive_field, None)
    out["username"] = user.get("username")
    items = await editor_portfolio_items_col.find({"editor_id": editor_doc["user_id"], "deleted_at": None}).sort("created_at", -1).to_list(100)
    out["portfolio_items"] = serialize_list(items)
    return out


@router.get("/me/dashboard")
async def get_editor_dashboard(current_user: dict = Depends(require_editor)):
    editor = await editors_col.find_one({"user_id": current_user["_id"]})
    if not editor:
        raise HTTPException(status_code=404, detail="Editor profile not found")

    project_counts = {}
    async for row in requests_col.aggregate([
        {"$match": {"editor_user_id": current_user["_id"]}},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]):
        project_counts[row["_id"]] = row["count"]

    captured = await payments_col.find({
        "editor_user_id": current_user["_id"],
        "payment_type": "project_payment",
        "status": "CAPTURED",
    }).to_list(2000)
    authorized = await payments_col.find({
        "editor_user_id": current_user["_id"],
        "payment_type": "project_payment",
        "status": "AUTHORIZED",
    }).to_list(2000)

    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    month_keys = []
    year, month = now.year, now.month
    for offset in range(5, -1, -1):
        target_month = month - offset
        target_year = year
        while target_month <= 0:
            target_month += 12
            target_year -= 1
        month_keys.append(f"{target_year:04d}-{target_month:02d}")
    monthly_totals = {key: 0.0 for key in month_keys}
    for payment in captured:
        captured_at = payment.get("captured_at") or payment.get("updated_at") or payment.get("created_at")
        if captured_at:
            key = captured_at.strftime("%Y-%m")
            if key in monthly_totals:
                monthly_totals[key] += float(payment.get("editor_earning_amount", 0))

    profile_fields = [
        bool(current_user.get("username")),
        bool(editor.get("bio")),
        bool(editor.get("skills")),
        float(editor.get("hourly_rate") or 0) > 0,
        bool(editor.get("location")),
        bool(editor.get("category")),
        bool(editor.get("profile_picture")),
        bool(editor.get("portfolio_links")),
    ]
    profile_completion = round(sum(profile_fields) / len(profile_fields) * 100)

    hold_cutoff = now - timedelta(days=settings.PAYOUT_HOLD_DAYS)
    pending_captured = sum(
        float(item.get("editor_earning_amount", 0)) for item in captured
        if item.get("captured_at", now) > hold_cutoff
    )
    return {
        "counts": {
            "new_requests": project_counts.get("pending", 0),
            "accepted_projects": project_counts.get("accepted", 0) + project_counts.get("payment_failed", 0),
            "active_projects": sum(project_counts.get(status, 0) for status in ("in_progress", "overdue", "admin_review", "revision_requested", "delivered", "cancel_requested", "disputed", "refund_pending")),
            "completed_projects": sum(project_counts.get(status, 0) for status in ("completed", "rejected", "cancelled", "refunded", "expired")),
        },
        "total_earnings": round(sum(float(item.get("editor_earning_amount", 0)) for item in captured), 2),
        "pending_earnings": round(sum(float(item.get("editor_earning_amount", 0)) for item in authorized) + pending_captured, 2),
        "rating": float(editor.get("rating_avg", 0)),
        "rating_count": int(editor.get("rating_count", 0)),
        "profile_completion": profile_completion,
        "portfolio_count": len(editor.get("portfolio_links", [])),
        "is_available": editor.get("is_available", True),
        "monthly_earnings": [
            {"month": key, "amount": round(amount, 2)}
            for key, amount in monthly_totals.items()
        ],
    }


@router.get("")
async def list_editors(
    category: Optional[str] = Query(None, description="All, Image Editor, TikTok Editor, Video Editor"),
    search: Optional[str] = Query(None),
    min_rate: Optional[float] = None,
    max_rate: Optional[float] = None,
    current_user: dict = Depends(get_current_user),
):
    query = {**ACTIVE_EDITOR_FILTER, "is_available": {"$ne": False}}
    if category and category.lower() != "all":
        query["category"] = category
    if search:
        safe_search = re.escape(search.strip())
        query["$or"] = [
            {"bio": {"$regex": safe_search, "$options": "i"}},
            {"skills": {"$regex": safe_search, "$options": "i"}},
            {"location": {"$regex": safe_search, "$options": "i"}},
        ]
    if min_rate is not None or max_rate is not None:
        rate_filter = {}
        if min_rate is not None:
            rate_filter["$gte"] = min_rate
        if max_rate is not None:
            rate_filter["$lte"] = max_rate
        query["hourly_rate"] = rate_filter

    if search:
        matching_users = await users_col.find(
            {"role": "editor", **ACTIVE_ACCOUNT_FILTER, "username": {"$regex": safe_search, "$options": "i"}},
            {"_id": 1},
        ).to_list(200)
        matching_user_ids = [user["_id"] for user in matching_users]
        if matching_user_ids:
            profile_filters = query.pop("$or")
            query["$or"] = profile_filters + [{"user_id": {"$in": matching_user_ids}}]

    editors = await editors_col.find(query).to_list(200)

    results = [
        attached
        for editor in editors
        if (attached := await _attach_user_info(editor)) is not None
    ]
    return {"editors": results, "count": len(results)}


@router.get("/me/profile")
async def get_my_editor_profile(current_user: dict = Depends(require_editor)):
    editor = await editors_col.find_one({"user_id": current_user["_id"]})
    if not editor:
        raise HTTPException(status_code=404, detail="Editor profile not found")
    return await _attach_user_info(editor)


@router.put("/me/profile")
async def update_my_editor_profile(body: EditorProfileUpdate, current_user: dict = Depends(require_editor)):
    submitted_data = {k: v for k, v in body.model_dump().items() if v is not None}
    if not submitted_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    editor = await editors_col.find_one({"user_id": current_user["_id"]})
    if not editor:
        raise HTTPException(status_code=404, detail="Editor profile not found")

    username = submitted_data.pop("username", None)
    if submitted_data:
        await editors_col.update_one({"_id": editor["_id"]}, {"$set": submitted_data})
    if username is not None:
        await users_col.update_one({"_id": current_user["_id"]}, {"$set": {"username": username}})

    editor = await editors_col.find_one({"user_id": current_user["_id"]})
    return await _attach_user_info(editor)


@router.put("/me/profile-picture")
async def update_profile_picture(upload_id: str, current_user: dict = Depends(require_editor)):
    """Backward-compatible editor route using canonical owned upload metadata."""
    if not ObjectId.is_valid(upload_id):
        raise HTTPException(status_code=400, detail="Invalid profile upload ID")
    upload = await db["uploads.files"].find_one({
        "_id": ObjectId(upload_id), "metadata.owner_id": current_user["_id"],
        "metadata.purpose": "profile_picture", "metadata.category": "image",
        "metadata.state": {"$in": ["safe", "available"]},
    })
    if not upload:
        raise HTTPException(status_code=422, detail="Profile image is unavailable or failed validation")
    file_url = f'/api/v1/uploads/file/{upload["filename"]}'
    await editors_col.update_one({"user_id": current_user["_id"]}, {"$set": {"profile_picture": file_url}})
    await users_col.update_one({"_id": current_user["_id"]}, {"$set": {"profile_picture": file_url}})
    return {"success": True, "message": "Profile image updated", "profile_image_url": file_url, "profile_picture": file_url}


@router.post("/me/portfolio", status_code=201)
async def add_portfolio_item(body: PortfolioItemBody, current_user: dict = Depends(require_editor)):
    profile = await editors_col.find_one({"user_id": current_user["_id"]}, {"portfolio_links": 1})
    if not profile:
        raise HTTPException(status_code=404, detail="Editor profile not found")
    if await editor_portfolio_items_col.count_documents({"editor_id": current_user["_id"], "deleted_at": None}) >= 50:
        raise HTTPException(status_code=400, detail="Portfolio is limited to 50 items")
    upload = await db["uploads.files"].find_one({
        "_id": oid(body.upload_id), "metadata.owner_id": current_user["_id"],
        "metadata.purpose": "editor_portfolio", "metadata.scan_status": "safe", "metadata.state": "safe",
    })
    if not upload:
        raise HTTPException(status_code=422, detail="Portfolio media is unavailable or still being scanned")
    metadata = upload.get("metadata", {})
    duration = None
    if metadata.get("category") == "video":
        try:
            duration = await gridfs_video_duration(upload["_id"])
        except MediaMetadataError:
            raise HTTPException(status_code=422, detail="The reel video could not be inspected")
        if duration > settings.MAX_REEL_VIDEO_DURATION_SECONDS:
            raise HTTPException(status_code=422, detail="Reel is longer than 90 seconds")
    now = now_utc()
    file_url = f'/api/v1/uploads/file/{upload["filename"]}'
    doc = {"editor_id": current_user["_id"], "upload_id": upload["_id"], "storage_key": upload["filename"],
           "url": file_url, "thumbnail_url": None, "title": body.title, "description": body.description,
           "skills": body.skills, "media_type": metadata.get("category"), "mime_type": metadata.get("content_type"),
           "size": metadata.get("size") or upload.get("length", 0), "duration": duration,
           "created_at": now, "updated_at": now, "deleted_at": None}
    try:
        result = await editor_portfolio_items_col.insert_one(doc)
    except Exception:
        # The upload belongs to this editor and this purpose, and no portfolio
        # record was created. Remove only that newly orphaned GridFS object.
        await uploads_bucket.delete(upload["_id"])
        raise
    doc["_id"] = result.inserted_id
    # Keep the legacy URL projection populated for old clients and records.
    await editors_col.update_one(
        {"user_id": current_user["_id"]}, {"$push": {"portfolio_links": file_url}}
    )
    return serialize_doc(doc)


@router.patch("/me/portfolio/{item_id}")
async def update_portfolio_item(item_id: str, body: PortfolioItemUpdate, current_user: dict = Depends(require_editor)):
    changes = {key: value for key, value in body.model_dump().items() if value is not None}
    if not changes:
        raise HTTPException(status_code=422, detail="No portfolio changes supplied")
    changes["updated_at"] = now_utc()
    item = await editor_portfolio_items_col.find_one_and_update(
        {"_id": oid(item_id), "editor_id": current_user["_id"], "deleted_at": None}, {"$set": changes}, return_document=True,
    )
    if not item:
        raise HTTPException(status_code=404, detail="Portfolio item not found or not owned by you")
    return serialize_doc(item)


@router.delete("/me/portfolio/{item_id}")
async def delete_portfolio_item(item_id: str, current_user: dict = Depends(require_editor)):
    item = await editor_portfolio_items_col.find_one_and_update(
        {"_id": oid(item_id), "editor_id": current_user["_id"], "deleted_at": None},
        {"$set": {"deleted_at": now_utc(), "updated_at": now_utc()}}, return_document=True,
    )
    if not item:
        raise HTTPException(status_code=404, detail="Portfolio item not found or not owned by you")
    await editors_col.update_one({"user_id": current_user["_id"]}, {"$pull": {"portfolio_links": item["url"]}})
    # This upload purpose is single-owner and single-record. Delete only after
    # the record is no longer publicly referenced.
    if not await editor_portfolio_items_col.find_one({"upload_id": item["upload_id"], "deleted_at": None}):
        await uploads_bucket.delete(item["upload_id"])
    return {"message": "Portfolio item deleted"}


@router.get("/{editor_id}")
async def get_editor_profile(editor_id: str, current_user: dict = Depends(get_current_user)):
    editor = await editors_col.find_one({"_id": oid(editor_id), **ACTIVE_EDITOR_FILTER})
    if not editor:
        raise account_not_available()
    attached = await _attach_user_info(editor)
    if attached is None:
        raise account_not_available()
    await editors_col.update_one({"_id": editor["_id"]}, {"$inc": {"total_views": 1}})
    attached["total_views"] = int(attached.get("total_views", 0)) + 1
    return attached
