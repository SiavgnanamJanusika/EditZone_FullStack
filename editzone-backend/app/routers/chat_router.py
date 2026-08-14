from datetime import datetime, timedelta, timezone
import hashlib
import logging
import secrets

import jwt

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.db.mongodb import messages_col, requests_col, users_col, editors_col, chat_reports_col, notifications_col, chat_moderation_logs_col
from app.core.security import get_current_user
from app.core.utils import serialize_list, serialize_doc, oid, now_utc
from app.sockets.socket_manager import emit_message_notification, sio
from app.config import settings
from app.services.chat_moderation import BLOCK_MESSAGE, contact_violation, moderation_fingerprint
from app.services.chat_rate_limiter import allow_chat_event

router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])
logger = logging.getLogger(__name__)


CHAT_STATUSES = ("accepted", "in_progress", "overdue", "admin_review", "revision_requested", "delivered", "cancel_requested", "disputed", "refund_pending", "completed")
REPORT_REASONS = {"spam", "harassment", "fraud", "inappropriate_content", "off_platform_contact", "file_misuse", "copyright", "other"}


class ChatReportBody(BaseModel):
    reason: str
    description: str = Field(default="", max_length=2000)
    message_ids: list[str] = Field(default_factory=list, max_length=20)


class RestChatMessageBody(BaseModel):
    request_id: str
    text: str = Field(min_length=1, max_length=settings.MAX_TEXT_MESSAGE_LENGTH)
    client_message_id: str | None = Field(default=None, max_length=64)


async def _conversation(request_id: str, current_user: dict, *, admin=False):
    req_doc = await requests_col.find_one({"_id": oid(request_id)})
    if not req_doc:
        raise HTTPException(status_code=404, detail="Request not found")
    if current_user["_id"] not in (req_doc["user_id"], req_doc["editor_user_id"]) and not (admin and current_user["role"] == "admin"):
        raise HTTPException(status_code=403, detail="Not authorized to view this chat")
    if req_doc["status"] not in CHAT_STATUSES:
        raise HTTPException(status_code=400, detail="Chat is only available after the request is accepted")
    return req_doc


@router.post("/message", status_code=201)
async def create_rest_chat_message(
    body: RestChatMessageBody,
    current_user: dict = Depends(get_current_user),
):
    """Secure REST fallback; Socket.IO remains the primary chat workflow."""
    project = await _conversation(body.request_id, current_user)
    if project.get("status") in {"completed", "cancelled", "refunded", "expired", "rejected"}:
        raise HTTPException(status_code=409, detail="This project is completed and the chat is closed")
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Message cannot be empty")
    violation = contact_violation(text)
    if violation:
        code = "CONTACT_LINK_NOT_ALLOWED" if violation == "contact_link" else "PHONE_NUMBER_NOT_ALLOWED"
        try:
            await chat_moderation_logs_col.insert_one({
                "request_id": body.request_id,
                "user_id": current_user["_id"],
                "reason": violation,
                "fingerprint": moderation_fingerprint(text),
                "created_at": now_utc(),
            })
        except PyMongoError:
            logger.exception("Unable to persist REST chat moderation event request=%s", body.request_id)
        raise HTTPException(
            status_code=422,
            detail={"code": code, "message": BLOCK_MESSAGE},
        )
    user_id = str(current_user["_id"])
    if not await allow_chat_event("message-user", user_id, settings.CHAT_MESSAGE_RATE_LIMIT):
        raise HTTPException(status_code=429, detail={"code": "RATE_LIMITED", "message": "You are sending messages too quickly. Please wait."})
    client_message_id = (body.client_message_id or "").strip()
    if client_message_id and not all(char.isalnum() or char in "-_" for char in client_message_id):
        raise HTTPException(status_code=422, detail="Invalid client message ID")
    if client_message_id:
        existing = await messages_col.find_one({
            "request_id": body.request_id,
            "sender_id": user_id,
            "client_message_id": client_message_id,
        })
        if existing:
            return {"success": True, "duplicate": True, "message": serialize_doc(existing)}
    receiver_id = project["editor_user_id"] if current_user["_id"] == project["user_id"] else project["user_id"]
    doc = {
        "request_id": body.request_id,
        "sender_id": user_id,
        "receiver_id": str(receiver_id),
        "text": text,
        "file_url": None,
        "file_type": None,
        "message_type": "text",
        "delivery_status": "sent",
        "delivered_at": None,
        "read_at": None,
        "created_at": now_utc(),
    }
    if client_message_id:
        doc["client_message_id"] = client_message_id
    try:
        result = await messages_col.insert_one(doc)
    except DuplicateKeyError:
        existing = await messages_col.find_one({
            "request_id": body.request_id, "sender_id": user_id,
            "client_message_id": client_message_id,
        })
        return {"success": True, "duplicate": True, "message": serialize_doc(existing)}
    doc["_id"] = result.inserted_id
    await sio.emit("new_message", serialize_doc(doc), room=f"chat_{body.request_id}")
    try:
        await emit_message_notification(doc)
    except Exception:
        logger.exception("Unable to emit REST message notification for request %s", body.request_id)
    return {"success": True, "message": serialize_doc(doc)}


