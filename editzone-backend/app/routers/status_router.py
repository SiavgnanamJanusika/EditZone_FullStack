import logging
from datetime import timedelta

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from app.config import settings
from app.core.accounts import ACTIVE_ACCOUNT_FILTER
from app.core.security import get_current_user, require_editor
from app.core.utils import now_utc, oid, serialize_doc
from app.db.mongodb import (
    db, editor_portfolio_items_col, editor_statuses_col, editors_col, status_likes_col,
    status_views_col, uploads_bucket, users_col,
)
from app.services.media_metadata import MediaMetadataError, gridfs_video_duration
from app.sockets.socket_manager import sio

router = APIRouter(prefix="/api/v1/statuses", tags=["Editor Statuses"])
logger = logging.getLogger(__name__)


class StatusCreate(BaseModel):
    upload_id: str
    caption: str = Field(default="", max_length=settings.STATUS_CAPTION_MAX_LENGTH)


def _active_filter(now=None):
    return {"is_active": True, "expires_at": {"$gt": now or now_utc()}}


async def _status_or_404(status_id: str, *, active=True):
    query = {"_id": oid(status_id)}
    if active:
        query.update(_active_filter())
    status = await editor_statuses_col.find_one(query)
    if not status:
        raise HTTPException(status_code=404, detail="Status is unavailable or has expired")
    return status


async def _resolve_editor_user_id(editor_id: str) -> ObjectId:
    candidate = oid(editor_id)
    profile = await editors_col.find_one({"_id": candidate}, {"user_id": 1})
    return profile["user_id"] if profile else candidate


async def _relationship_counts(status_ids: list[ObjectId]) -> tuple[dict, dict]:
    if not status_ids:
        return {}, {}
    pipeline = [
        {"$match": {"status_id": {"$in": status_ids}}},
        {"$group": {"_id": "$status_id", "count": {"$sum": 1}}},
    ]
    likes = await status_likes_col.aggregate(pipeline).to_list(len(status_ids))
    views = await status_views_col.aggregate(pipeline).to_list(len(status_ids))
    return ({item["_id"]: item["count"] for item in likes}, {item["_id"]: item["count"] for item in views})


async def _serialize_statuses(docs, viewer):
    if not docs:
        return []
    editor_ids = list({doc["editor_id"] for doc in docs})
    users = await users_col.find(
        {"_id": {"$in": editor_ids}, "role": "editor", **ACTIVE_ACCOUNT_FILTER},
        {"username": 1},
    ).to_list(len(editor_ids))
    profiles = await editors_col.find(
        {"user_id": {"$in": editor_ids}}, {"user_id": 1, "profile_picture": 1}
    ).to_list(len(editor_ids))
    user_map = {item["_id"]: item for item in users}
    profile_map = {item["user_id"]: item for item in profiles}
    status_ids = [doc["_id"] for doc in docs]
    like_counts, view_counts = await _relationship_counts(status_ids)
    liked = {item["status_id"] for item in await status_likes_col.find(
        {"status_id": {"$in": status_ids}, "user_id": viewer["_id"]}, {"status_id": 1}
    ).to_list(len(status_ids))}
    viewed = {item["status_id"] for item in await status_views_col.find(
        {"status_id": {"$in": status_ids}, "viewer_id": viewer["_id"]}, {"status_id": 1}
    ).to_list(len(status_ids))}
    output = []
    for doc in docs:
        user = user_map.get(doc["editor_id"])
        if not user:
            continue
        profile = profile_map.get(doc["editor_id"], {})
        item = serialize_doc(doc)
        item.pop("upload_id", None)
        item["editor"] = {
            "id": str(doc["editor_id"]), "name": user.get("username", "Editor"),
            "profile_id": str(profile.get("_id", "")),
            "profile_image": profile.get("profile_picture", ""),
        }
        item["like_count"] = like_counts.get(doc["_id"], 0)
        item["view_count"] = view_counts.get(doc["_id"], 0)
        item["is_liked_by_me"] = doc["_id"] in liked
        item["liked_by_me"] = item["is_liked_by_me"]
        item["is_viewed_by_me"] = doc["_id"] in viewed
        item["is_owner"] = doc["editor_id"] == viewer["_id"]
        item["owner_id"] = str(doc["editor_id"])
        item["can_delete"] = item["is_owner"] or viewer.get("role") == "admin"
        output.append(item)
    return output


