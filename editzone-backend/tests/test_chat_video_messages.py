import pytest
from bson import ObjectId

from app.sockets import socket_manager


class Result:
    def __init__(self, inserted_id=None):
        self.inserted_id = inserted_id or ObjectId()


class Collection:
    def __init__(self, documents=None):
        self.documents = list(documents or [])

    async def find_one(self, query, projection=None):
        for document in self.documents:
            if all(
                document.get(key) != value["$ne"] if isinstance(value, dict) and "$ne" in value
                else document.get(key) == value
                for key, value in query.items()
            ):
                return document
        return None

    async def insert_one(self, document):
        document["_id"] = ObjectId()
        self.documents.append(document.copy())
        return Result(document["_id"])

    async def update_one(self, query, update):
        return Result()


@pytest.mark.asyncio
async def test_s3_video_message_is_saved_and_acknowledged(monkeypatch):
    user_id, other_id, request_oid = ObjectId(), ObjectId(), ObjectId()
    request_id = str(request_oid)
    requests = Collection([{"_id": request_oid, "user_id": user_id, "editor_user_id": other_id, "status": "in_progress"}])
    messages = Collection()
    multipart = Collection([{
        "upload_id": "aws-multipart-id", "owner_id": user_id, "request_id": request_id,
        "purpose": "chat_attachment", "scan_status": "safe", "category": "video",
        "content_type": "video/mp4", "size": 1024, "original_name": "review.mp4",
    }])
    monkeypatch.setattr(socket_manager, "requests_col", requests)
    monkeypatch.setattr(socket_manager, "messages_col", messages)
    monkeypatch.setattr(socket_manager, "multipart_uploads_col", multipart)
    monkeypatch.setattr(socket_manager, "users_col", Collection([{"_id": user_id, "role": "user"}]))
    monkeypatch.setattr(socket_manager.sio, "get_session", lambda sid: _async({"user_id": str(user_id)}))
    monkeypatch.setattr(socket_manager.sio, "emit", lambda *args, **kwargs: _async(None))

    result = await socket_manager.send_message("sid", {
        "request_id": request_id, "upload_id": "aws-multipart-id", "file_type": "video",
        "client_message_id": "client-1",
    })

    assert result["success"] is True
    assert result["message"]["file_url"].endswith("/s3/file/aws-multipart-id")
    assert result["message"]["original_name"] == "review.mp4"


@pytest.mark.asyncio
async def test_client_message_id_makes_retry_idempotent(monkeypatch):
    user_id, other_id, request_oid, message_oid = ObjectId(), ObjectId(), ObjectId(), ObjectId()
    request_id = str(request_oid)
    existing = {"_id": message_oid, "request_id": request_id, "sender_id": str(user_id), "client_message_id": "same-id", "text": "hello"}
    monkeypatch.setattr(socket_manager, "requests_col", Collection([{"_id": request_oid, "user_id": user_id, "editor_user_id": other_id, "status": "in_progress"}]))
    monkeypatch.setattr(socket_manager, "messages_col", Collection([existing]))
    monkeypatch.setattr(socket_manager, "users_col", Collection([{"_id": user_id, "role": "user"}]))
    monkeypatch.setattr(socket_manager.sio, "get_session", lambda sid: _async({"user_id": str(user_id)}))

    result = await socket_manager.send_message("sid", {"request_id": request_id, "text": "hello", "client_message_id": "same-id"})

    assert result["success"] is True
    assert result["duplicate"] is True
    assert result["message"]["id"] == str(message_oid)


@pytest.mark.asyncio
async def test_text_message_does_not_require_attachment_metadata(monkeypatch):
    user_id, other_id, request_oid = ObjectId(), ObjectId(), ObjectId()
    request_id = str(request_oid)
    messages = Collection()
    monkeypatch.setattr(socket_manager, "requests_col", Collection([{"_id": request_oid, "user_id": user_id, "editor_user_id": other_id, "status": "in_progress"}]))
    monkeypatch.setattr(socket_manager, "messages_col", messages)
    monkeypatch.setattr(socket_manager, "users_col", Collection([{"_id": user_id, "role": "user"}]))
    monkeypatch.setattr(socket_manager.sio, "get_session", lambda sid: _async({"user_id": str(user_id)}))
    monkeypatch.setattr(socket_manager.sio, "emit", lambda *args, **kwargs: _async(None))

    result = await socket_manager.send_message("sid", {
        "request_id": request_id, "text": "hello", "client_message_id": "text-1",
    })

    assert result["success"] is True
    assert result["message"]["message_type"] == "text"
    assert result["message"]["attachment"] is None


@pytest.mark.asyncio
async def test_invalid_voice_duration_is_rejected_before_persistence(monkeypatch):
    user_id, other_id, request_oid = ObjectId(), ObjectId(), ObjectId()
    request_id = str(request_oid)
    messages = Collection()
    monkeypatch.setattr(socket_manager, "requests_col", Collection([{"_id": request_oid, "user_id": user_id, "editor_user_id": other_id, "status": "in_progress"}]))
    monkeypatch.setattr(socket_manager, "messages_col", messages)
    monkeypatch.setattr(socket_manager, "users_col", Collection([{"_id": user_id, "role": "user"}]))
    monkeypatch.setattr(socket_manager.sio, "get_session", lambda sid: _async({"user_id": str(user_id)}))

    result = await socket_manager.send_message("sid", {
        "request_id": request_id, "text": "voice metadata", "duration_seconds": 999,
        "client_message_id": "voice-invalid",
    })

    assert result["success"] is False
    assert "duration" in result["message"]
    assert messages.documents == []


async def _async(value):
    return value
