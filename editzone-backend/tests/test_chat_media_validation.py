from app.config import settings
from app.routers.upload_router import _matches_magic
from pathlib import Path


def test_chat_media_limits_match_public_contract():
    assert settings.MAX_CHAT_ATTACHMENT_MB == 1000
    assert settings.MAX_VIDEO_MB == 1000


def test_image_signatures_reject_fake_webp_and_accept_supported_headers():
    assert _matches_magic("image", b"\xff\xd8\xff" + b"x" * 20)
    assert _matches_magic("image", b"\x89PNG\r\n\x1a\n" + b"x" * 20)
    assert _matches_magic("image", b"RIFF\x00\x00\x00\x00WEBP" + b"x" * 20)
    assert not _matches_magic("image", b"RIFF\x00\x00\x00\x00WAVE" + b"x" * 20)


def test_browser_recorded_audio_signatures_are_supported():
    assert _matches_magic("audio", b"\x1aE\xdf\xa3" + b"x" * 20)  # WebM/Opus
    assert _matches_magic("audio", b"OggS" + b"x" * 20)  # Ogg/Opus
    assert _matches_magic("audio", b"\x00\x00\x00\x18ftypM4A " + b"x" * 20)  # Safari MP4


def test_uploads_trigger_immediate_quarantine_scan_without_bypassing_worker():
    source = (Path(__file__).parents[1] / "app/routers/upload_router.py").read_text()
    assert "background_tasks.add_task(_scan_gridfs_background" in source
    assert "background_tasks.add_task(scan_pending_s3_uploads, 1)" in source
    assert "except ScannerUnavailable" in source
