"""Versioned final-price quotes and commission-based PayHere checkout."""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.config import settings
from app.core.security import get_current_user, require_editor, require_user
from app.core.utils import ensure_utc, now_utc, oid, serialize_doc
from app.db.mongodb import (
    editor_payouts_col, messages_col, notifications_col, payment_ledger_col,
    payment_webhooks_col, payments_col, project_quotes_col, requests_col, users_col,
)
from app.schemas.schemas import CreateQuoteBody, FinalQuoteBody, InitiateQuotePaymentBody
from app.services.financial_records import webhook_event_key
from app.services.payhere_service import PayHereAPIError, amount_to_minor, checkout_hash, format_amount, minor_to_amount, notification_is_valid, payhere_checkout_config, split_project_amount
from app.sockets.socket_manager import sio

router = APIRouter(tags=["Project Quotes and Commission"])
TERMINAL_QUOTES = {"PAID", "EXPIRED", "CANCELLED", "NEEDS_REVISION"}
RETRYABLE_PAYMENT = {"FAILED", "CANCELLED", "CHARGEDBACK"}
STATUS_MAP = {"2": "SUCCESS", "0": "PENDING", "-1": "CANCELLED", "-2": "FAILED", "-3": "CHARGEDBACK"}
logger = logging.getLogger(__name__)


def quote_money(amount) -> dict:
    gross, commission, client_fee, revenue, editor_net = split_project_amount(amount)
    return {
        "project_amount_minor": gross,
        "client_service_fee_minor": client_fee,
        "client_total_minor": gross + client_fee,
        "editor_commission_minor": commission,
        "editor_net_payable_minor": editor_net,
        "editzone_gross_revenue_minor": revenue,
    }


def public_quote(doc: dict) -> dict:
    result = serialize_doc(doc)
    for key in ("project_amount", "client_service_fee", "client_total", "editor_commission", "editor_net_payable"):
        minor = doc.get(f"{key}_minor")
        if minor is not None:
            result[key] = minor_to_amount(minor)
    result["editor_net_earning"] = result.get("editor_net_payable")
    result["can_pay"] = doc.get("status") == "SENT"
    result["can_edit"] = doc.get("status") in {"SENT", "EXPIRED", "NEEDS_REVISION"}
    return result


async def _project_participant(project_id: str, user: dict) -> dict:
    project = await requests_col.find_one({"_id": oid(project_id)})
    if not project:
        raise HTTPException(404, "Project not found")
    if user.get("role") != "admin" and user["_id"] not in (project.get("user_id"), project.get("editor_user_id")):
        raise HTTPException(403, "Not authorized for this project")
    return project


def _expiry_error(message: str):
    raise HTTPException(422, detail=[{"type": "value_error", "loc": ["body", "expires_at"], "msg": message, "input": None}])


def _normalise_expiry(value: datetime | None, expiry_days: int | None = None) -> datetime:
    now = now_utc()
    if expiry_days is not None:
        if not 1 <= expiry_days <= 30:
            _expiry_error("Quote expiry must be between 1 and 30 days")
        return now + timedelta(days=expiry_days)
    expiry = value or (now + timedelta(hours=settings.QUOTE_DEFAULT_EXPIRY_HOURS))
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    expiry = expiry.astimezone(timezone.utc)
    if expiry <= now:
        _expiry_error("Quote expiry must be in the future")
    # ISO timestamps originate on another clock, so tolerate minor transit and
    # clock skew while preserving the 30-calendar-day business limit.
    if expiry > now + timedelta(days=30, seconds=60):
        _expiry_error("Quote expiry cannot be more than 30 days away")
    return expiry