@router.post("", status_code=201)
async def create_status(body: StatusCreate, current_user: dict = Depends(require_editor)):
    upload_id = oid(body.upload_id)
    upload = await db["uploads.files"].find_one({
        "_id": upload_id, "metadata.owner_id": current_user["_id"],
        "metadata.purpose": "editor_status", "metadata.scan_status": "safe",
        "metadata.state": "safe",
    })
    if not upload:
        raise HTTPException(status_code=422, detail="Status media is unavailable or still being scanned")
    metadata = upload.get("metadata") or {}
    if metadata.get("category") not in {"image", "video"}:
        raise HTTPException(status_code=415, detail="Unsupported status media")
    duration_seconds = None
    if metadata.get("category") == "video":
        try:
            duration_seconds = await gridfs_video_duration(upload_id)
        except MediaMetadataError:
            raise HTTPException(status_code=422, detail={"code": "STATUS_VIDEO_INVALID", "message": "The status video could not be inspected."})
        if duration_seconds > settings.MAX_STATUS_VIDEO_DURATION_SECONDS:
            await db["uploads.files"].update_one({"_id": upload_id}, {"$set": {
                "metadata.state": "rejected", "metadata.scan_status": "rejected",
                "metadata.rejection_reason": "status_video_too_long",
            }})
            raise HTTPException(status_code=422, detail={"code": "STATUS_VIDEO_TOO_LONG", "message": "Status videos can be up to 1 minute 30 seconds."})
    now = now_utc()
    doc = {
        "editor_id": current_user["_id"], "upload_id": upload_id,
        "media_url": f'/api/v1/uploads/file/{upload["filename"]}',
        "media_type": metadata["category"], "caption": body.caption.strip(),
        "duration_seconds": duration_seconds,
        "created_at": now, "expires_at": now + timedelta(hours=settings.STATUS_LIFETIME_HOURS),
        "is_active": True, "view_count": 0, "like_count": 0,
    }
    try:
        result = await editor_statuses_col.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="This media is already attached to a status")
    doc["_id"] = result.inserted_id
    payload = (await _serialize_statuses([doc], current_user))[0]
    await sio.emit("status_created", payload)
    return payload


@router.get("")
async def list_statuses(limit: int = Query(100, ge=1, le=200), current_user: dict = Depends(get_current_user)):
    docs = await editor_statuses_col.find(_active_filter()).sort("created_at", 1).limit(limit).to_list(limit)
    return {"statuses": await _serialize_statuses(docs, current_user)}


@router.get("/mine")
async def my_statuses(current_user: dict = Depends(require_editor)):
    docs = await editor_statuses_col.find({"editor_id": current_user["_id"], **_active_filter()}).sort("created_at", 1).to_list(100)
    return {"statuses": await _serialize_statuses(docs, current_user)}


@router.get("/editor/{editor_id}")
async def editor_statuses(editor_id: str, current_user: dict = Depends(get_current_user)):
    editor_user_id = await _resolve_editor_user_id(editor_id)
    docs = await editor_statuses_col.find({"editor_id": editor_user_id, **_active_filter()}).sort("created_at", 1).to_list(100)
    return {"statuses": await _serialize_statuses(docs, current_user)}


@router.get("/{status_id}")
async def get_status(status_id: str, current_user: dict = Depends(get_current_user)):
    return (await _serialize_statuses([await _status_or_404(status_id)], current_user))[0]


@router.delete("/{status_id}")
async def delete_status(status_id: str, current_user: dict = Depends(get_current_user)):
    status = await editor_statuses_col.find_one({"_id": oid(status_id)})
    if not status:
        raise HTTPException(status_code=404, detail="Status not found")
    if status["editor_id"] != current_user["_id"] and current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only the status owner can delete it")
    deleted = await editor_statuses_col.delete_one({"_id": status["_id"]})
    if not deleted.deleted_count:
        raise HTTPException(status_code=404, detail="Status not found")
    await status_likes_col.delete_many({"status_id": status["_id"]})
    await status_views_col.delete_many({"status_id": status["_id"]})
    upload_id = status.get("upload_id")
    media_deleted = False
    if upload_id:
        shared_status = await editor_statuses_col.find_one({"upload_id": upload_id}, {"_id": 1})
        shared_portfolio = await editor_portfolio_items_col.find_one({"upload_id": upload_id}, {"_id": 1})
        if not shared_status and not shared_portfolio:
            try:
                await uploads_bucket.delete(upload_id)
                media_deleted = True
            except Exception as exc:
                logger.warning("STATUS_MEDIA_CLEANUP_FAILED status_id=%s error_type=%s", status_id, type(exc).__name__)
    logger.info("STATUS_DELETED status_id=%s actor_id=%s media_deleted=%s", status_id, current_user["_id"], media_deleted)
    await sio.emit("status_deleted", {"status_id": status_id, "owner_id": str(status["editor_id"])})
    return {"message": "Status deleted successfully.", "status_id": status_id, "media_deleted": media_deleted}


