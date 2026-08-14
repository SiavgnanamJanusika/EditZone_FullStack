import socketio
import logging
from http.cookies import SimpleCookie
from bson import ObjectId
from pymongo.errors import DuplicateKeyError, PyMongoError
from app.config import settings
from app.core.security import decode_token
from app.db.mongodb import db, messages_col, multipart_uploads_col, requests_col, users_col, chat_moderation_logs_col, auth_sessions_col
from app.core.utils import serialize_doc, now_utc
from app.core.accounts import ACTIVE_ACCOUNT_FILTER
from app.core.validators import upload_limit_bytes
from app.services.chat_moderation import BLOCK_MESSAGE, contact_violation, moderation_fingerprint
from app.services.chat_rate_limiter import allow_chat_event
from app.services.web_push import send_chat_push

logger = logging.getLogger(__name__)


def _message_preview(message: dict) -> str:
    """Return a short, safe popup preview without exposing storage metadata."""
    message_type = message.get("message_type") or message.get("file_type") or "text"
    media_previews = {
        "image": "📷 Sent an image", "video": "🎥 Sent a video",
        "audio": "🎤 Sent a voice message", "voice": "🎤 Sent a voice message",
        "document": "📄 Sent a document", "archive": "📎 Sent a file",
        "file": "📎 Sent a file", "system": "💳 Sent payment information",
    }
    if message_type in media_previews:
        return media_previews[message_type]
    text = " ".join(str(message.get("text") or "New message").split())
    return f"{text[:77]}…" if len(text) > 78 else text


async def emit_message_notification(message: dict) -> None:
    """Emit a transient chat notification only to the authenticated receiver room."""
    receiver_id = str(message.get("receiver_id") or "")
    sender_id = str(message.get("sender_id") or "")
    if not receiver_id or receiver_id == sender_id or not ObjectId.is_valid(sender_id):
        return
    sender = await users_col.find_one(
        {"_id": ObjectId(sender_id), **ACTIVE_ACCOUNT_FILTER},
        {"username": 1, "role": 1, "profile_picture": 1},
    )
    if not sender:
        return
    avatar = str(sender.get("profile_picture") or "")
    # Never place signed query strings, storage keys, or arbitrary external
    # locations in a notification payload. Relative protected profile routes
    # are sufficient; the UI falls back to an icon for everything else.
    if not avatar.startswith("/") or "?" in avatar or ".." in avatar:
        avatar = ""
    payload = {
        "id": str(message.get("_id") or message.get("id") or ""),
        "request_id": str(message.get("request_id") or ""),
        "project_id": str(message.get("request_id") or ""),
        "sender_id": sender_id,
        "sender_name": sender.get("username") or "EditZone member",
        "sender_role": sender.get("role") or "user",
        "sender_avatar": avatar,
        "receiver_id": receiver_id,
        "message_type": message.get("message_type") or message.get("file_type") or "text",
        "preview": _message_preview(message),
        "created_at": message.get("created_at").isoformat() if hasattr(message.get("created_at"), "isoformat") else message.get("created_at"),
    }
    await sio.emit("message_notification", payload, room=f"user:{receiver_id}")
    # A connected browser produces the native notification from this Socket.IO
    # event. Web Push is the closed-tab fallback, avoiding double OS alerts.
    receiver_online = any([participant async for participant in sio.manager.get_participants("/", f"user:{receiver_id}")])
    if not receiver_online:
        await send_chat_push(receiver_id, {
            "title": "EditZone",
            "body": f"{payload['sender_name']}: {payload['preview']}",
            "message_id": payload["id"],
            "room_id": payload["request_id"],
            "chat_url": f"/editor/chat/{payload['request_id']}" if payload["sender_role"] == "user" else f"/chat/{payload['request_id']}",
            "tag": f"editzone-chat-{payload['request_id']}",
        })

# Local development runs safely as a single process when Redis is not installed.
# Staging and production always use Redis so rooms/events work across workers.
manager = (
    socketio.AsyncRedisManager(settings.REDIS_URL)
    if settings.ENV.lower() in {"production", "staging"}
    else None
)

