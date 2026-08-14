from datetime import timedelta

from fastapi import HTTPException
from pymongo import ReturnDocument

from app.core.utils import now_utc
from app.db.mongodb import editor_payouts_col, payment_escrows_col, payments_col
from app.services.financial_records import append_ledger
from app.services.payhere_service import (
    PayHereAPIError,
    capture_authorization,
    format_amount,
    minor_to_amount,
    split_project_amount,
)


async def _record_settlement(payment: dict) -> dict:
    """Persist the fixed-precision 10% + 2% ledger exactly once."""
    gross, platform, service, admin_total, editor_net = split_project_amount(
        payment.get("captured_amount") or payment.get("authorized_amount") or payment["amount"]
    )
    calculated_at = now_utc()
    amounts = {
        "gross_amount_minor": gross,
        "platform_commission_minor": platform,
        "service_fee_minor": service,
        "admin_total_minor": admin_total,
        "editor_net_minor": editor_net,
        "gross_amount": float(minor_to_amount(gross)),
        "platform_commission": float(minor_to_amount(platform)),
        "service_fee": float(minor_to_amount(service)),
        "admin_total": float(minor_to_amount(admin_total)),
        "editor_net": float(minor_to_amount(editor_net)),
        "platform_fee_amount": float(minor_to_amount(admin_total)),
        "platform_fee_minor": admin_total,
        "commission_amount": float(minor_to_amount(platform)),
        "editor_earning_amount": float(minor_to_amount(editor_net)),
        "editor_earning_minor": editor_net,
        "editor_payout_amount": float(minor_to_amount(editor_net)),
        "editor_earning_status": "PAYABLE",
        "commission_calculated": True,
        "commission_calculated_at": calculated_at,
        "editor_payable": float(minor_to_amount(editor_net)),
        "editor_payout_status": "PENDING",
        "settlement_status": "EDITOR_PAYOUT_PENDING",
    }
    await payments_col.update_one(
        {"_id": payment["_id"], "commission_calculated": {"$ne": True}},
        {"$set": amounts},
    )
    await editor_payouts_col.update_one(
        {"payment_id": str(payment["_id"])},
        {"$setOnInsert": {
            "payment_id": str(payment["_id"]),
            "order_id": payment.get("order_id"),
            "project_id": payment.get("request_id"),
            "editor_id": payment.get("editor_user_id"),
            "currency": payment.get("currency", "LKR"),
            "gross_amount_minor": gross,
            "platform_commission_minor": platform,
            "service_fee_minor": service,
            "admin_total_minor": admin_total,
            "editor_net_minor": editor_net,
            "gross_amount": float(minor_to_amount(gross)),
            "platform_commission": float(minor_to_amount(platform)),
            "service_fee": float(minor_to_amount(service)),
            "admin_total": float(minor_to_amount(admin_total)),
            "editor_net": float(minor_to_amount(editor_net)),
            "payout_status": "PENDING",
            "payment_received": True,
            "commission_calculated": True,
            "created_at": calculated_at,
            "updated_at": calculated_at,
        }},
        upsert=True,
    )
    return amounts


async def capture_and_record_project_payment(request_id: str, actor_id) -> dict:
    """Capture a PayHere authorization once, then create one internal payout."""
    existing = await payments_col.find_one({
        "request_id": request_id,
        "payment_type": "project_payment",
    })
    if not existing:
        # Quote checkout is captured by PayHere before its signed callback. Its
        # payout remains in CALCULATING until this delivery-release workflow.
        quote_payment = await payments_col.find_one({
            "request_id": request_id,
            "payment_type": "project_quote_payment",
            "status": "SUCCESS",
            "callback_verified": True,
        })
        if quote_payment:
            return quote_payment
    if not existing:
        raise HTTPException(status_code=409, detail="Client payment has not been successfully authorized/paid.")
    if existing.get("status") == "CAPTURED":
        await _record_settlement(existing)
        return await payments_col.find_one({"_id": existing["_id"]})
    if existing.get("status") != "AUTHORIZED":
        raise HTTPException(status_code=409, detail="Client payment has not been successfully authorized/paid.")
    if not existing.get("authorization_token"):
        raise HTTPException(status_code=409, detail="The PayHere authorization cannot be captured.")

    stale_before = now_utc() - timedelta(minutes=5)
    payment = await payments_col.find_one_and_update(
        {
            "_id": existing["_id"],
            "status": "AUTHORIZED",
            "$or": [
                {"capture_in_progress": {"$ne": True}},
                {"capture_started_at": {"$lt": stale_before}},
            ],
        },
        {"$set": {
            "capture_in_progress": True,
            "capture_started_at": now_utc(),
            "capture_requested_by": actor_id,
        }},
        return_document=ReturnDocument.AFTER,
    )
    if not payment:
        latest = await payments_col.find_one({"_id": existing["_id"]})
        if latest and latest.get("status") == "CAPTURED":
            await _record_settlement(latest)
            return await payments_col.find_one({"_id": latest["_id"]})
        raise HTTPException(status_code=409, detail="Payment capture is already processing.")

    escrow = await payment_escrows_col.find_one_and_update(
        {"payment_id": str(payment["_id"]), "status": "FUNDED"},
        {"$set": {"status": "RELEASE_PENDING", "updated_at": now_utc()}},
        return_document=ReturnDocument.AFTER,
    )
    if not escrow:
        await payments_col.update_one(
            {"_id": payment["_id"]},
            {"$unset": {"capture_in_progress": "", "capture_started_at": ""}},
        )
        raise HTTPException(status_code=409, detail="Payment protection is not funded or is already processing.")

    try:
        captured = await capture_authorization(
            payment["authorization_token"],
            format_amount(payment["authorized_amount"]),
        )
    except PayHereAPIError as exc:
        await payments_col.update_one(
            {"_id": payment["_id"], "status": "AUTHORIZED"},
            {"$unset": {"capture_in_progress": "", "capture_started_at": ""}},
        )
        await payment_escrows_col.update_one(
            {"payment_id": str(payment["_id"]), "status": "RELEASE_PENDING"},
            {"$set": {"status": "FUNDED", "updated_at": now_utc()}},
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    captured_amount = format_amount(captured.get("captured_amount", payment["authorized_amount"]))
    updated = await payments_col.find_one_and_update(
        {"_id": payment["_id"], "status": "AUTHORIZED", "capture_in_progress": True},
        {"$set": {
            "status": "CAPTURED",
            "provider_status": "CAPTURED",
            "protection_status": "RELEASED",
            "payment_received": True,
            "captured_amount": float(captured_amount),
            "payment_id": str(captured.get("payment_id", payment.get("payment_id", ""))),
            "captured_at": now_utc(),
            "updated_at": now_utc(),
        }, "$unset": {
            "capture_in_progress": "",
            "capture_started_at": "",
            "authorization_token": "",
        }},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise HTTPException(status_code=409, detail="Capture succeeded but its local result needs reconciliation.")
    await append_ledger(updated, "captured", amount=updated["captured_amount"])
    await payment_escrows_col.update_one(
        {"payment_id": str(payment["_id"]), "status": "RELEASE_PENDING"},
        {"$set": {
            "status": "RELEASED",
            "settlement_status": "PAYABLE",
            "released_at": now_utc(),
            "updated_at": now_utc(),
        }},
    )
    await _record_settlement(updated)
    return await payments_col.find_one({"_id": updated["_id"]})
