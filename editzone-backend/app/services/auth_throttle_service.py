import hashlib
from datetime import timedelta

import httpx
from fastapi import HTTPException
from pymongo import ReturnDocument

from app.config import settings
from app.core.utils import now_utc
from app.db.mongodb import auth_rate_limits_col


def _hashed_key(kind: str, value: str) -> str:
    return f"{kind}:{hashlib.sha256(value.strip().lower().encode()).hexdigest()}"


def throttle_keys(email: str, ip: str) -> tuple[str, str]:
    """Return privacy-preserving account and network limiter keys."""
    return _hashed_key("email", email), _hashed_key("ip", ip)


async def get_scope_counts(email: str, ip: str, action: str, minutes: int) -> dict[str, int]:
    now = now_utc()
    window_start = now.replace(second=0, microsecond=0)
    window_start -= timedelta(minutes=window_start.minute % minutes)
    email_key, ip_key = throttle_keys(email, ip)
    docs = await auth_rate_limits_col.find({
        "key": {"$in": [email_key, ip_key]}, "action": action,
        "window": window_start.isoformat(),
    }).to_list(2)
    values = {doc["key"]: int(doc.get("count", 0)) for doc in docs}
    return {"email": values.get(email_key, 0), "ip": values.get(ip_key, 0)}


async def get_counter(email: str, ip: str, action: str, minutes: int) -> dict | None:
    now = now_utc()
    window_start = now.replace(second=0, microsecond=0)
    window_start -= timedelta(minutes=window_start.minute % minutes)
    docs = await auth_rate_limits_col.find({
        "key": {"$in": throttle_keys(email, ip)},
        "action": action,
        "window": window_start.isoformat(),
    }).sort("count", -1).limit(1).to_list(1)
    return docs[0] if docs else None


async def increment_counter(email: str, ip: str, action: str, minutes: int) -> dict:
    now = now_utc()
    window_start = now.replace(second=0, microsecond=0)
    window_start -= timedelta(minutes=window_start.minute % minutes)
    updated = []
    for key in throttle_keys(email, ip):
        updated.append(await auth_rate_limits_col.find_one_and_update(
            {"key": key, "action": action, "window": window_start.isoformat()},
            {"$inc": {"count": 1}, "$setOnInsert": {"created_at": now, "expires_at": window_start + timedelta(minutes=minutes * 2)}},
            upsert=True, return_document=ReturnDocument.AFTER,
        ))
    return max(updated, key=lambda item: int(item.get("count", 0)))


async def clear_counter(email: str, ip: str, action: str):
    await auth_rate_limits_col.delete_many({"key": {"$in": throttle_keys(email, ip)}, "action": action})


async def require_captcha(token: str | None, ip: str):
    if not token:
        raise HTTPException(status_code=429, detail="CAPTCHA verification is required", headers={"X-Captcha-Required": "true"})
    if not settings.TURNSTILE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="CAPTCHA protection is required but not configured")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post("https://challenges.cloudflare.com/turnstile/v0/siteverify", data={"secret": settings.TURNSTILE_SECRET_KEY, "response": token, "remoteip": ip})
            result = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="CAPTCHA verification is temporarily unavailable") from exc
    if not result.get("success"):
        raise HTTPException(status_code=429, detail="CAPTCHA verification failed", headers={"X-Captcha-Required": "true"})
