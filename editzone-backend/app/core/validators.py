import re
from urllib.parse import urlparse

NIC_OLD_RE = re.compile(r"^[0-9]{9}[vVxX]$")
NIC_NEW_RE = re.compile(r"^[0-9]{12}$")
PHONE_RE = re.compile(r"^(?:\+94|0)[0-9]{9}$")
EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def is_valid_nic(nic: str) -> bool:
    if not nic:
        return False
    nic = nic.strip()
    return bool(NIC_OLD_RE.match(nic) or NIC_NEW_RE.match(nic))


def is_valid_phone(phone: str) -> bool:
    if not phone:
        return False
    return bool(PHONE_RE.match(phone.strip()))


def is_valid_email(email: str) -> bool:
    if not email:
        return False
    return bool(EMAIL_RE.match(email.strip()))


ALLOWED_FILE_EXTENSIONS = {
    "image": {".jpg", ".jpeg", ".png", ".gif", ".webp"},
    "video": {".mp4", ".mov", ".avi", ".mkv", ".webm"},
    "document": {".pdf", ".doc", ".docx", ".txt"},
    "archive": {".zip", ".rar", ".7z"},
    "audio": {".mp3", ".wav", ".m4a", ".ogg", ".weba"},
}

DANGEROUS_FILE_EXTENSIONS = {
    ".exe", ".msi", ".bat", ".cmd", ".sh", ".php", ".js", ".jar",
    ".apk", ".dll", ".scr", ".com",
}


def upload_limit_bytes(category: str, *, purpose: str = "", voice: bool = False, view_once: bool = False) -> int:
    """Return the single backend source of truth for upload byte limits."""
    from app.config import settings

    if purpose == "profile_picture":
        mb = settings.MAX_PROFILE_IMAGE_MB
    elif purpose == "editor_status" and category == "image":
        mb = settings.MAX_STATUS_IMAGE_MB
    elif purpose == "editor_status" and category == "video":
        mb = settings.MAX_STATUS_VIDEO_MB
    elif purpose == "editor_portfolio" and category == "image":
        mb = settings.MAX_REEL_IMAGE_MB
    elif purpose == "editor_portfolio" and category == "video":
        mb = settings.MAX_REEL_VIDEO_MB
    elif purpose == "chat_attachment" and category == "image":
        mb = settings.MAX_CHAT_IMAGE_MB
    elif purpose == "chat_attachment" and category == "audio":
        mb = getattr(settings, "MAX_CHAT_AUDIO_MB", settings.MAX_VOICE_MESSAGE_MB)
    elif purpose == "chat_attachment" and category == "video":
        mb = settings.MAX_CHAT_VIDEO_MB
    elif purpose == "chat_attachment":
        mb = settings.MAX_CHAT_FILE_MB
    elif purpose == "final_delivery":
        return settings.FINAL_DELIVERY_MAX_BYTES
    elif purpose in {"project_source_file", "project_reference_file", "dispute_evidence"}:
        mb = settings.MAX_PROJECT_MEDIA_MB
    elif category == "image":
        mb = settings.MAX_CHAT_IMAGE_MB
    elif category == "audio" and voice:
        mb = settings.MAX_VOICE_MESSAGE_MB
    elif category == "audio":
        mb = settings.MAX_AUDIO_MB
    elif category == "document":
        mb = settings.MAX_DOCUMENT_MB
    elif category == "archive":
        mb = settings.MAX_ZIP_MB
    elif category == "video" and view_once:
        mb = settings.MAX_VIEW_ONCE_VIDEO_MB
    elif category == "video":
        mb = settings.MAX_VIDEO_MB
    else:
        mb = settings.MAX_UPLOAD_MB
    return mb * 1024 * 1024


def get_file_category(filename: str) -> str | None:
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    for category, exts in ALLOWED_FILE_EXTENSIONS.items():
        if ext in exts:
            return category
    return None


def is_valid_upload_url(value: str) -> bool:
    if not isinstance(value, str) or not value or len(value) > 2048:
        return False
    if value.startswith(("/api/v1/uploads/file/", "/api/v1/uploads/s3/")):
        return bool(get_file_category(value))
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc) and bool(get_file_category(parsed.path))
