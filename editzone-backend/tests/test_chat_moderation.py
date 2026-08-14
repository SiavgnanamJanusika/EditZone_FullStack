import pytest
from unittest.mock import AsyncMock

from bson import ObjectId
from fastapi import HTTPException

from app.services.chat_moderation import contact_violation
from app.core.security import create_socket_token, decode_token
from app.routers.chat_router import RestChatMessageBody, create_rest_chat_message
from app.sockets.socket_manager import send_message


@pytest.mark.parametrize("value", [
    "call me 077 123 4567",
    "WhatsApp +94 (77) 123-4567",
    "tel:+94771234567",
    "https://wa.me/94771234567",
    "https://api.whatsapp.com/send?phone=94771234567",
    "0094771234567",
    "(077) 123 4567",
    "٠٧٧١٢٣٤٥٦٧",
    "0 7 7 1 2 3 4 5 6 7",
])
def test_phone_sharing_bypasses_are_blocked(value):
    assert contact_violation(value)


@pytest.mark.parametrize("value", [
    "Budget is LKR 12500",
    "Export at 1920 x 1080 and 60 fps",
    "Delivery date is 2026-08-15",
    "Order EZ-20260807-12345",
    "The source video is 3600 seconds",
    "Rs 5000",
    "1080p",
    "Project 1234",
    "Delivery in 7 days",
    "Version 2.0",
    "https://wa.me/design-team",
])
def test_project_numbers_are_not_blocked(value):
    assert contact_violation(value) is None


def test_socket_token_is_short_lived_and_socket_scoped():
    payload = decode_token(create_socket_token({"sub": "507f1f77bcf86cd799439011", "sid": "session-1"}))
    assert payload["type"] == "socket"
    assert payload["sid"] == "session-1"
    assert payload["exp"] - payload["iat"] == 120


@pytest.mark.asyncio
async def test_rest_phone_rejection_never_saves_or_broadcasts(monkeypatch):
    user_id, editor_id, request_oid = ObjectId(), ObjectId(), ObjectId()
    project = {"_id": request_oid, "user_id": user_id, "editor_user_id": editor_id, "status": "in_progress"}
    monkeypatch.setattr("app.routers.chat_router._conversation", AsyncMock(return_value=project))
    insert = AsyncMock()
    broadcast = AsyncMock()
    monkeypatch.setattr("app.routers.chat_router.messages_col.insert_one", insert)
    monkeypatch.setattr("app.routers.chat_router.chat_moderation_logs_col.insert_one", AsyncMock())
    monkeypatch.setattr("app.routers.chat_router.sio.emit", broadcast)
    with pytest.raises(HTTPException) as raised:
        await create_rest_chat_message(
            RestChatMessageBody(request_id=str(request_oid), text="0771234567"),
            {"_id": user_id, "role": "user"},
        )
    assert raised.value.status_code == 422
    assert raised.value.detail["code"] == "PHONE_NUMBER_NOT_ALLOWED"
    insert.assert_not_awaited()
    broadcast.assert_not_awaited()


@pytest.mark.asyncio
async def test_socket_phone_rejection_emits_chat_error_without_saving(monkeypatch):
    user_id, request_id = ObjectId(), str(ObjectId())
    monkeypatch.setattr("app.sockets.socket_manager.sio.get_session", AsyncMock(return_value={"user_id": str(user_id)}))
    monkeypatch.setattr("app.sockets.socket_manager._can_access_request", AsyncMock(return_value=True))
    monkeypatch.setattr("app.sockets.socket_manager._chat_is_open", AsyncMock(return_value=True))
    monkeypatch.setattr("app.sockets.socket_manager.allow_chat_event", AsyncMock(return_value=True))
    monkeypatch.setattr("app.sockets.socket_manager.requests_col.find_one", AsyncMock(return_value={"user_id": user_id, "editor_user_id": ObjectId()}))
    insert = AsyncMock()
    emit = AsyncMock()
    monkeypatch.setattr("app.sockets.socket_manager.messages_col.insert_one", insert)
    monkeypatch.setattr("app.sockets.socket_manager.chat_moderation_logs_col.insert_one", AsyncMock())
    monkeypatch.setattr("app.sockets.socket_manager.sio.emit", emit)
    response = await send_message("socket-1", {"request_id": request_id, "text": "+94 77 123 4567"})
    assert response["code"] == "PHONE_NUMBER_NOT_ALLOWED"
    insert.assert_not_awaited()
    emit.assert_awaited_once_with(
        "chat_error",
        {"code": "PHONE_NUMBER_NOT_ALLOWED", "message": response["message"]},
        to="socket-1",
    )