@router.get("/{request_id}/messages")
async def get_chat_history(
    request_id: str,
    before: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    req_doc = await _conversation(request_id, current_user)
    if req_doc.get("media_access_revoked_at"):
        return {"messages": [], "has_more": False, "next_before": None, "chat_closed": True, "closing_reason": "Payment completed successfully. This conversation is now closed."}
    query = {"request_id": request_id, "deleted_for": {"$ne": str(current_user["_id"])}}
    if before:
        query["created_at"] = {"$lt": before}

    msgs = await messages_col.find(query).sort("created_at", -1).limit(limit + 1).to_list(limit + 1)
    has_more = len(msgs) > limit
    msgs = msgs[:limit]
    msgs.reverse()
    return {
        "messages": serialize_list(msgs),
        "has_more": has_more,
        "next_before": msgs[0].get("created_at").isoformat() if has_more and msgs else None,
        "chat_closed": req_doc["status"] in ("completed", "cancelled", "refunded", "expired", "rejected"),
    }


@router.get("")
async def conversation_list(current_user: dict = Depends(get_current_user)):
    member_field = "editor_user_id" if current_user.get("role") == "editor" else "user_id"
    projects = await requests_col.find({member_field: current_user["_id"], "status": {"$in": CHAT_STATUSES}}).sort("status_updated_at", -1).limit(100).to_list(100)
    items = []
    for project in projects:
        request_id = str(project["_id"])
        latest = await messages_col.find_one({"request_id": request_id}, sort=[("created_at", -1)])
        unread = await messages_col.count_documents({"request_id": request_id, "receiver_id": str(current_user["_id"]), "read_at": None})
        items.append({
            "request_id": request_id, "project_title": project.get("project_title", "EditZone Project"),
            "status": project.get("status"), "latest_message": serialize_doc(latest) if latest else None,
            "unread_count": unread,
        })
    return {"conversations": items}


@router.get("/{request_id}/participant")
async def participant_profile(request_id: str, current_user: dict = Depends(get_current_user)):
    project = await _conversation(request_id, current_user)
    other_id = project["editor_user_id"] if current_user["_id"] == project["user_id"] else project["user_id"]
    other = await users_col.find_one({"_id": other_id}, {"username": 1, "role": 1, "profile_picture": 1, "created_at": 1, "is_email_verified": 1})
    if not other:
        raise HTTPException(status_code=404, detail="Participant is unavailable")
    result = {
        "display_name": other.get("username", "EditZone member"), "role": other.get("role"),
        "profile_picture": other.get("profile_picture", ""), "member_since": other.get("created_at"),
        "verified": bool(other.get("is_email_verified")), "project_title": project.get("project_title"),
        "project_status": project.get("status"),
    }
    if other.get("role") == "editor":
        profile = await editors_col.find_one({"user_id": other_id}, {"skills": 1, "bio": 1, "rating_avg": 1, "rating_count": 1, "category": 1, "identity_verification_status": 1})
        if profile:
            result.update({key: profile.get(key) for key in ("skills", "bio", "rating_avg", "rating_count", "category", "identity_verification_status")})
    return serialize_doc(result)


@router.post("/{request_id}/reports", status_code=201)
async def report_chat(request_id: str, body: ChatReportBody, current_user: dict = Depends(get_current_user)):
    project = await _conversation(request_id, current_user)
    if body.reason not in REPORT_REASONS:
        raise HTTPException(status_code=422, detail="Unsupported report reason")
    reported_id = project["editor_user_id"] if current_user["_id"] == project["user_id"] else project["user_id"]
    evidence_ids = [ObjectId(value) for value in body.message_ids if ObjectId.is_valid(value)]
    evidence = await messages_col.find({"_id": {"$in": evidence_ids}, "request_id": request_id}, {"_id": 1}).to_list(20)
    dedupe = hashlib.sha256(f"{request_id}:{current_user['_id']}:{reported_id}:{body.reason}".encode()).hexdigest()
    doc = {
        "dedupe_key": dedupe, "request_id": request_id, "reporter_id": current_user["_id"],
        "reported_user_id": reported_id, "reason": body.reason, "description": body.description.strip(),
        "message_ids": [item["_id"] for item in evidence], "status": "open", "created_at": now_utc(),
    }
    try:
        result = await chat_reports_col.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="An open report for this reason already exists")
    await notifications_col.insert_one({"user_id": None, "audience": "admin", "title": "New chat report", "body": f"Report requires review: {body.reason}", "request_id": request_id, "is_read": False, "created_at": now_utc()})
    return {"id": str(result.inserted_id), "message": "Report submitted for admin review"}


