from pathlib import Path

import pytest

from app.services import chat_rate_limiter


@pytest.mark.asyncio
async def test_development_chat_rate_limit_rejects_burst(monkeypatch):
    chat_rate_limiter._local_counts.clear()
    monkeypatch.setattr(chat_rate_limiter.settings, "ENV", "development")
    monkeypatch.setattr(chat_rate_limiter.settings, "CHAT_RATE_LIMIT_WINDOW_SECONDS", 60)
    assert await chat_rate_limiter.allow_chat_event("message-user", "user-1", 2)
    assert await chat_rate_limiter.allow_chat_event("message-user", "user-1", 2)
    assert not await chat_rate_limiter.allow_chat_event("message-user", "user-1", 2)


def test_view_once_uses_single_redeemable_server_capability():
    root = Path(__file__).parents[1]
    chat = (root / "app/routers/chat_router.py").read_text()
    uploads = (root / "app/routers/upload_router.py").read_text()
    assert '"type": "view_once"' in chat
    assert '"aud": "editzone-view-once"' in chat
    assert '"view_once_status": "reserved"' in chat
    assert '"view_once_status": "unopened"' in (root / "app/sockets/socket_manager.py").read_text()
    assert '"view_once_capability_expires_at": {"$lte": now}' in chat
    assert '"code": "VIEW_ONCE_ALREADY_OPENED"' in chat
    assert '@router.get("/view-once/{message_id}")' in uploads
    assert '"view_once_status": "opened"' in uploads
    assert '"consumed": True' in uploads
    assert '"view_once_delivered_at": None' in uploads
    assert "generate_presigned_url" not in uploads.split('@router.get("/view-once/{message_id}")', 1)[1].split('@router.', 1)[0]