# Redis propagates rooms and events between workers/backend instances.
sio = socketio.AsyncServer(
    async_mode="asgi",
    client_manager=manager,
    cors_allowed_origins=list(dict.fromkeys([
        *settings.SOCKET_CORS_ORIGINS,
        settings.FRONTEND_URL.rstrip("/"),
    ])),
    logger=False,
    engineio_logger=False,
)


@sio.event
async def connect(sid, environ, auth):
    """Authenticate from an HttpOnly cookie or a legacy auth payload."""
    remote_ip = environ.get("HTTP_CF_CONNECTING_IP") or environ.get("REMOTE_ADDR") or "unknown"
    if not await allow_chat_event("connect", remote_ip, settings.CHAT_CONNECTION_RATE_LIMIT):
        raise ConnectionRefusedError("Too many connection attempts. Please wait and retry.")
    token = auth.get("token") if isinstance(auth, dict) else None
    if not token:
        cookies = SimpleCookie()
        cookies.load(environ.get("HTTP_COOKIE", ""))
        token_cookie = cookies.get("ez_access_token")
        token = token_cookie.value if token_cookie else None
    if not token:
        if settings.ENV.lower() == "development":
            logger.warning("Socket connection rejected sid=%s reason=missing_token origin=%s", sid, environ.get("HTTP_ORIGIN"))
        raise ConnectionRefusedError("Authentication token required")
    try:
        payload = decode_token(token)
    except Exception as exc:
        if settings.ENV.lower() == "development":
            logger.warning("Socket connection rejected sid=%s reason=invalid_or_expired_token type=%s", sid, type(exc).__name__)
        raise ConnectionRefusedError("Invalid or expired token")

    user_id = payload.get("sub")
    if payload.get("type") not in {"access", "socket"} or not user_id or not ObjectId.is_valid(user_id):
        if settings.ENV.lower() == "development":
            logger.warning("Socket connection rejected sid=%s reason=invalid_payload", sid)
        raise ConnectionRefusedError("Invalid token payload")

    session_id = payload.get("sid")
    if not session_id or not await auth_sessions_col.find_one({"session_id": session_id, "user_id": ObjectId(user_id), "revoked_at": None}, {"_id": 1}):
        if settings.ENV.lower() == "development":
            logger.warning("Socket connection rejected sid=%s reason=revoked_session user=%s", sid, user_id[-6:])
        raise ConnectionRefusedError("Session was revoked or expired")

    user = await users_col.find_one({"_id": ObjectId(user_id), **ACTIVE_ACCOUNT_FILTER})
    if not user or user.get("is_banned") or user.get("is_deleted"):
        if settings.ENV.lower() == "development":
            logger.warning("Socket connection rejected sid=%s reason=account_unavailable user=%s", sid, user_id[-6:])
        raise ConnectionRefusedError("Account is unavailable")

    # Join a personal room for direct notifications
    await sio.save_session(sid, {"user_id": user_id})
    await sio.enter_room(sid, f"user:{user_id}")
    return True


@sio.event
async def disconnect(sid):
    try:
        session = await sio.get_session(sid)
    except KeyError:
        return
    user_id = session.get("user_id")
    last_seen = now_utc()
    if user_id and ObjectId.is_valid(user_id):
        await users_col.update_one({"_id": ObjectId(user_id)}, {"$set": {"last_seen_at": last_seen}})
    for request_id in session.get("joined_requests", []):
        await sio.emit("presence_status", {"request_id": request_id, "user_id": user_id, "online": False, "last_seen_at": last_seen.isoformat()}, room=f"chat_{request_id}")


async def disconnect_user(user_id: str) -> None:
    participants = [participant async for participant in sio.manager.get_participants("/", f"user:{user_id}")]
    for participant in participants:
        sid = participant[0] if isinstance(participant, tuple) else participant
        await sio.disconnect(sid, namespace="/")