async def _expire_quote(quote: dict | None) -> dict | None:
    created_at = ensure_utc(quote.get("created_at")) if quote else None
    expires_at = ensure_utc(quote.get("expires_at")) if quote else None
    invalid_window = bool(
        created_at and expires_at
        and expires_at > created_at + timedelta(days=30, seconds=60)
    )
    if quote and quote.get("status") == "SENT" and (invalid_window or (expires_at and expires_at <= now_utc())):
        changed = await project_quotes_col.find_one_and_update(
            {"_id": quote["_id"], "status": "SENT"},
            {"$set": {"status": "NEEDS_REVISION" if invalid_window else "EXPIRED", "updated_at": now_utc()}},
            return_document=ReturnDocument.AFTER,
        )
        return changed or quote
    return quote


@router.get("/api/v1/quotes/project/{project_id}")
async def current_quote(project_id: str, current_user: dict = Depends(get_current_user)):
    await _project_participant(project_id, current_user)
    quote = await project_quotes_col.find_one(
        {"project_id": project_id},
        sort=[("quote_version", -1)],
    )
    quote = await _expire_quote(quote)
    return {"quote": public_quote(quote) if quote else None}


@router.get("/api/v1/projects/{project_id}/final-quote")
async def project_final_quote(project_id: str, current_user: dict = Depends(get_current_user)):
    return await current_quote(project_id, current_user)


@router.get("/api/v1/quotes/{quote_id}")
async def quote_by_id(quote_id: str, current_user: dict = Depends(get_current_user)):
    quote = await project_quotes_col.find_one({"_id": oid(quote_id)})
    if not quote:
        raise HTTPException(404, "Quote not found")
    project = await _project_participant(quote["project_id"], current_user)
    quote = await _expire_quote(quote)
    return {"quote": public_quote(quote), "project": serialize_doc(project)}


async def _create_quote(project_id: str, amount, note: str | None, expires_at: datetime | None, current_user: dict, expiry_days: int | None = None):
    project = await _project_participant(project_id, current_user)
    if project.get("editor_user_id") != current_user["_id"]:
        raise HTTPException(403, "Only the assigned editor can set the final amount")
    if project.get("status") not in {"accepted", "in_progress", "overdue", "revision_requested", "admin_review", "delivered"}:
        raise HTTPException(409, "The project must be accepted before setting the final amount")
    if project.get("status") in {"completed", "cancelled", "refund_pending", "refunded"}:
        raise HTTPException(409, "A final payment request cannot be created for this project")
    expiry = _normalise_expiry(expires_at, expiry_days)
    minor = amount_to_minor(amount)
    min_minor = amount_to_minor(settings.PROJECT_MIN_AMOUNT)
    max_minor = amount_to_minor(settings.PROJECT_MAX_AMOUNT)
    if minor < min_minor or minor > max_minor:
        raise HTTPException(422, f"Amount must be between LKR {settings.PROJECT_MIN_AMOUNT:.2f} and {settings.PROJECT_MAX_AMOUNT:.2f}")
    active = await project_quotes_col.find_one(
        {"project_id": project_id, "status": {"$in": ["SENT", "PAYMENT_PENDING", "PAID"]}},
        sort=[("quote_version", -1)],
    )
    active = await _expire_quote(active)
    if active and active.get("status") == "PAID":
        raise HTTPException(409, "A paid quote cannot be changed")
    if active and active.get("status") == "PAYMENT_PENDING":
        raise HTTPException(409, "This quote has a payment attempt and cannot be changed")
    latest = await project_quotes_col.find_one({"project_id": project_id}, sort=[("quote_version", -1)])
    version = int((latest or {}).get("quote_version", 0)) + 1
    now = now_utc()
    doc = {
        "project_id": project_id, "chat_room_id": project_id,
        "editor_id": current_user["_id"], "client_id": project["user_id"],
        "quote_version": version, "currency": "LKR", **quote_money(amount),
        "client_fee_rate_snapshot": settings.CLIENT_SERVICE_FEE_PERCENT,
        "editor_commission_rate_snapshot": settings.EDITOR_COMMISSION_PERCENT,
        "commission_mode": settings.EDITOR_COMMISSION_MODE,
        "note": note, "expires_at": expiry,
        "status": "SENT", "created_at": now, "updated_at": now, "locked_at": now,
    }
    try:
        if active and active.get("status") == "SENT":
            await project_quotes_col.update_one({"_id": active["_id"], "status": "SENT"}, {"$set": {"status": "REVISED", "updated_at": now}})
        result = await project_quotes_col.insert_one(doc)
        doc["_id"] = result.inserted_id
    except DuplicateKeyError:
        raise HTTPException(409, "A duplicate quote submission was blocked")
    payload = public_quote(doc)
    system_message = {
        "request_id": project_id, "sender_id": "system", "receiver_id": str(project["user_id"]),
        "text": "The editor set the final project price. Review the payment card to continue securely.",
        "message_type": "system", "quote_id": str(doc["_id"]), "delivery_status": "sent", "created_at": now,
    }
    inserted = await messages_col.insert_one(system_message)
    system_message["_id"] = inserted.inserted_id
    await sio.emit("new_message", serialize_doc(system_message), room=f"chat_{project_id}")
    await sio.emit("quote_updated", {"project_id": project_id, "quote": payload}, room=f"chat_{project_id}")
    await sio.emit("project_quote_created", {"project_id": project_id, "quote": payload}, room=f"chat_{project_id}")
    return payload


