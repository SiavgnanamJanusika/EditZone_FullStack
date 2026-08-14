"""Server-side chat contact moderation with conservative false-positive controls."""
import hashlib
import re
import unicodedata

from app.config import settings

BLOCK_MESSAGE = "Phone numbers cannot be shared in chat. Please communicate through EditZone for your security."

_CONTACT_LINK = re.compile(
    r"(?ix)(?:"
    r"\btel\s*:\s*\+?(?:[0-9][\s().\-]*){7,15}"
    r"|(?:https?://)?wa\.me/\+?(?:[0-9][\s().\-]*){7,15}"
    r"|(?:https?://)?api\.whatsapp\.com/send\?[^\s]*\bphone=\+?(?:[0-9][\s().\-]*){7,15}"
    r")"
)
_PHONE_CONTEXT = re.compile(r"(?i)\b(?:call|phone|mobile|whatsapp|contact|ring|text me|number|නම්බර්|දුරකථන)\b")
_CANDIDATE = re.compile(r"(?<![\w])(?:\+?[0-9][\s().\-–—_/\\]*){7,15}(?![\w])")
_SRI_LANKA = re.compile(r"^(?:0094|94|0)7[0-9]{8}$")


def _normalize_unicode_digits(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    output = []
    for char in normalized:
        try:
            output.append(str(unicodedata.digit(char)) if unicodedata.category(char) == "Nd" else char)
        except (TypeError, ValueError):
            output.append(char)
    return "".join(output)


def contact_violation(text: str) -> str | None:
    """Return a reason without returning or logging the sensitive value."""
    value = _normalize_unicode_digits(str(text or "")).lower().strip()
    if not value:
        return None
    if _CONTACT_LINK.search(value):
        return "contact_link"
    for match in _CANDIDATE.finditer(value):
        raw = match.group(0)
        digits = re.sub(r"[^0-9]", "", raw)
        # Common project numbers (years, dimensions, prices, order IDs) stay valid.
        if _SRI_LANKA.fullmatch(digits):
            return "sri_lankan_phone"
        if raw.lstrip().startswith("+") and 8 <= len(digits) <= 15:
            return "international_phone"
        if digits.startswith("00") and 10 <= len(digits) <= 15:
            return "international_phone"
        if _PHONE_CONTEXT.search(value) and 7 <= len(digits) <= 15:
            return "contextual_phone"
        # Heavy separators are a common evasion pattern; require phone-like length.
        separators = len(re.findall(r"[\s().\-–—_/\\]", raw))
        if 9 <= len(digits) <= 15 and separators >= 3:
            return "obfuscated_phone"
        if 10 <= len(digits) <= 15 and separators == 0:
            return "likely_phone"
    return None


def moderation_fingerprint(text: str) -> str:
    normalized = re.sub(r"\s+", "", (text or "").lower())
    return hashlib.sha256(f"{settings.JWT_SECRET_KEY}:{normalized}".encode()).hexdigest()