async def _can_access_request(sid, request_id):
    if not request_id or not ObjectId.is_valid(request_id):
        return False
    try:
        session = await sio.get_session(sid)
    except KeyError:
        return False
    if not session or not ObjectId.is_valid(session.get("user_id")):
        return False
    user_id = ObjectId(session["user_id"])
    request = await requests_col.find_one({"_id": ObjectId(request_id)})
    if not request:
        return False
    if user_id not in (request.get("user_id"), request.get("editor_user_id")):
        return False
    return bool(await users_col.find_one({"_id": user_id, **ACTIVE_ACCOUNT_FILTER}, {"_id": 1}))


async def _chat_is_open(request_id):
    if not request_id or not ObjectId.is_valid(request_id):
        return False
    request = await requests_col.find_one(
        {"_id": ObjectId(request_id)},
        {"status": 1},
    )
    return bool(request and request.get("status") in ("accepted", "in_progress", "overdue", "admin_review", "revision_requested", "delivered", "cancel_requested", "disputed", "refund_pending"))


@sio.event
async def join_chat(sid, data):
    """Join a specific project's chat room: data = { request_id }"""
    if not isinstance(data, dict):
        return {"success": False, "message": "Invalid chat data"}
    request_id = data.get("request_id")
    if await _can_access_request(sid, request_id):
        request = await requests_col.find_one(
            {"_id": ObjectId(request_id)},
            {"status": 1},
        )
        if request.get("status") not in ("accepted", "in_progress", "overdue", "admin_review", "revision_requested", "delivered", "cancel_requested", "disputed", "refund_pending", "completed"):
            return {"success": False, "message": "Chat is available after the request is accepted"}
        await sio.enter_room(sid, f"chat_{request_id}")
        session = await sio.get_session(sid)
        joined = list(dict.fromkeys([*session.get("joined_requests", []), request_id]))
        session["joined_requests"] = joined
        await sio.save_session(sid, session)
        await sio.emit("presence_status", {"request_id": request_id, "user_id": session["user_id"], "online": True, "last_seen_at": None}, room=f"chat_{request_id}", skip_sid=sid)
        return {"success": True, "chat_closed": not await _chat_is_open(request_id)}
    return {"success": False, "message": "Not authorized to join this chat"}


@sio.event
async def join_conversation(sid, data):
    return await join_chat(sid, data)


@sio.event
async def leave_conversation(sid, data):
    request_id = data.get("request_id") if isinstance(data, dict) else None
    if request_id and ObjectId.is_valid(request_id):
        await sio.leave_room(sid, f"chat_{request_id}")
        try:
            session = await sio.get_session(sid)
            session["joined_requests"] = [value for value in session.get("joined_requests", []) if value != request_id]
            await sio.save_session(sid, session)
        except KeyError:
            pass
    return {"success": True}


@sio.event
async def leave_chat(sid, data):
    return await leave_conversation(sid, data)


async def _reject_contact_sharing(sid, request_id: str, user_id: str, value: str, reason: str) -> dict:
    code = "CONTACT_LINK_NOT_ALLOWED" if reason == "contact_link" else "PHONE_NUMBER_NOT_ALLOWED"
    payload = {"success": False, "code": code, "message": BLOCK_MESSAGE}
    try:
        await chat_moderation_logs_col.insert_one({
            "request_id": request_id,
            "user_id": ObjectId(user_id),
            "reason": reason,
            "fingerprint": moderation_fingerprint(value),
            "created_at": now_utc(),
        })
    except PyMongoError:
        # Moderation logging is best-effort; the rejected content must never be
        # persisted or broadcast merely because the audit database is degraded.
        logger.exception("Unable to persist chat moderation event request=%s reason=%s", request_id, reason)
    try:
        await sio.emit("chat_error", {"code": code, "message": BLOCK_MESSAGE}, to=sid)
    except Exception:
        logger.warning("Unable to emit chat moderation error request=%s", request_id)
    return payload


