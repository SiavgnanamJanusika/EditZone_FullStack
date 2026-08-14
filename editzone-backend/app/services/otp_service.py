import hashlib
from datetime import datetime, timezone

from fastapi import HTTPException
from redis.asyncio import Redis
from redis.exceptions import RedisError
from pymongo import ReturnDocument

from app.config import settings
from app.core.utils import now_utc
from app.db.mongodb import otps_col


def _otp_key(email: str, purpose: str) -> str:
    identity = hashlib.sha256(email.strip().lower().encode()).hexdigest()
    return f"editzone:otp:{purpose}:{identity}"


def _redis() -> Redis:
    return Redis.from_url(settings.REDIS_URL, decode_responses=True, socket_connect_timeout=2)


def _redis_required() -> bool:
    return settings.ENV.lower() in {"production", "staging"}


async def _fallback_or_raise(exc: RedisError):
    if _redis_required():
        raise HTTPException(status_code=503, detail="OTP service is temporarily unavailable") from exc


async def store_otp(email: str, purpose: str, otp_hash: str) -> dict:
    created_at = now_utc()
    client = _redis()
    try:
        key = _otp_key(email, purpose)
        await client.hset(key, mapping={
            "otp_hash": otp_hash,
            "attempts": "0",
            "locked": "0",
            "created_at": created_at.isoformat(),
        })
        await client.expire(key, settings.OTP_EXPIRE_SECONDS)
        return {"backend": "redis", "key": key, "otp_hash": otp_hash, "attempts": 0, "locked": False, "created_at": created_at}
    except RedisError as exc:
        await _fallback_or_raise(exc)
        await otps_col.update_one(
            {"email": email, "purpose": purpose},
            {"$set": {"email": email, "purpose": purpose, "otp_hash": otp_hash,
                      "attempts": 0, "locked": False, "created_at": created_at}},
            upsert=True,
        )
        return {"backend": "mongodb", "email": email, "purpose": purpose, "otp_hash": otp_hash, "attempts": 0, "locked": False, "created_at": created_at}
    finally:
        await client.aclose()


async def get_otp(email: str, purpose: str) -> dict | None:
    client = _redis()
    try:
        key = _otp_key(email, purpose)
        values = await client.hgetall(key)
        if not values:
            return None
        return {
            "backend": "redis", "key": key,
            "otp_hash": values.get("otp_hash", ""),
            "attempts": int(values.get("attempts", 0)),
            "locked": values.get("locked") == "1",
            "created_at": datetime.fromisoformat(values["created_at"]).astimezone(timezone.utc),
        }
    except (RedisError, ValueError) as exc:
        if isinstance(exc, RedisError):
            await _fallback_or_raise(exc)
        record = await otps_col.find_one({"email": email, "purpose": purpose})
        if record:
            record["backend"] = "mongodb"
        return record
    finally:
        await client.aclose()


async def increment_otp_attempts(record: dict) -> int:
    if record.get("backend") == "redis":
        client = _redis()
        try:
            return int(await client.hincrby(record["key"], "attempts", 1))
        finally:
            await client.aclose()
    updated = await otps_col.find_one_and_update(
        {"_id": record["_id"], "locked": {"$ne": True}},
        {"$inc": {"attempts": 1}},
        return_document=ReturnDocument.AFTER,
    )
    return int((updated or {}).get("attempts", int(record.get("attempts", 0)) + 1))


async def lock_otp(record: dict):
    if record.get("backend") == "redis":
        client = _redis()
        try:
            await client.hset(record["key"], "locked", "1")
        finally:
            await client.aclose()
        return
    await otps_col.update_one({"_id": record["_id"]}, {"$set": {"locked": True, "locked_at": now_utc()}})


async def consume_otp(record: dict, expected_hash: str) -> bool:
    """Atomically consume a matching OTP so concurrent/repeated submissions lose."""
    if record.get("backend") == "redis":
        client = _redis()
        try:
            consumed = await client.eval(
                """
                if redis.call('HGET', KEYS[1], 'otp_hash') == ARGV[1] then
                    return redis.call('DEL', KEYS[1])
                end
                return 0
                """,
                1,
                record["key"],
                expected_hash,
            )
            return int(consumed or 0) == 1
        finally:
            await client.aclose()
    deleted = await otps_col.find_one_and_delete({
        "_id": record["_id"],
        "otp_hash": expected_hash,
        "locked": {"$ne": True},
    })
    return deleted is not None


async def delete_otp(email: str, purpose: str):
    client = _redis()
    try:
        await client.delete(_otp_key(email, purpose))
    except RedisError as exc:
        await _fallback_or_raise(exc)
    finally:
        await client.aclose()
    await otps_col.delete_one({"email": email, "purpose": purpose})


async def purge_otp_cache(email: str) -> int:
    """Remove every Redis OTP value currently supported for an account."""
    client = _redis()
    keys = [_otp_key(email, purpose) for purpose in ("verify_email", "reset_password")]
    try:
        return int(await client.delete(*keys))
    except RedisError as exc:
        await _fallback_or_raise(exc)
        return 0
    finally:
        await client.aclose()