@router.post("/{request_id}/messages/{message_id}/view-once")
async def consume_view_once(request_id: str, message_id: str, current_user: dict = Depends(get_current_user)):
    """Atomically consume access before issuing the short-lived media capability."""
    await _conversation(request_id, current_user)
    capability_id = secrets.token_urlsafe(24)
    capability_hash = hashlib.sha256(capability_id.encode()).hexdigest()
    expires_at = now_utc() + timedelta(seconds=settings.VIEW_ONCE_TOKEN_EXPIRE_SECONDS)
    now = now_utc()
    message = await messages_col.find_one_and_update(
        {
            "_id": oid(message_id), "request_id": request_id, "view_once": True,
            "receiver_id": str(current_user["_id"]), "viewed_at": None,
            "view_once_delivered_at": None,
            "$or": [
                {"view_once_status": "unopened"},
                {"view_once_status": {"$exists": False}},
                {"view_once_status": "reserved", "view_once_capability_expires_at": {"$lte": now}},
            ],
        },
        {"$set": {
            "view_once_status": "reserved", "view_once_capability_hash": capability_hash,
            "view_once_capability_expires_at": expires_at,
        }},
        return_document=ReturnDocument.AFTER,
    )
    if not message:
        existing = await messages_col.find_one({"_id": oid(message_id), "request_id": request_id}, {"viewed_at": 1, "view_once": 1})
        if existing and existing.get("view_once") and existing.get("viewed_at"):
            raise HTTPException(status_code=410, detail={"code": "VIEW_ONCE_ALREADY_OPENED", "message": "This media has already been viewed."})
        if existing and existing.get("view_once"):
            raise HTTPException(status_code=409, detail={"code": "VIEW_ONCE_ALREADY_OPENED", "message": "This media has already been viewed."})
        raise HTTPException(status_code=403, detail="View-once media is unavailable")
    token = jwt.encode({
        "sub": str(current_user["_id"]), "message_id": message_id,
        "cap": capability_id, "type": "view_once", "exp": expires_at,
        "iat": datetime.now(timezone.utc), "iss": settings.JWT_ISSUER,
        "aud": "editzone-view-once",
    }, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return {
        "file_url": f"/api/v1/uploads/view-once/{message_id}?token={token}",
        "viewed_at": None, "expires_at": expires_at, "status": "reserved",
    }