@sio.event
async def send_message(sid, data):
    """
    data = { request_id, text?, file_url?, file_type? }
    Persists message and broadcasts it to everyone in the chat room.
    """
    if not isinstance(data, dict):
        return {"success": False, "message": "Invalid message data"}
    try:
        session = await sio.get_session(sid)
    except KeyError:
        return {"success": False, "message": "Chat session expired. Please reconnect."}
    user_id = session["user_id"]
    request_id = data.get("request_id")
    if not await _can_access_request(sid, request_id):
        return {"success": False, "message": "Not authorized to send to this chat"}
    if not await _chat_is_open(request_id):
        return {"success": False, "message": "This project is completed and the chat is closed"}
    if not await allow_chat_event("message-user", user_id, settings.CHAT_MESSAGE_RATE_LIMIT):
        return {"success": False, "code": "RATE_LIMITED", "message": "You are sending messages too quickly. Please wait."}
    if not await allow_chat_event("message-room", request_id, settings.CHAT_ROOM_MESSAGE_RATE_LIMIT):
        return {"success": False, "code": "RATE_LIMITED", "message": "This conversation is temporarily busy. Please retry shortly."}
    project = await requests_col.find_one({"_id": ObjectId(request_id)}, {"user_id": 1, "editor_user_id": 1})

    text = (data.get("text") or "").strip()
    file_url = data.get("file_url")
    upload_id = data.get("upload_id")
    # Text-only messages have no upload branch, but document construction uses
    # this mapping to derive the canonical message type.
    metadata = {}
    attachment_ids = data.get("attachment_ids")
    if attachment_ids is not None:
        if not isinstance(attachment_ids, list) or len(attachment_ids) > settings.MAX_FILES_PER_MESSAGE:
            return {"success": False, "message": f"A message can contain at most {settings.MAX_FILES_PER_MESSAGE} files"}
        # The current wire format sends one metadata record per persisted message.
        # Reject binary/unknown arrays rather than allowing them into Socket.IO.
        if any(not isinstance(value, str) for value in attachment_ids):
            return {"success": False, "message": "Attachment IDs must be strings"}
    raw_client_message_id = data.get("client_message_id")
    if raw_client_message_id is not None and not isinstance(raw_client_message_id, str):
        return {"success": False, "message": "Client message ID must be a string"}
    client_message_id = (raw_client_message_id or "").strip()
    file_type = data.get("file_type")
    timecode_seconds = data.get("timecode_seconds")
    duration_seconds = data.get("duration_seconds")
    view_once = data.get("view_once") is True
    if not text and not upload_id:
        return {"success": False, "message": "Message cannot be empty"}
    if len(text) > settings.MAX_TEXT_MESSAGE_LENGTH:
        return {"success": False, "message": f"Message must be {settings.MAX_TEXT_MESSAGE_LENGTH} characters or fewer"}
    if text:
        logger.info(
            "CHAT_TEXT_VALIDATION user_id=%s room_id=%s message_type=%s",
            user_id, request_id, "caption" if upload_id else "text",
        )
    violation = contact_violation(text)
    if violation:
        return await _reject_contact_sharing(sid, request_id, user_id, text, violation)
    if timecode_seconds is not None and (not isinstance(timecode_seconds, int) or timecode_seconds < 0 or timecode_seconds > 24 * 60 * 60):
        return {"success": False, "message": "Invalid video timestamp"}
    if duration_seconds is not None and (not isinstance(duration_seconds, int) or duration_seconds < 1 or duration_seconds > settings.VOICE_MESSAGE_MAX_SECONDS):
        return {"success": False, "message": "Invalid voice message duration"}
    if client_message_id and (len(client_message_id) > 64 or not all(char.isalnum() or char in "-_" for char in client_message_id)):
        return {"success": False, "message": "Invalid client message ID"}
    if client_message_id:
        existing = await messages_col.find_one({
            "request_id": request_id, "sender_id": user_id,
            "client_message_id": client_message_id,
        })
        if existing:
            return {"success": True, "duplicate": True, "message": serialize_doc(existing)}
    if file_url and not upload_id:
        return {"success": False, "message": "Attachments must reference an EditZone upload; use the external_link message type for links"}
    if upload_id:
        upload = await db["uploads.files"].find_one({"_id": ObjectId(upload_id)}) if ObjectId.is_valid(upload_id) else None
        multipart = None if upload else await multipart_uploads_col.find_one({"upload_id": upload_id})
        metadata = (upload or {}).get("metadata", {}) if upload else (multipart or {})
        if not (upload or multipart) or metadata.get("owner_id") != ObjectId(user_id) or metadata.get("request_id") != request_id:
            return {"success": False, "message": "Attachment does not belong to this sender and project"}
        if metadata.get("purpose") != "chat_attachment" or metadata.get("scan_status") != "safe":
            return {"success": False, "message": "Attachment is not approved for chat"}
        file_url = f"/api/v1/uploads/file/{upload['filename']}" if upload else f"/api/v1/uploads/s3/file/{upload_id}"
        # Stored, server-validated metadata is authoritative. Never let the
        # socket payload relabel an uploaded image as another media category.
        file_type = metadata.get("category")
        if file_type not in ("image", "video", "document", "archive", "audio"):
            return {"success": False, "message": "Invalid attachment type"}
        if file_type == "video":
            allowed_mimes = {"video/mp4", "video/webm", "video/quicktime"}
            if metadata.get("content_type", "").lower() not in allowed_mimes:
                return {"success": False, "message": "Chat videos must be MP4, WebM, or MOV"}
        size = metadata.get("size") or metadata.get("length") or (upload or {}).get("length", 0)
        limit = upload_limit_bytes(file_type, purpose="chat_attachment", voice=bool(metadata.get("voice")), view_once=view_once)
        if size and size > limit:
            label = "Voice message" if metadata.get("voice") else "Chat image" if file_type == "image" else "Attachment"
            return {"success": False, "code": "UPLOAD_TOO_LARGE", "message": f"{label} exceeds the {limit // 1048576} MB limit."}
    doc = {
        "request_id": request_id,
        "sender_id": user_id,
        "receiver_id": str(project["editor_user_id"] if str(project.get("user_id")) == user_id else project["user_id"]),
        "text": text or None,
        "file_url": file_url,
        "upload_id": upload_id,
        "file_type": file_type,
        "message_type": "audio" if metadata.get("voice") else (file_type or "text"),
        "attachment_id": upload_id,
        "attachment": ({
            "upload_id": upload_id, "bucket": metadata.get("bucket"), "object_key": metadata.get("key"),
            "original_filename": metadata.get("original_name"), "safe_filename": metadata.get("safe_filename"),
            "mime_type": metadata.get("content_type"), "category": "voice" if metadata.get("voice") else file_type,
            "size": metadata.get("size") or (upload or {}).get("length"), "uploader_id": user_id,
            "conversation_id": request_id, "checksum": metadata.get("checksum"), "upload_status": metadata.get("scan_status"),
            "created_at": metadata.get("created_at"),
        } if upload_id else None),
        "original_name": metadata.get("original_name") if upload_id else None,
        "file_size": (metadata.get("size") or (upload or {}).get("length")) if upload_id else None,
        "mime_type": metadata.get("content_type") if upload_id else None,
        "delivery_status": "sent",
        "delivered_at": None,
        "read_at": None,
        "view_once": view_once,
        "view_once_status": "unopened" if view_once else None,
        "consumed": False if view_once else None,
        "consumed_at": None,
        "duration_seconds": duration_seconds if metadata.get("voice") else None,
        "viewed_at": None,
        "viewed_by": None,
        "timecode_seconds": timecode_seconds,
        "created_at": now_utc(),
    }
    if client_message_id:
        doc["client_message_id"] = client_message_id
    try:
        result = await messages_col.insert_one(doc)
    except DuplicateKeyError:
        existing = await messages_col.find_one({"request_id": request_id, "sender_id": user_id, "client_message_id": client_message_id})
        return {"success": True, "duplicate": True, "message": serialize_doc(existing)}
    except PyMongoError:
        logger.exception("Unable to persist chat message for request %s", request_id)
        return {"success": False, "code": "TEMPORARY_UNAVAILABLE", "message": "Message could not be saved. It will be safe to retry."}
    doc["_id"] = result.inserted_id

    if upload_id:
        logger.info(
            "CHAT_MEDIA_UPLOAD_SUCCESS message_id=%s upload_id=%s user_id=%s room_id=%s type=%s mime=%s size=%s",
            doc["_id"], upload_id, user_id, request_id, doc["message_type"],
            doc.get("mime_type"), doc.get("file_size"),
        )

    try:
        await emit_message_notification(doc)
    except Exception:
        # A popup is supplementary; a notification failure must never fail a
        # message that has already been durably saved.
        logger.exception("Unable to emit message notification for request %s", request_id)

    try:
        await sio.emit("new_message", serialize_doc(doc), room=f"chat_{request_id}")
    except Exception:
        logger.exception("Unable to broadcast chat message for request %s", request_id)
        return {"success": True, "warning": "Message was saved; real-time delivery will resume automatically.", "message": serialize_doc(doc)}
    delivered_at = now_utc()
    await messages_col.update_one({"_id": doc["_id"]}, {"$set": {"delivery_status": "delivered", "delivered_at": delivered_at}})
    doc["delivery_status"] = "delivered"
    doc["delivered_at"] = delivered_at
    await sio.emit("message_delivered", {"id": str(doc["_id"]), "request_id": request_id}, room=f"chat_{request_id}")
    return {"success": True, "message": serialize_doc(doc)}


