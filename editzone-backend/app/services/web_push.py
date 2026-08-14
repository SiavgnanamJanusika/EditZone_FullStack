"""Web Push delivery for chat notifications.

Subscription endpoints and private VAPID material never leave the backend.
Failures are supplementary and cannot roll back an already-persisted message.
"""
import asyncio
import json
import logging

from app.config import settings
from app.core.utils import now_utc
from app.db.mongodb import push_subscriptions_col

logger = logging.getLogger(__name__)


def web_push_configured() -> bool:
    return bool(settings.WEB_PUSH_VAPID_PUBLIC_KEY and settings.WEB_PUSH_VAPID_PRIVATE_KEY)


async def send_chat_push(receiver_id: str, payload: dict) -> None:
    if not receiver_id or not web_push_configured():
        return
    subscriptions = await push_subscriptions_col.find({"user_id": receiver_id, "active": True}).to_list(20)
    if not subscriptions:
        return

    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.error("Web Push is configured but pywebpush is not installed")
        return

    serialized = json.dumps(payload, separators=(",", ":"))
    vapid_claims = {"sub": settings.WEB_PUSH_VAPID_SUBJECT}

    def deliver(subscription: dict):
        try:
            webpush(
                subscription_info={
                    "endpoint": subscription["endpoint"],
                    "keys": subscription["keys"],
                },
                data=serialized,
                vapid_private_key=settings.WEB_PUSH_VAPID_PRIVATE_KEY,
                vapid_claims=vapid_claims,
                ttl=120,
            )
            return subscription["endpoint"], None
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            return subscription["endpoint"], status or str(exc)

    results = await asyncio.gather(*(asyncio.to_thread(deliver, item) for item in subscriptions))
    for endpoint, error in results:
        if error in (404, 410):
            await push_subscriptions_col.update_one(
                {"endpoint": endpoint, "user_id": receiver_id},
                {"$set": {"active": False, "disabled_at": now_utc()}},
            )
        elif error:
            logger.warning("Web Push delivery failed receiver=%s status=%s", receiver_id[-6:], error)