@router.post("/api/v1/quotes", status_code=201)
async def set_final_amount(body: CreateQuoteBody, current_user: dict = Depends(require_editor)):
    return await _create_quote(body.project_id, body.amount, body.note, body.expires_at, current_user, body.expiry_days)


@router.post("/api/v1/projects/{project_id}/final-quote", status_code=201)
@router.post("/api/v1/projects/{project_id}/final-quote/revise", status_code=201)
async def create_project_final_quote(project_id: str, body: FinalQuoteBody, current_user: dict = Depends(require_editor)):
    # Project-chat quotes intentionally use one server-owned seven-day expiry.
    return await _create_quote(project_id, body.amount, body.note, None, current_user, 7)


@router.post("/api/v1/payments/payhere/initiate", status_code=201)
async def initiate_quote_payment(body: InitiateQuotePaymentBody, current_user: dict = Depends(require_user)):
    quote = await project_quotes_col.find_one({"_id": oid(body.quote_id)})
    if not quote:
        raise HTTPException(404, "Quote not found")
    project = await _project_participant(quote["project_id"], current_user)
    if quote.get("client_id") != current_user["_id"] or project.get("user_id") != current_user["_id"]:
        raise HTTPException(403, "Only this project's client can pay")
    quote = await _expire_quote(quote)
    latest = await project_quotes_col.find_one({"project_id": quote["project_id"]}, sort=[("quote_version", -1)])
    if latest and latest["_id"] != quote["_id"]:
        raise HTTPException(409, "A newer final payment request is available")
    if quote.get("editor_id") != project.get("editor_user_id") or quote.get("status") in TERMINAL_QUOTES | {"SUPERSEDED", "REVISED"}:
        raise HTTPException(409, "This quote is not payable")
    try:
        payhere = payhere_checkout_config()
    except PayHereAPIError as exc:
        raise HTTPException(503, str(exc)) from exc
    existing = await payments_col.find_one({"quote_id": body.quote_id, "status": {"$in": ["INITIATED", "PENDING", "SUCCESS"]}})
    if existing:
        if existing["status"] == "SUCCESS":
            raise HTTPException(409, "This quote is already paid")
        raise HTTPException(409, "A payment order is already pending")
    now = now_utc()
    locked = await project_quotes_col.find_one_and_update(
        {"_id": quote["_id"], "status": "SENT", "$or": [{"expires_at": None}, {"expires_at": {"$gt": now}}]},
        {"$set": {"status": "PAYMENT_PENDING", "updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    if not locked:
        raise HTTPException(409, "This payment request is expired, paid, or already has a payment attempt")
    order_id = f"EZ-{quote['project_id'][-6:].upper()}-{int(now.timestamp())}-{uuid.uuid4().hex[:12].upper()}"
    payment = {
        "order_id": order_id, "quote_id": body.quote_id, "project_id": quote["project_id"],
        "request_id": quote["project_id"], "client_id": current_user["_id"], "user_id": current_user["_id"],
        "editor_id": quote["editor_id"], "editor_user_id": quote["editor_id"],
        "merchant_id": payhere["merchant_id"], "currency": payhere["currency"],
        "expected_amount_minor": quote["client_total_minor"], "amount_minor": quote["client_total_minor"],
        "project_amount_minor": quote["project_amount_minor"], "client_service_fee_minor": quote["client_service_fee_minor"],
        "editor_commission_minor": quote["editor_commission_minor"], "editor_net_payable_minor": quote["editor_net_payable_minor"],
        "payment_type": "project_quote_payment", "payment_method": "payhere_checkout",
        "quote_version": quote.get("quote_version"), "expires_at": quote.get("expires_at"),
        "status": "INITIATED", "callback_verified": False, "created_at": now, "updated_at": now,
    }
    try:
        result = await payments_col.insert_one(payment)
        payment["_id"] = result.inserted_id
    except DuplicateKeyError:
        await project_quotes_col.update_one({"_id": quote["_id"], "status": "PAYMENT_PENDING"}, {"$set": {"status": "SENT", "updated_at": now}})
        raise HTTPException(409, "A duplicate payment order was blocked")
    amount = minor_to_amount(quote["client_total_minor"])
    names = (current_user.get("username") or "EditZone Client").split(maxsplit=1)
    fields = {
        "merchant_id": payhere["merchant_id"], "return_url": f"{settings.FRONTEND_URL.rstrip('/')}/payment/success?order_id={order_id}",
        "cancel_url": f"{settings.FRONTEND_URL.rstrip('/')}/payment/cancel?order_id={order_id}", "notify_url": payhere["notify_url"],
        "order_id": order_id, "items": str(project.get("project_title") or "EditZone project")[:120],
        "amount": amount, "currency": payhere["currency"], "hash": checkout_hash(order_id, amount, payhere["currency"], merchant_id=payhere["merchant_id"], merchant_secret=payhere["merchant_secret"]),
        "first_name": names[0], "last_name": names[1] if len(names) > 1 else "-", "email": current_user["email"],
        "phone": current_user.get("phone") or "0000000000", "address": body.address or current_user.get("address") or "Sri Lanka",
        "city": body.city or current_user.get("city") or current_user.get("district") or "Colombo", "country": "Sri Lanka",
        "custom_1": body.quote_id, "custom_2": quote["project_id"],
    }
    logger.info(
        "PayHere init merchant=%s order=%s amount=%s currency=%s hash_present=%s secret_loaded=%s secret_length=%s mode=%s checkout=%s return_url=%s cancel_url=%s notify_url=%s",
        payhere["merchant_id"], order_id, amount, payhere["currency"], bool(fields["hash"]),
        bool(payhere["merchant_secret"]), len(payhere["merchant_secret"]), payhere["mode"],
        payhere["checkout_url"], fields["return_url"], fields["cancel_url"], fields["notify_url"],
    )
    return {"checkout_url": payhere["checkout_url"], "payment_id": str(payment["_id"]), "order_id": order_id, "sandbox": True, "payment_data": fields, "action_url": payhere["checkout_url"], "fields": fields}


@router.post("/api/v1/payments/payhere/create", status_code=201)
async def create_quote_payment(body: InitiateQuotePaymentBody, current_user: dict = Depends(require_user)):
    return await initiate_quote_payment(body, current_user)


@router.get("/api/v1/payments/{order_id}/status")
async def quote_payment_status(order_id: str, current_user: dict = Depends(get_current_user)):
    payment = await payments_col.find_one({"order_id": order_id})
    if not payment:
        raise HTTPException(404, "Payment not found")
    allowed = {payment.get("client_id"), payment.get("editor_id"), payment.get("user_id"), payment.get("editor_user_id")}
    if current_user.get("role") != "admin" and current_user["_id"] not in allowed:
        raise HTTPException(403, "Not authorized to view this payment")
    return {"order_id": order_id, "quote_id": payment.get("quote_id"), "project_id": payment.get("project_id"), "status": payment["status"], "callback_verified": bool(payment.get("callback_verified"))}


async def _insert_financial_entries(payment: dict, event: str, sign: int = 1):
    values = [
        ("CLIENT_PAYMENT_RECEIVED", payment["expected_amount_minor"]),
        ("PROJECT_BASE_AMOUNT", payment["project_amount_minor"]),
        ("CLIENT_SERVICE_FEE_REVENUE", payment["client_service_fee_minor"]),
        ("EDITOR_GROSS_EARNING", payment["project_amount_minor"]),
        ("EDITOR_COMMISSION_REVENUE", payment["editor_commission_minor"]),
        ("EDITOR_NET_PAYABLE", payment["editor_net_payable_minor"]),
    ]
    occurred_at = now_utc()
    for sequence, (entry_type, amount) in enumerate(values, start=1):
        try:
            await payment_ledger_col.insert_one({
                "dedupe_key": f"{payment['order_id']}:{event}:{entry_type}", "payment_id": str(payment["_id"]),
                "sequence": sequence if sign > 0 else sequence + 100,
                "order_id": payment["order_id"], "project_id": payment["project_id"], "quote_id": payment["quote_id"],
                "client_id": payment["client_id"], "editor_user_id": payment["editor_id"],
                "entry_type": entry_type, "event": event, "direction": "CREDIT" if sign > 0 else "REVERSAL",
                "amount_minor": sign * amount, "currency": "LKR", "source_status": payment.get("status"),
                "effective_at": occurred_at, "created_at": occurred_at,
            })
        except DuplicateKeyError:
            pass


async def _editor_quote_earnings(editor_id, month: str | None):
    if month and (len(month) != 7 or month[4] != "-"):
        raise HTTPException(422, "Month must use YYYY-MM")
    payout_query = {"editor_id": editor_id}
    if month:
        payout_query["month"] = month
    payouts = await editor_payouts_col.find(payout_query).sort("created_at", -1).to_list(500)
    eligible = [p for p in payouts if p.get("payout_eligible", p.get("payout_status") != "CALCULATING")]
    gross = sum(p.get("gross_amount_minor", 0) for p in eligible)
    commission = sum(p.get("editor_commission_minor", 0) for p in eligible)
    net = sum(p.get("net_amount_minor", 0) + p.get("adjustments_minor", 0) for p in eligible)
    paid = sum(p.get("net_amount_minor", 0) + p.get("adjustments_minor", 0) for p in eligible if p.get("payout_status") == "PAID")
    return {
        "currency": "LKR", "month": month, "gross_earnings": minor_to_amount(gross),
        "commission": minor_to_amount(commission), "net_payout": minor_to_amount(net),
        "pending_payout": minor_to_amount(max(net - paid, 0)), "paid_payout": minor_to_amount(paid),
        "paid_projects": len(eligible), "payouts": [serialize_doc(p) for p in payouts],
        "total_earnings": minor_to_amount(net), "pending_earnings": minor_to_amount(max(net - paid, 0)),
        "available": minor_to_amount(max(net - paid, 0)),
    }


@router.get("/api/v1/editors/me/earnings")
async def editor_quote_earnings(month: str | None = None, current_user: dict = Depends(require_editor)):
    return await _editor_quote_earnings(current_user["_id"], month)


@router.get("/api/v1/editors/me/commission-statements")
async def editor_commission_statements(month: str | None = None, current_user: dict = Depends(require_editor)):
    summary = await _editor_quote_earnings(current_user["_id"], month)
    ledger_query = {"editor_user_id": current_user["_id"], "entry_type": {"$in": ["EDITOR_GROSS_EARNING", "EDITOR_COMMISSION_REVENUE", "EDITOR_NET_PAYABLE"]}}
    entries = await payment_ledger_col.find(ledger_query).sort("effective_at", -1).to_list(1000)
    if month:
        zone = ZoneInfo(settings.APP_TIMEZONE)
        entries = [entry for entry in entries if (entry.get("effective_at") or entry.get("created_at")).astimezone(zone).strftime("%Y-%m") == month]
    return {**summary, "statements": [serialize_doc(entry) for entry in entries]}


@router.post("/api/v1/payments/payhere/notify", include_in_schema=False)
async def quote_payhere_notify(request: Request):
    form = await request.form()
    fields = {key: str(value) for key, value in form.items()}
    payment = await payments_col.find_one({"order_id": fields.get("order_id")}) if fields.get("order_id") else None
    if payment and payment.get("payment_type") != "project_quote_payment":
        from app.routers.payment_router import payhere_notification
        return await payhere_notification(request)
    required = ("merchant_id", "order_id", "payment_id", "payhere_amount", "payhere_currency", "status_code", "md5sig")
    if any(not fields.get(key) for key in required):
        raise HTTPException(400, "Incomplete PayHere notification")
    event_key = webhook_event_key(fields)
    stored_fields = {key: value[:500] for key, value in fields.items() if key not in {"authorization_token", "md5sig"}}
    try:
        await payment_webhooks_col.insert_one({
            "event_key": event_key, "provider": "payhere", "order_id": fields["order_id"],
            "fields": stored_fields, "signature_hash": __import__("hashlib").sha256(fields["md5sig"].encode()).hexdigest(),
            "verified": False, "processing_status": "received", "received_at": now_utc(),
        })
    except DuplicateKeyError:
        return Response(status_code=200)
    if not notification_is_valid(fields["merchant_id"], fields["order_id"], fields["payhere_amount"], fields["payhere_currency"], fields["status_code"], fields["md5sig"]):
        await payment_webhooks_col.update_one({"event_key": event_key}, {"$set": {"processing_status": "rejected", "rejection_reason": "invalid_signature"}})
        raise HTTPException(400, "Invalid PayHere signature")
    await payment_webhooks_col.update_one({"event_key": event_key}, {"$set": {"verified": True, "verified_at": now_utc()}})
    if not payment:
        await payment_webhooks_col.update_one({"event_key": event_key}, {"$set": {"processing_status": "rejected", "rejection_reason": "unknown_order"}})
        raise HTTPException(404, "Unknown PayHere order")
    if fields["merchant_id"] != payment["merchant_id"] or fields["payhere_currency"].upper() != "LKR" or amount_to_minor(fields["payhere_amount"]) != payment["expected_amount_minor"]:
        await payment_webhooks_col.update_one({"event_key": event_key}, {"$set": {"processing_status": "rejected", "rejection_reason": "payment_context_mismatch"}})
        raise HTTPException(400, "Payment context mismatch")
    if fields.get("custom_1") != payment["quote_id"] or fields.get("custom_2") != payment["project_id"]:
        await payment_webhooks_col.update_one({"event_key": event_key}, {"$set": {"processing_status": "rejected", "rejection_reason": "payment_ownership_mismatch"}})
        raise HTTPException(400, "Payment ownership mismatch")
    status = STATUS_MAP.get(fields["status_code"])
    if not status:
        return Response(status_code=200)
    if payment.get("status") == "SUCCESS" and status != "CHARGEDBACK":
        await payment_webhooks_col.update_one({"event_key": event_key}, {"$set": {"processing_status": "duplicate_state", "processed_at": now_utc()}})
        return Response(status_code=200)
    now = now_utc()
    update = {"status": status, "provider_status": status, "callback_verified": True, "callback_received_at": now,
              "payhere_payment_id": fields["payment_id"], "payment_method": fields.get("method"), "updated_at": now}
    try:
        changed = await payments_col.find_one_and_update(
            {"_id": payment["_id"], "status": {"$ne": status}}, {"$set": update}, return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        raise HTTPException(409, "Duplicate PayHere payment reference")
    if not changed:
        await payment_webhooks_col.update_one({"event_key": event_key}, {"$set": {"processing_status": "duplicate_state", "processed_at": now}})
        return Response(status_code=200)
    if status == "SUCCESS":
        await project_quotes_col.update_one({"_id": oid(payment["quote_id"]), "status": "PAYMENT_PENDING"}, {"$set": {"status": "PAID", "paid_at": now, "updated_at": now}})
        await requests_col.update_one({"_id": oid(payment["project_id"])}, {"$set": {"paid": True, "payment_status": "SUCCESS", "updated_at": now}})
        await _insert_financial_entries(payment, "PAYMENT_SUCCESS")
        payout = {
            "payment_id": str(payment["_id"]), "order_id": payment["order_id"], "editor_id": payment["editor_id"],
            "month": now.astimezone(ZoneInfo("Asia/Colombo")).strftime("%Y-%m"), "currency": "LKR",
            "gross_amount_minor": payment["project_amount_minor"], "editor_commission_minor": payment["editor_commission_minor"],
            "net_amount_minor": payment["editor_net_payable_minor"], "adjustments_minor": 0,
            "payout_status": "CALCULATING", "payout_eligible": False,
            "eligibility_reason": "Awaiting completed/released project", "manual_payout": True,
            "created_at": now, "updated_at": now, "audit_history": [],
        }
        try:
            await editor_payouts_col.insert_one(payout)
        except DuplicateKeyError:
            pass
        notices = ((payment["client_id"], "Payment verified successfully."), (payment["editor_id"], "Client payment received. Your net earning has been recorded."))
        for user_id, message in notices:
            await notifications_col.insert_one({"user_id": user_id, "title": "Project payment", "body": message, "request_id": payment["project_id"], "is_read": False, "created_at": now})
            await sio.emit("notification", {"title": "Project payment", "body": message}, room=str(user_id))
    elif status == "CHARGEDBACK" and payment.get("status") == "SUCCESS":
        await _insert_financial_entries(payment, "CHARGEBACK", -1)
        await editor_payouts_col.update_one({"payment_id": str(payment["_id"])}, {"$set": {"payout_status": "ADJUSTED", "adjustments_minor": -payment["editor_net_payable_minor"], "manual_review_required": True, "updated_at": now}})
    elif status in RETRYABLE_PAYMENT:
        quote = await project_quotes_col.find_one({"_id": oid(payment["quote_id"])})
        retry_status = "EXPIRED" if quote and quote.get("expires_at") and quote["expires_at"] <= now else "SENT"
        await project_quotes_col.update_one({"_id": oid(payment["quote_id"]), "status": "PAYMENT_PENDING"}, {"$set": {"status": retry_status, "updated_at": now}})
    await payment_webhooks_col.update_one({"event_key": event_key}, {"$set": {"processing_status": "processed", "processed_at": now, "resulting_status": status}})
    await sio.emit("payment_status_updated", {"project_id": payment["project_id"], "order_id": payment["order_id"], "payment_status": status}, room=f"chat_{payment['project_id']}")
    await sio.emit("project_payment_updated", {"project_id": payment["project_id"], "order_id": payment["order_id"], "payment_status": status}, room=f"chat_{payment['project_id']}")
    return Response(status_code=200)
