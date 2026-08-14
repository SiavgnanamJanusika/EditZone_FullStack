from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, HttpUrl

from app.config import settings
from app.db.mongodb import notifications_col, push_subscriptions_col
from app.core.security import get_current_user
from app.core.utils import serialize_list, oid, now_utc

router = APIRouter(prefix="/api/v1/notifications", tags=["Notifications"])


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=20, max_length=512)
    auth: str = Field(min_length=8, max_length=256)


class PushSubscription(BaseModel):
    endpoint: HttpUrl
    keys: PushKeys


@router.get("/push/public-key")
async def push_public_key(current_user: dict = Depends(get_current_user)):
    return {"public_key": settings.WEB_PUSH_VAPID_PUBLIC_KEY, "enabled": bool(settings.WEB_PUSH_VAPID_PUBLIC_KEY and settings.WEB_PUSH_VAPID_PRIVATE_KEY)}


@router.post("/push/subscribe")
async def subscribe_push(subscription: PushSubscription, current_user: dict = Depends(get_current_user)):
    endpoint = str(subscription.endpoint)
    now = now_utc()
    await push_subscriptions_col.update_one(
        {"endpoint": endpoint},
        {"$set": {
            "user_id": str(current_user["_id"]),
            "endpoint": endpoint,
            "keys": subscription.keys.model_dump(),
            "active": True,
            "updated_at": now,
            "expires_at": now + timedelta(days=180),
        }, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return {"success": True}


@router.delete("/push/subscription")
async def unsubscribe_push(subscription: PushSubscription, current_user: dict = Depends(get_current_user)):
    await push_subscriptions_col.update_one(
        {"endpoint": str(subscription.endpoint), "user_id": str(current_user["_id"])},
        {"$set": {"active": False, "disabled_at": now_utc()}},
    )
    return {"success": True}


@router.get("")
async def list_notifications(current_user: dict = Depends(get_current_user)):
    docs = await notifications_col.find({"user_id": current_user["_id"]}).sort("created_at", -1).to_list(100)
    return {"notifications": serialize_list(docs)}


@router.patch("/read-all")
async def mark_all_read(current_user: dict = Depends(get_current_user)):
    await notifications_col.update_many(
        {"user_id": current_user["_id"], "is_read": False}, {"$set": {"is_read": True}}
    )
    return {"message": "All notifications marked as read"}


@router.patch("/{notification_id}/read")
async def mark_read(notification_id: str, current_user: dict = Depends(get_current_user)):
    result = await notifications_col.update_one(
        {"_id": oid(notification_id), "user_id": current_user["_id"]},
        {"$set": {"is_read": True}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"message": "Marked as read"}
