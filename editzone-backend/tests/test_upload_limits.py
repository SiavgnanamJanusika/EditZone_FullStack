from types import SimpleNamespace

import pytest

from app.core import validators


@pytest.mark.parametrize(
    ("category", "kwargs", "setting"),
    [
        ("image", {}, "MAX_CHAT_IMAGE_MB"),
        ("audio", {"voice": True}, "MAX_VOICE_MESSAGE_MB"),
        ("audio", {}, "MAX_AUDIO_MB"),
        ("document", {}, "MAX_DOCUMENT_MB"),
        ("archive", {}, "MAX_ZIP_MB"),
        ("video", {}, "MAX_VIDEO_MB"),
        ("video", {"view_once": True}, "MAX_VIEW_ONCE_VIDEO_MB"),
    ],
)
def test_category_upload_limits_are_bytes(monkeypatch, category, kwargs, setting):
    configured = SimpleNamespace(
        MAX_PROFILE_IMAGE_MB=5, MAX_CHAT_IMAGE_MB=15, MAX_CHAT_AUDIO_MB=25, MAX_VOICE_MESSAGE_MB=25,
        MAX_AUDIO_MB=50, MAX_DOCUMENT_MB=50, MAX_ZIP_MB=100,
        MAX_VIDEO_MB=250, MAX_VIEW_ONCE_VIDEO_MB=250, MAX_UPLOAD_MB=250,
        MAX_STATUS_IMAGE_MB=10, MAX_STATUS_VIDEO_MB=100,
    )
    monkeypatch.setattr("app.config.settings", configured)
    assert validators.upload_limit_bytes(category, **kwargs) == getattr(configured, setting) * 1024 * 1024


def test_profile_limit_overrides_image_limit(monkeypatch):
    configured = SimpleNamespace(MAX_PROFILE_IMAGE_MB=5, MAX_CHAT_IMAGE_MB=15)
    monkeypatch.setattr("app.config.settings", configured)
    assert validators.upload_limit_bytes("image", purpose="profile_picture") == 5 * 1024 * 1024


@pytest.mark.parametrize(("category", "expected_mb"), [("image", 20), ("audio", 25), ("document", 50), ("archive", 50), ("video", 100)])
def test_chat_attachment_uses_category_limit(monkeypatch, category, expected_mb):
    configured = SimpleNamespace(MAX_CHAT_ATTACHMENT_MB=100, MAX_CHAT_IMAGE_MB=20, MAX_CHAT_AUDIO_MB=25, MAX_CHAT_VIDEO_MB=100, MAX_CHAT_FILE_MB=50, MAX_VOICE_MESSAGE_MB=25)
    monkeypatch.setattr("app.config.settings", configured)
    limit = validators.upload_limit_bytes(category, purpose="chat_attachment", voice=True, view_once=True)
    assert expected_mb * 1024 * 1024 == limit


def test_dangerous_extensions_are_explicitly_blocked():
    assert {".exe", ".msi", ".bat", ".cmd", ".sh", ".php", ".js", ".jar", ".apk", ".dll", ".scr", ".com"} <= validators.DANGEROUS_FILE_EXTENSIONS


def test_status_limits_reuse_central_upload_policy(monkeypatch):
    configured = SimpleNamespace(MAX_STATUS_IMAGE_MB=9, MAX_STATUS_VIDEO_MB=60)
    monkeypatch.setattr("app.config.settings", configured)
    assert validators.upload_limit_bytes("image", purpose="editor_status") == 9 * 1024 * 1024
    assert validators.upload_limit_bytes("video", purpose="editor_status") == 60 * 1024 * 1024


def test_reel_limits_reuse_central_upload_policy(monkeypatch):
    configured = SimpleNamespace(MAX_REEL_IMAGE_MB=15, MAX_REEL_VIDEO_MB=150)
    monkeypatch.setattr("app.config.settings", configured)
    assert validators.upload_limit_bytes("image", purpose="editor_portfolio") == 15 * 1024 * 1024
    assert validators.upload_limit_bytes("video", purpose="editor_portfolio") == 150 * 1024 * 1024
