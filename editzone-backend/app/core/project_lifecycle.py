from fastapi import HTTPException
from pymongo import ReturnDocument

from app.core.utils import now_utc
from app.db.mongodb import requests_col


TERMINAL_STATUSES = {"rejected", "cancelled", "refunded", "expired", "completed"}
CHAT_OPEN_STATUSES = {
    "accepted", "in_progress", "overdue", "admin_review", "delivered",
    "revision_requested", "cancel_requested", "disputed", "refund_pending",
}

ALLOWED_TRANSITIONS = {
    "pending": {"accepted", "rejected", "cancelled", "expired"},
    "accepted": {"in_progress", "cancel_requested", "cancelled", "payment_failed", "expired"},
    "payment_failed": {"accepted", "cancel_requested", "cancelled", "expired"},
    "in_progress": {"admin_review", "cancel_requested", "disputed", "overdue", "refund_pending"},
    "overdue": {"admin_review", "cancel_requested", "disputed", "in_progress", "refund_pending"},
    "admin_review": {"delivered", "completed", "revision_requested", "disputed", "cancel_requested", "refund_pending"},
    "revision_requested": {"in_progress", "admin_review", "cancel_requested", "disputed", "refund_pending"},
    "delivered": {"completed", "revision_requested", "disputed", "cancel_requested", "refund_pending"},
    "cancel_requested": {"cancelled", "in_progress", "overdue", "disputed", "refund_pending"},
    "disputed": {"admin_review", "in_progress", "revision_requested", "refund_pending", "cancelled", "completed"},
    "refund_pending": {"refunded", "disputed", "cancelled"},
    "completed": {"disputed", "refund_pending"},
}


async def transition_project(project: dict, target: str, actor: dict | None, *, reason: str = "", extra: dict | None = None) -> dict:
    source = project["status"]
    if target == source:
        return project
    if target not in ALLOWED_TRANSITIONS.get(source, set()):
        raise HTTPException(status_code=409, detail=f"Project cannot move from {source} to {target}")
    now = now_utc()
    event = {
        "from": source, "to": target, "reason": reason[:1000],
        "actor_id": actor.get("_id") if actor else None,
        "actor_role": actor.get("role", "system") if actor else "system",
        "created_at": now,
    }
    fields = {"status": target, "status_updated_at": now, f"{target}_at": now, **(extra or {})}
    updated = await requests_col.find_one_and_update(
        {"_id": project["_id"], "status": source},
        {"$set": fields, "$push": {"status_history": event}},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise HTTPException(status_code=409, detail="Project changed while this action was being processed; refresh and retry")
    return updated