@router.post("/{status_id}/view")
async def record_view(status_id: str, current_user: dict = Depends(get_current_user)):
    status = await _status_or_404(status_id)
    try:
        await status_views_col.insert_one({"status_id": status["_id"], "viewer_id": current_user["_id"], "viewed_at": now_utc()})
        await editor_statuses_col.update_one({"_id": status["_id"]}, {"$inc": {"view_count": 1}})
    except DuplicateKeyError:
        pass
    count = await status_views_col.count_documents({"status_id": status["_id"]})
    return {"view_count": count, "is_viewed_by_me": True}


@router.put("/{status_id}/like")
@router.post("/{status_id}/like")
async def like_status(status_id: str, current_user: dict = Depends(get_current_user)):
    status = await _status_or_404(status_id)
    if current_user.get("role") in {"editor", "admin"}:
        raise HTTPException(status_code=403, detail="Only clients can like editor statuses")
    if status["editor_id"] == current_user["_id"]:
        raise HTTPException(status_code=403, detail="You cannot like your own status")
    try:
        await status_likes_col.insert_one({"status_id": status["_id"], "user_id": current_user["_id"], "created_at": now_utc()})
        await editor_statuses_col.update_one({"_id": status["_id"]}, {"$inc": {"like_count": 1}})
    except DuplicateKeyError:
        pass
    count = await status_likes_col.count_documents({"status_id": status["_id"]})
    await editor_statuses_col.update_one({"_id": status["_id"]}, {"$set": {"like_count": count}})
    payload = {"status_id": status_id, "like_count": count, "is_liked_by_me": True, "liked_by_me": True}
    await sio.emit("status_like_updated", payload)
    return payload


@router.delete("/{status_id}/like")
async def unlike_status(status_id: str, current_user: dict = Depends(get_current_user)):
    status = await _status_or_404(status_id)
    if current_user.get("role") in {"editor", "admin"}:
        raise HTTPException(status_code=403, detail="Only clients can like editor statuses")
    deleted = await status_likes_col.delete_one({"status_id": status["_id"], "user_id": current_user["_id"]})
    if deleted.deleted_count:
        await editor_statuses_col.update_one({"_id": status["_id"], "like_count": {"$gt": 0}}, {"$inc": {"like_count": -1}})
    count = await status_likes_col.count_documents({"status_id": status["_id"]})
    await editor_statuses_col.update_one({"_id": status["_id"]}, {"$set": {"like_count": count}})
    payload = {"status_id": status_id, "like_count": count, "is_liked_by_me": False, "liked_by_me": False}
    await sio.emit("status_like_updated", payload)
    return payload


async def _people(status_id: str, current_user: dict, kind: str):
    status = await editor_statuses_col.find_one({"_id": oid(status_id)})
    if not status:
        raise HTTPException(status_code=404, detail="Status not found")
    if status["editor_id"] != current_user["_id"] and current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Status insights are private to the owner")
    collection, field, time_field = (status_likes_col, "user_id", "created_at") if kind == "likes" else (status_views_col, "viewer_id", "viewed_at")
    relations = await collection.find({"status_id": status["_id"]}).sort(time_field, -1).limit(500).to_list(500)
    ids = [item[field] for item in relations]
    users = await users_col.find({"_id": {"$in": ids}, **ACTIVE_ACCOUNT_FILTER}, {"username": 1, "profile_picture": 1}).to_list(len(ids)) if ids else []
    user_map = {item["_id"]: item for item in users}
    editor_profiles = await editors_col.find({"user_id": {"$in": ids}}, {"user_id": 1, "profile_picture": 1}).to_list(len(ids)) if ids else []
    editor_image_map = {item["user_id"]: item.get("profile_picture", "") for item in editor_profiles}
    people = [{"id": str(uid), "name": user_map[uid].get("username", "User"), "profile_image": editor_image_map.get(uid) or user_map[uid].get("profile_picture", "")} for uid in ids if uid in user_map]
    return {"users": people, "count": len(people)}


@router.get("/{status_id}/likes")
async def status_likes(status_id: str, current_user: dict = Depends(get_current_user)):
    return await _people(status_id, current_user, "likes")


@router.get("/{status_id}/viewers")
async def status_viewers(status_id: str, current_user: dict = Depends(get_current_user)):
    return await _people(status_id, current_user, "views")