@sio.event
async def typing(sid, data):
    if not isinstance(data, dict):
        return {"success": False, "message": "Invalid typing data"}
    try:
        session = await sio.get_session(sid)
    except KeyError:
        return {"success": False, "message": "Chat session expired. Please reconnect."}
    if not session:
        return {"success": False, "message": "Chat session expired. Please reconnect."}
    request_id = data.get("request_id")
    if await _can_access_request(sid, request_id) and await _chat_is_open(request_id):
        if not await allow_chat_event("typing-user", session["user_id"], settings.CHAT_TYPING_RATE_LIMIT):
            return {"success": False, "code": "RATE_LIMITED", "message": "Typing updates are temporarily limited"}
        if not await allow_chat_event("typing-room", request_id, settings.CHAT_ROOM_TYPING_RATE_LIMIT):
            return {"success": False, "code": "RATE_LIMITED", "message": "Typing updates are temporarily limited"}
        await sio.emit(
            "user_typing",
            {"user_id": session["user_id"], "request_id": request_id},
            room=f"chat_{request_id}",
            skip_sid=sid,
        )
    return {"success": True}


@sio.event
async def typing_start(sid, data):
    return await typing(sid, data)


@sio.event
async def typing_stop(sid, data):
    if not isinstance(data, dict):
        return {"success": False}
    try:
        session = await sio.get_session(sid)
    except KeyError:
        return {"success": False}
    request_id = data.get("request_id")
    if await _can_access_request(sid, request_id):
        await sio.emit("user_stopped_typing", {"user_id": session["user_id"], "request_id": request_id}, room=f"chat_{request_id}", skip_sid=sid)
    return {"success": True}


@sio.event
async def mark_read(sid, data):
    if not isinstance(data, dict):
        return {"success": False, "message": "Invalid receipt data"}
    try:
        session = await sio.get_session(sid)
    except KeyError:
        return {"success": False, "message": "Chat session expired"}
    request_id = data.get("request_id")
    if not await _can_access_request(sid, request_id):
        return {"success": False, "message": "Not authorized"}
    read_at = now_utc()
    result = await messages_col.update_many(
        {"request_id": request_id, "receiver_id": session["user_id"], "read_at": None},
        {"$set": {"read_at": read_at, "delivery_status": "seen"}},
    )
    await sio.emit("messages_read", {"request_id": request_id, "reader_id": session["user_id"], "read_at": read_at.isoformat()}, room=f"chat_{request_id}")
    return {"success": True, "updated": result.modified_count}
