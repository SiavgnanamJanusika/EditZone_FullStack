from bson import ObjectId
from datetime import datetime, timezone
from fastapi import HTTPException


def oid(id_str: str) -> ObjectId:
    if not ObjectId.is_valid(id_str):
        raise HTTPException(status_code=400, detail="Invalid ID format")
    return ObjectId(id_str)


def serialize_doc(doc: dict) -> dict:
    """Convert a Mongo document into a JSON-safe dict."""
    if doc is None:
        return None
    out = dict(doc)
    if "_id" in out:
        out["id"] = str(out.pop("_id"))
    for key, value in out.items():
        out[key] = _json_safe(value)
    return out


def _json_safe(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def serialize_list(docs) -> list:
    return [serialize_doc(d) for d in docs]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime | None) -> datetime | None:
    """Normalize BSON/Python datetimes to timezone-aware UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
