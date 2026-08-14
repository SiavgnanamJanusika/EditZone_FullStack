"""Append-only financial audit records and deterministic reconciliation."""
import hashlib
import json

from pymongo.errors import DuplicateKeyError

from app.core.utils import now_utc
from app.db.mongodb import payment_ledger_col, payments_col


def webhook_event_key(fields: dict) -> str:
    stable = "|".join(str(fields.get(key, "")) for key in (
        "merchant_id", "order_id", "payment_id", "status_code", "payhere_amount",
        "payhere_currency", "md5sig",
    ))
    return hashlib.sha256(stable.encode()).hexdigest()


async def append_ledger(payment: dict, event: str, *, amount: float = 0, metadata: dict | None = None) -> bool:
    payment_id = str(payment["_id"])
    previous = await payment_ledger_col.find_one({"payment_id": payment_id}, sort=[("sequence", -1)])
    sequence = int((previous or {}).get("sequence", 0)) + 1
    created_at = now_utc()
    payload = {
        "payment_id": payment_id, "order_id": payment.get("order_id"),
        "request_id": payment.get("request_id"), "editor_user_id": payment.get("editor_user_id"),
        "sequence": sequence, "event": event, "amount": round(float(amount), 2),
        "currency": payment.get("currency"), "metadata": metadata or {},
        "created_at": created_at.isoformat(), "previous_hash": (previous or {}).get("entry_hash", ""),
    }
    entry_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
    payload["created_at"] = created_at
    payload["entry_hash"] = entry_hash
    try:
        await payment_ledger_col.insert_one(payload)
        return True
    except DuplicateKeyError:
        return False


async def reconcile_payments() -> dict:
    from app.config import settings
    from app.services.payhere_service import PayHereAPIError, retrieve_payment
    checked = mismatches = provider_checked = provider_errors = 0
    async for payment in payments_col.find({"payment_type": "project_payment"}):
        checked += 1
        events = await payment_ledger_col.find({"payment_id": str(payment["_id"])}).sort("sequence", 1).to_list(1000)
        expected = {
            "AUTHORIZED": "authorized", "CAPTURED": "captured", "REFUNDED": "refunded",
            "CANCELLED": "cancelled",
        }.get(payment.get("status"))
        matched = not expected or any(entry.get("event") == expected for entry in events)
        if not matched:
            mismatches += 1
            await payments_col.update_one({"_id": payment["_id"]}, {"$set": {
                "reconciliation_status": "MISMATCH", "reconciled_at": now_utc(),
                "reconciliation_message": f"Missing immutable {expected} ledger event",
            }})
        else:
            await payments_col.update_one({"_id": payment["_id"]}, {"$set": {
                "reconciliation_status": "MATCHED", "reconciled_at": now_utc(),
            }, "$unset": {"reconciliation_message": ""}})
        if settings.PAYHERE_APP_ID and settings.PAYHERE_APP_SECRET and payment.get("status") in {"CAPTURED", "REFUNDED", "CHARGEBACK"}:
            try:
                provider_records = await retrieve_payment(payment["order_id"])
                provider_checked += 1
                provider_statuses = {str(item.get("status", "")).upper() for item in provider_records}
                expected_provider = {"CAPTURED": "RECEIVED", "REFUNDED": "REFUNDED", "CHARGEBACK": "CHARGEBACKED"}[payment["status"]]
                if expected_provider not in provider_statuses:
                    mismatches += 1
                    await payments_col.update_one({"_id": payment["_id"]}, {"$set": {
                        "reconciliation_status": "PROVIDER_MISMATCH", "reconciled_at": now_utc(),
                        "manual_review_required": True,
                        "reconciliation_message": f"PayHere did not report expected status {expected_provider}",
                    }})
            except PayHereAPIError:
                provider_errors += 1
                await payments_col.update_one({"_id": payment["_id"]}, {"$set": {
                    "reconciliation_status": "PROVIDER_UNAVAILABLE", "reconciled_at": now_utc(),
                    "reconciliation_message": "PayHere retrieval failed; retry scheduled",
                }})
    return {"checked": checked, "mismatches": mismatches, "provider_checked": provider_checked, "provider_errors": provider_errors}
