import uuid
import logging
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.config import settings
from app.core.security import get_current_user, require_editor, require_user
from app.core.utils import now_utc, oid, serialize_doc, serialize_list
from app.db.mongodb import disputes_col, deliveries_col, editor_payouts_col, messages_col, notifications_col, payment_escrows_col, payment_webhooks_col, payments_col, requests_col, users_col
from app.schemas.schemas import CreateChatPaymentBody, CreatePaymentBody, RefundPaymentBody
from app.services.financial_records import append_ledger, webhook_event_key
from app.services.payhere_service import (
    PayHereAPIError,
    amount_to_minor,
    capture_authorization,
    checkout_hash,
    format_amount,
    notification_is_valid,
    refund_payment,
    split_amount,
    split_project_amount,
)
from app.sockets.socket_manager import sio
from app.core.project_lifecycle import transition_project
from app.core.proposals import payment_eligibility

router = APIRouter(prefix="/api/v1/payments", tags=["Payment Protection"])

PENDING = "PENDING"
AUTHORIZED = "AUTHORIZED"
CAPTURED = "CAPTURED"
CANCELLED = "CANCELLED"
FAILED = "FAILED"
REFUNDED = "REFUNDED"
CHARGEBACK = "CHARGEBACK"
logger = logging.getLogger(__name__)


def _log_payment_eligibility(project_id: str, eligibility: dict) -> None:
    logger.info(
        "Payment eligibility: project_id=%s proposal_id=%s revision=%s client_accepted=%s editor_accepted=%s amount=%s delivery_days=%s payment_allowed=%s",
        project_id, eligibility.get("proposal_id"), eligibility.get("revision"),
        eligibility.get("client_accepted"), eligibility.get("editor_accepted"),
        eligibility.get("amount"), eligibility.get("delivery_days"), eligibility.get("payment_allowed"),
    )


@router.get("/eligibility/{request_id}")
async def get_payment_eligibility(request_id: str, current_user: dict = Depends(get_current_user)):
    req_doc = await requests_col.find_one({"_id": oid(request_id)})
    if not req_doc:
        raise HTTPException(status_code=404, detail="Request not found")
    if current_user["_id"] not in (req_doc.get("user_id"), req_doc.get("editor_user_id")) and current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not authorized to view payment eligibility")
    eligibility = payment_eligibility(req_doc)
    _log_payment_eligibility(request_id, eligibility)
    return eligibility


def _masked(value: str | None) -> str:
    value = str(value or "")
    return f"***{value[-4:]}" if value else "-"


def _with_order_id(url: str, order_id: str) -> str:
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}order_id={order_id}"


def _public_payment(doc: dict) -> dict:
    safe = dict(doc)
    safe.pop("authorization_token", None)
    safe.pop("capture_in_progress", None)
    safe.pop("refund_in_progress", None)
    if safe.get("payment_type") == "project_payment" and not safe.get("protection_status"):
        safe["protection_status"] = {"AUTHORIZED": "PROTECTED", "CAPTURED": "RELEASED", "REFUNDED": "REFUNDED", "CANCELLED": "FAILED"}.get(safe.get("status"), safe.get("status"))
    return serialize_doc(safe)


def _require_payhere_configured(*, merchant_api: bool = False):
    if settings.PAYHERE_MODE != "sandbox" or not settings.PAYHERE_SANDBOX:
        raise HTTPException(status_code=503, detail="Live PayHere mode is locked until sandbox testing is complete")
    if not settings.PAYHERE_MERCHANT_ID or not settings.PAYHERE_MERCHANT_SECRET:
        raise HTTPException(status_code=503, detail="PayHere Merchant ID and Secret are not configured")
    if merchant_api and (not settings.PAYHERE_APP_ID or not settings.PAYHERE_APP_SECRET):
        raise HTTPException(status_code=503, detail="PayHere Capture/Refund API credentials are not configured")


@router.get("/sandbox/capabilities")
async def sandbox_capabilities(current_user: dict = Depends(get_current_user)):
    return {
        "provider": "PayHere Sandbox",
        "sandbox": settings.PAYHERE_SANDBOX,
        "cost": "Free",
        "real_payments": False,
        "api_integration": True,
        "project_demo_suitable": True,
        "tests": [
            "Payment success", "Payment failed", "Card authorization",
            "Signed callback", "Refund API", "Payment status update",
            "Client approval flow", "Admin payment monitoring",
        ],
    }


async def _cancel_stale_pending(payment: dict) -> bool:
    """Release a checkout abandoned for 30 minutes so the user can retry."""
    if not payment or payment.get("status") != PENDING:
        return False
    result = await payments_col.update_one(
        {
            "_id": payment["_id"],
            "status": PENDING,
            "created_at": {"$lt": now_utc() - timedelta(minutes=30)},
        },
        {
            "$set": {
                "status": CANCELLED,
                "protection_status": "FAILED",
                "gateway_status_message": "Checkout expired before confirmation",
                "cancelled_at": now_utc(),
                "updated_at": now_utc(),
            },
            "$unset": {"active_request_key": ""},
        },
    )
    return result.modified_count == 1


@router.post("/payhere/initiate", status_code=201)
async def create_payment(body: CreatePaymentBody, current_user: dict = Depends(require_user)):
    """Create a signed PayHere Sandbox checkout from the accepted proposal."""
    req_doc = await requests_col.find_one({"_id": oid(body.request_id)})
    if not req_doc:
        raise HTTPException(status_code=404, detail="Request not found")
    if req_doc["user_id"] != current_user["_id"]:
        raise HTTPException(status_code=403, detail="Only the project owner can create this payment")
    if req_doc["status"] not in ("accepted", "payment_failed", "in_progress", "admin_review", "delivered") or req_doc.get("paid"):
        raise HTTPException(status_code=409, detail="This project is not awaiting payment authorization")
    eligibility = payment_eligibility(req_doc)
    _log_payment_eligibility(body.request_id, eligibility)
    if not eligibility["payment_allowed"]:
        raise HTTPException(status_code=409, detail=eligibility["message"])
    _require_payhere_configured()
    if not current_user.get("phone"):
        raise HTTPException(status_code=422, detail="Add a phone number to your profile before payment")
    existing = await payments_col.find_one({
        "request_id": body.request_id,
        "payment_type": "project_payment",
        "status": {"$in": [PENDING, AUTHORIZED, CAPTURED]},
    })
    if await _cancel_stale_pending(existing):
        existing = None
    if existing:
        raise HTTPException(status_code=409, detail="A payment for this project is already pending, authorized, or captured")

    project_amount = format_amount(eligibility["amount"])
    delivery_date = (date.today() + timedelta(days=int(eligibility["delivery_days"]))).isoformat()
    authorized_total = project_amount
    currency = settings.PAYHERE_CURRENCY.upper()
    order_id = f"EZ-{uuid.uuid4().hex.upper()}"
    gross_minor, platform_minor, service_minor, admin_minor, editor_minor = split_project_amount(project_amount)
    platform_commission = platform_minor / 100
    service_fee = service_minor / 100
    admin_total = admin_minor / 100
    editor_earning = editor_minor / 100
    editor = await users_col.find_one({"_id": req_doc["editor_user_id"]})

    doc = {
        "order_id": order_id,
        "active_request_key": body.request_id,
        "request_id": body.request_id,
        "delivery_id": body.delivery_id,
        "proposal_id": eligibility["proposal_id"],
        "proposal_revision": eligibility["revision"],
        "proposal_delivery_days": eligibility["delivery_days"],
        "payment_type": "project_payment",
        "user_id": current_user["_id"],
        "editor_user_id": req_doc["editor_user_id"],
        "project_name": req_doc["project_title"],
        "project_description": req_doc["project_description"],
        "editor_name": editor.get("username", "Editor") if editor else "Editor",
        "delivery_date": delivery_date,
        "order_date": date.today().isoformat(),
        "payment_method": "payhere_checkout",
        "amount": float(project_amount),
        "amount_minor": gross_minor,
        "currency": currency,
        "authorized_amount": float(authorized_total),
        "captured_amount": 0.0,
        "commission_percent": settings.PROJECT_PLATFORM_COMMISSION_PERCENT,
        "service_fee_percent": settings.PROJECT_SERVICE_FEE_PERCENT,
        "platform_fee_amount": admin_total,
        "platform_fee_minor": admin_minor,
        "commission_amount": platform_commission,
        "platform_commission": platform_commission,
        "platform_commission_minor": platform_minor,
        "service_fee": service_fee,
        "service_fee_minor": service_minor,
        "admin_total": admin_total,
        "admin_total_minor": admin_minor,
        "gross_amount": float(project_amount),
        "gross_amount_minor": gross_minor,
        "editor_net": editor_earning,
        "editor_net_minor": editor_minor,
        "editor_earning_amount": editor_earning,
        "editor_earning_minor": editor_minor,
        "editor_payout_amount": editor_earning,
        "editor_earning_status": "ON_HOLD",
        "editor_payout_status": "NOT_READY",
        "payment_received": False,
        "commission_calculated": False,
        "status": PENDING,
        "provider_status": "PENDING",
        "escrow_status": "AWAITING_AUTHORIZATION",
        "settlement_status": "NOT_DUE",
        "protection_status": "PENDING",
        "payment_protection": "HOLD_UNTIL_APPROVAL",
        "created_at": now_utc(),
        "updated_at": now_utc(),
    }
    try:
        await payments_col.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="A duplicate payment attempt was blocked")
    await append_ledger(doc, "created", amount=doc["authorized_amount"])

    customer_name = (current_user.get("username") or "EditZone Customer").strip().split(maxsplit=1)
    fields = {
        "merchant_id": settings.PAYHERE_MERCHANT_ID,
        "return_url": _with_order_id(settings.PAYHERE_RETURN_URL or f"{settings.FRONTEND_URL.rstrip('/')}/payment/success", order_id),
        "cancel_url": _with_order_id(settings.PAYHERE_CANCEL_URL or f"{settings.FRONTEND_URL.rstrip('/')}/payment/cancel", order_id),
        "notify_url": settings.PAYHERE_NOTIFY_URL,
        "order_id": order_id,
        "items": req_doc["project_title"][:120],
        "amount": authorized_total,
        "currency": currency,
        "hash": checkout_hash(order_id, authorized_total, currency),
        "first_name": customer_name[0],
        "last_name": customer_name[1] if len(customer_name) > 1 else "-",
        "email": current_user["email"],
        "phone": current_user["phone"],
        "address": str(current_user.get("address") or current_user.get("district") or "Sri Lanka").strip(),
        "city": str(current_user.get("city") or current_user.get("district") or "Colombo").strip(),
        "country": "Sri Lanka",
        "custom_1": body.request_id,
        "custom_2": body.delivery_id or body.request_id,
    }
    return {
        "order_id": order_id,
        "sandbox": True,
        "action_url": settings.PAYHERE_SANDBOX_URL,
        "fields": fields,
    }


@router.post("/payhere/project/initiate", status_code=201)
async def create_project_delivery_payment(body: CreatePaymentBody, current_user: dict = Depends(require_user)):
    if not body.delivery_id:
        raise HTTPException(status_code=422, detail="A final delivery identifier is required")
    delivery = await deliveries_col.find_one({
        "delivery_id": body.delivery_id, "project_id": body.request_id,
        "client_id": current_user["_id"],
    })
    if not delivery:
        raise HTTPException(status_code=404, detail="Final delivery not found")
    if delivery.get("delivery_status") not in {"READY_FOR_PAYMENT", "PAYMENT_FAILED"}:
        raise HTTPException(status_code=409, detail="Final output is not ready for payment")
    result = await create_payment(body, current_user)
    await deliveries_col.update_one(
        {"_id": delivery["_id"], "delivery_status": {"$in": ["READY_FOR_PAYMENT", "PAYMENT_FAILED"]}},
        {"$set": {"delivery_status": "PAYMENT_PENDING", "payment_status": "pending", "order_id": result["order_id"], "updated_at": now_utc()}},
    )
    return result


@router.post("/payhere/create", status_code=201)
async def create_chat_payment(
    body: CreateChatPaymentBody,
    current_user: dict = Depends(require_user),
):
    """Create checkout from chat without accepting an amount or billing data."""
    address = str(current_user.get("address") or current_user.get("district") or "Sri Lanka").strip()
    city = str(current_user.get("city") or current_user.get("district") or "Colombo").strip()
    return await create_payment(
        CreatePaymentBody(request_id=body.request_id, address=address, city=city),
        current_user,
    )




@router.post("/payhere/notify", include_in_schema=False)
async def payhere_notification(request: Request):
    """Verify PayHere's signed authorization notification."""
    _require_payhere_configured()
    form = await request.form()
    fields = {key: str(value) for key, value in form.items()}
    logger.info("PayHere callback received order=%s status=%s", _masked(fields.get("order_id")), fields.get("status_code", "-"))
    required = ("merchant_id", "order_id", "payhere_amount", "payhere_currency", "status_code", "md5sig")
    if any(not fields.get(key) for key in required):
        raise HTTPException(status_code=400, detail="Incomplete PayHere notification")
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
    if not notification_is_valid(
        fields["merchant_id"], fields["order_id"], fields["payhere_amount"],
        fields["payhere_currency"], fields["status_code"], fields["md5sig"],
    ):
        await payment_webhooks_col.update_one({"event_key": event_key}, {"$set": {"processing_status": "rejected", "rejection_reason": "invalid_signature"}})
        raise HTTPException(status_code=400, detail="Invalid PayHere signature")
    logger.info("PayHere callback verified order=%s", _masked(fields["order_id"]))
    await payment_webhooks_col.update_one({"event_key": event_key}, {"$set": {"verified": True, "verified_at": now_utc()}})

    payment = await payments_col.find_one({"order_id": fields["order_id"]})
    if not payment:
        await payment_webhooks_col.update_one({"event_key": event_key}, {"$set": {"processing_status": "rejected", "rejection_reason": "unknown_order"}})
        raise HTTPException(status_code=404, detail="Unknown PayHere order")
    if payment.get("payment_type") in {"monthly_hire_subscription", "editor_client_subscription"}:
        # Historical rows remain available for audit, but the retired billing
        # product can no longer change account access or payment state.
        await payment_webhooks_col.update_one({"event_key": event_key}, {"$set": {
            "processing_status": "legacy_product_ignored", "processed_at": now_utc(),
        }})
        return Response(status_code=200)
    try:
        amount_matches = int(payment.get("amount_minor", amount_to_minor(payment["authorized_amount"]))) == amount_to_minor(fields["payhere_amount"])
    except ValueError:
        amount_matches = False
    custom_matches = not payment.get("request_id") or (
        fields.get("custom_1") == payment["request_id"]
        and fields.get("custom_2") == (payment.get("delivery_id") or payment["request_id"])
    )
    if not amount_matches or payment["currency"] != fields["payhere_currency"].upper() or not custom_matches:
        await payment_webhooks_col.update_one({"event_key": event_key}, {"$set": {"processing_status": "rejected", "rejection_reason": "payment_context_mismatch"}})
        raise HTTPException(status_code=400, detail="Payment amount or currency mismatch")
    project = None
    if payment.get("request_id"):
        project = await requests_col.find_one({"_id": oid(payment["request_id"])})
        if not project or project.get("user_id") != payment.get("user_id") or project.get("editor_user_id") != payment.get("editor_user_id"):
            await payment_webhooks_col.update_one({"event_key": event_key}, {"$set": {"processing_status": "rejected", "rejection_reason": "payment_ownership_mismatch"}})
            raise HTTPException(status_code=400, detail="Payment ownership mismatch")
    if payment["status"] in (REFUNDED, CHARGEBACK) or (
        payment["status"] in (AUTHORIZED, CAPTURED) and fields["status_code"] != "-3"
    ):
        await payment_webhooks_col.update_one({"event_key": event_key}, {"$set": {"processing_status": "duplicate_state"}})
        return Response(status_code=200)

    # PayHere may send an intermediate pending notification before its terminal
    # notification. Keeping the local order pending allows the later signed
    # success/authorization callback to complete it.
    if fields["status_code"] == "0":
        await payments_col.update_one(
            {"_id": payment["_id"], "status": PENDING},
            {"$set": {
                "gateway_status_code": "0",
                "gateway_status_message": fields.get("status_message", "")[:500],
                "updated_at": now_utc(),
            }},
        )
        await payment_webhooks_col.update_one({"event_key": event_key}, {"$set": {"processing_status": "pending", "processed_at": now_utc()}})
        return Response(status_code=200)

    if fields["status_code"] == "2":
        if not fields.get("payment_id"):
            raise HTTPException(status_code=400, detail="PayHere payment reference is missing")
        new_status = CAPTURED
        update = {
            "$set": {
                "status": CAPTURED,
                "provider_status": "CAPTURED",
                "captured_amount": float(format_amount(fields["payhere_amount"])),
                "payment_id": fields.get("payment_id", ""),
                "payment_received": True,
                "signature_verified": True,
                "gateway_status_code": fields["status_code"],
                "gateway_status_message": fields.get("status_message", "")[:500],
                "gateway_method": fields.get("method"),
                "card_last4": fields.get("card_no", "")[-4:] or None,
                "captured_at": now_utc(),
                "paid_at": now_utc(),
                "updated_at": now_utc(),
            }
        }
        update["$set"].update({
            "escrow_status": "FUNDED",
            "protection_status": "PROTECTED",
            "settlement_status": "NOT_DUE",
        })
    elif fields["status_code"] in {"-1", "-2"}:
        new_status = CANCELLED if fields["status_code"] == "-1" else FAILED
        update = {
            "$set": {
                "status": new_status,
                "provider_status": "CANCELLED" if fields["status_code"] == "-1" else "FAILED",
                "escrow_status": "FAILED",
                "protection_status": "FAILED",
                "gateway_status_code": fields["status_code"],
                "gateway_status_message": fields.get("status_message", "")[:500],
                "cancelled_at": now_utc(),
                "updated_at": now_utc(),
            },
            "$unset": {"active_request_key": ""},
        }
    elif fields["status_code"] == "-3":
        new_status = CHARGEBACK
        update = {"$set": {
            "status": CHARGEBACK, "provider_status": "CHARGEBACK",
            "escrow_status": "FROZEN", "settlement_status": "BLOCKED",
            "manual_review_required": True, "gateway_status_code": "-3",
            "gateway_status_message": fields.get("status_message", "Chargeback")[:500],
            "updated_at": now_utc(),
        }}
    else:
        await payment_webhooks_col.update_one({"event_key": event_key}, {"$set": {"processing_status": "ignored", "rejection_reason": "unexpected_status_code"}})
        return Response(status_code=200)
    try:
        state_filter = {"$in": [PENDING, AUTHORIZED, CAPTURED]} if new_status == CHARGEBACK else PENDING
        result = await payments_col.update_one(
            {"_id": payment["_id"], "status": state_filter},
            update,
        )
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Duplicate PayHere payment reference")
    if result.modified_count and new_status in {AUTHORIZED, CAPTURED} and payment.get("request_id"):
        escrow_gross, escrow_fee, escrow_editor = split_amount(
            payment["authorized_amount"], payment.get("commission_percent", settings.PLATFORM_COMMISSION_PERCENT)
        )
        await payment_escrows_col.update_one(
            {"payment_id": str(payment["_id"])},
            {"$setOnInsert": {
                "payment_id": str(payment["_id"]), "order_id": payment["order_id"],
                "request_id": payment["request_id"], "payer_id": payment.get("user_id"),
                "editor_user_id": payment.get("editor_user_id"), "currency": payment["currency"],
                "gross_minor": int(payment.get("amount_minor", escrow_gross)),
                "platform_fee_minor": int(payment.get("platform_fee_minor", escrow_fee)),
                "editor_amount_minor": int(payment.get("editor_earning_minor", escrow_editor)),
                "status": "FUNDED", "settlement_status": "NOT_DUE",
                "funded_at": now_utc(), "created_at": now_utc(), "updated_at": now_utc(),
            }}, upsert=True,
        )
        await append_ledger(payment, "authorized", amount=payment["authorized_amount"], metadata={"webhook_event_key": event_key})
        if project and project["status"] in ("accepted", "payment_failed"):
            await transition_project(project, "in_progress", None, reason="Payment confirmed by gateway", extra={"paid": True, "payment_authorized": True, "payment_status": "PROTECTED", "authorized_at": now_utc()})
            notification_time = now_utc()
            await notifications_col.insert_many([
                {"user_id": project["user_id"], "title": "Payment Completed", "message": f"Payment protection is active for '{project['project_title']}'.", "request_id": payment["request_id"], "is_read": False, "created_at": notification_time},
                {"user_id": project["editor_user_id"], "title": "Payment Completed", "message": f"Payment protection is active for '{project['project_title']}'. You can begin work.", "request_id": payment["request_id"], "is_read": False, "created_at": notification_time},
            ])
            for recipient in (project["user_id"], project["editor_user_id"]):
                await sio.emit("notification", {"title": "Payment Completed", "request_id": payment["request_id"], "payment_status": new_status}, room=str(recipient))
        if new_status == CAPTURED and payment.get("delivery_id"):
            paid_at = now_utc()
            released = await deliveries_col.find_one_and_update(
                {"delivery_id": payment["delivery_id"], "project_id": payment["request_id"], "delivery_status": "PAYMENT_PENDING"},
                {"$set": {"delivery_status": "RELEASED", "upload_status": "ready_for_payment", "payment_status": "paid", "access_status": "released", "paid_at": paid_at, "released_at": paid_at, "updated_at": paid_at}},
                return_document=ReturnDocument.AFTER,
            )
            if released:
                await requests_col.update_one(
                    {"_id": oid(payment["request_id"]), "paid": {"$ne": True}},
                    {"$set": {"paid": True, "payment_status": "CAPTURED", "delivery_status": "RELEASED", "paid_at": paid_at, "updated_at": paid_at}},
                )
                system_text = "Payment verified. The final edited video is now unlocked."
                message = {"request_id": payment["request_id"], "sender_id": "system", "receiver_id": str(payment["user_id"]), "text": system_text, "message_type": "system", "delivery_id": payment["delivery_id"], "created_at": paid_at}
                inserted = await messages_col.insert_one(message)
                message["_id"] = inserted.inserted_id
                await sio.emit("new_message", serialize_doc(message), room=f"chat_{payment['request_id']}")
                await sio.emit("delivery_released", {"request_id": payment["request_id"], "delivery_id": payment["delivery_id"], "payment_status": "CAPTURED"}, room=f"chat_{payment['request_id']}")
                for recipient in (payment.get("user_id"), payment.get("editor_user_id")):
                    if recipient:
                        await notifications_col.insert_one({"user_id": recipient, "title": "Payment Completed", "body": system_text, "request_id": payment["request_id"], "is_read": False, "created_at": paid_at})
    elif result.modified_count and new_status in {CANCELLED, FAILED} and payment.get("request_id"):
        await append_ledger(payment, "cancelled", metadata={"webhook_event_key": event_key})
        project = await requests_col.find_one({"_id": oid(payment["request_id"])})
        if project and project["status"] == "accepted":
            await transition_project(project, "payment_failed", None, reason=fields.get("status_message", "Payment authorization failed")[:500], extra={"payment_status": "FAILED"})
        if payment.get("delivery_id"):
            await deliveries_col.update_one({"delivery_id": payment["delivery_id"], "delivery_status": "PAYMENT_PENDING"}, {"$set": {"delivery_status": "PAYMENT_FAILED", "payment_status": "failed", "access_status": "locked", "updated_at": now_utc()}})
    elif result.modified_count and new_status == CHARGEBACK:
        await payment_escrows_col.update_one({"payment_id": str(payment["_id"])}, {"$set": {"status": "FROZEN", "settlement_status": "BLOCKED", "updated_at": now_utc()}})
        await append_ledger(payment, "chargeback", amount=payment.get("authorized_amount", 0), metadata={"webhook_event_key": event_key})
    if result.modified_count and payment.get("request_id"):
        await sio.emit(
            "payment_status_updated",
            {"request_id": payment["request_id"], "payment_status": new_status},
            room=f"chat_{payment['request_id']}",
        )
    await payment_webhooks_col.update_one({"event_key": event_key}, {"$set": {"processing_status": "processed", "processed_at": now_utc()}})
    return Response(status_code=200)


@router.post("/{request_id}/approve")
async def approve_work_and_capture(request_id: str, current_user: dict = Depends(require_user)):
    """Client approval releases a previously protected authorization after video verification."""
    _require_payhere_configured(merchant_api=True)
    req_doc = await requests_col.find_one({"_id": oid(request_id)})
    if not req_doc:
        raise HTTPException(status_code=404, detail="Request not found")
    if req_doc["user_id"] != current_user["_id"]:
        raise HTTPException(status_code=403, detail="Only the project owner can approve work")
    if req_doc["status"] != "delivered" or not req_doc.get("delivered_file_url"):
        raise HTTPException(status_code=409, detail="Final work has not been delivered")
    if await disputes_col.find_one({"request_id": request_id, "status": {"$in": ["open", "under_review"]}}):
        raise HTTPException(status_code=409, detail="Payment cannot be released while a dispute is open")

    stale_before = now_utc() - timedelta(minutes=5)
    payment = await payments_col.find_one_and_update(
        {
            "request_id": request_id,
            "status": AUTHORIZED,
            "$or": [
                {"capture_in_progress": {"$ne": True}},
                {"capture_started_at": {"$lt": stale_before}},
            ],
        },
        {"$set": {
            "capture_in_progress": True,
            "capture_started_at": now_utc(),
            "approved_by": current_user["_id"],
        }},
        return_document=ReturnDocument.AFTER,
    )
    if not payment:
        existing = await payments_col.find_one({"request_id": request_id, "payment_type": "project_payment"})
        if existing and existing.get("status") == CAPTURED:
            return {"message": "Work was already approved and payment captured", "payment": _public_payment(existing)}
        raise HTTPException(status_code=409, detail="Payment is not authorized or capture is already running")

    escrow = await payment_escrows_col.find_one_and_update(
        {"payment_id": str(payment["_id"]), "status": "FUNDED"},
        {"$set": {"status": "RELEASE_PENDING", "updated_at": now_utc()}},
        return_document=ReturnDocument.AFTER,
    )
    if not escrow:
        await payments_col.update_one({"_id": payment["_id"]}, {"$unset": {"capture_in_progress": "", "capture_started_at": ""}})
        raise HTTPException(status_code=409, detail="Escrow is not funded or another operation is running")

    try:
        capture = await capture_authorization(
            payment["authorization_token"],
            format_amount(payment["authorized_amount"]),
        )
    except PayHereAPIError as exc:
        await payments_col.update_one(
            {"_id": payment["_id"], "status": AUTHORIZED},
            {"$unset": {"capture_in_progress": "", "capture_started_at": ""}},
        )
        await payment_escrows_col.update_one(
            {"payment_id": str(payment["_id"]), "status": "RELEASE_PENDING"},
            {"$set": {"status": "FUNDED", "updated_at": now_utc()}},
        )
        raise HTTPException(status_code=502, detail=str(exc))

    updated = await payments_col.find_one_and_update(
        {"_id": payment["_id"], "status": AUTHORIZED, "capture_in_progress": True},
        {
            "$set": {
                "status": CAPTURED,
                "protection_status": "RELEASED",
                "captured_amount": float(format_amount(capture.get("captured_amount", payment["authorized_amount"]))),
                "payment_id": str(capture.get("payment_id", "")),
                "editor_earning_status": "AVAILABLE",
                "captured_at": now_utc(),
                "updated_at": now_utc(),
            },
            "$unset": {
                "capture_in_progress": "",
                "capture_started_at": "",
                "authorization_token": "",
            },
        },
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise HTTPException(status_code=409, detail="Capture result could not be committed")
    await append_ledger(updated, "captured", amount=updated["captured_amount"])
    await payment_escrows_col.update_one(
        {"payment_id": str(payment["_id"]), "status": "RELEASE_PENDING"},
        {"$set": {"status": "RELEASED", "settlement_status": "PAYABLE", "released_at": now_utc(), "updated_at": now_utc()}},
    )

    completed_at = now_utc()
    await transition_project(req_doc, "completed", current_user, reason="Client approved the delivered work", extra={
        "paid": True, "payment_status": "RELEASED", "work_approved": True,
        "work_approved_at": completed_at, "completed_at": completed_at,
        "chat_closed_at": completed_at, "media_access_revoked_at": completed_at,
        "chat_cleanup_status": "queued",
    })
    await sio.emit(
        "project_completed",
        {
            "request_id": request_id,
            "status": "completed",
            "payment_status": CAPTURED,
            "message": "Payment completed successfully. This conversation is now closed.",
        },
        room=f"chat_{request_id}",
    )
    return {"message": "Work approved and sandbox payment captured", "payment": _public_payment(updated)}


@router.post("/{request_id}/refund")
async def refund(request_id: str, body: RefundPaymentBody, current_user: dict = Depends(get_current_user)):
    """Refund a hold or captured payment once; owner or admin only."""
    _require_payhere_configured(merchant_api=True)
    payment = await payments_col.find_one({"request_id": request_id, "payment_type": "project_payment"})
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    if current_user["role"] != "admin" and payment["user_id"] != current_user["_id"]:
        raise HTTPException(status_code=403, detail="Not authorized to refund this payment")
    if payment["status"] == REFUNDED:
        return {"message": "Payment was already refunded", "payment": _public_payment(payment)}
    if payment["status"] not in (AUTHORIZED, CAPTURED):
        raise HTTPException(status_code=409, detail="Only authorized or captured payments can be refunded")
    project = await requests_col.find_one({"_id": oid(request_id)})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if current_user["role"] != "admin" and project["status"] not in ("cancel_requested", "disputed", "refund_pending", "completed"):
        raise HTTPException(status_code=409, detail="Request cancellation or open a dispute before requesting a refund")
    if project["status"] != "refund_pending":
        project = await transition_project(project, "refund_pending", current_user, reason=body.reason)

    locked = await payments_col.find_one_and_update(
        {"_id": payment["_id"], "status": payment["status"], "refund_in_progress": {"$ne": True}, "capture_in_progress": {"$ne": True}},
        {"$set": {"refund_in_progress": True, "refund_started_at": now_utc()}},
        return_document=ReturnDocument.AFTER,
    )
    if not locked:
        raise HTTPException(status_code=409, detail="A refund is already running")
    escrow = await payment_escrows_col.find_one_and_update(
        {"payment_id": str(payment["_id"]), "status": {"$in": ["FUNDED", "RELEASED"]}},
        {"$set": {"status": "REFUND_PENDING", "settlement_status": "BLOCKED", "updated_at": now_utc()}},
        return_document=ReturnDocument.AFTER,
    )
    if not escrow:
        await payments_col.update_one({"_id": payment["_id"]}, {"$unset": {"refund_in_progress": "", "refund_started_at": ""}})
        raise HTTPException(status_code=409, detail="Escrow cannot be refunded while another operation is running")
    try:
        result = await refund_payment(
            reason=body.reason,
            payment_id=payment.get("payment_id") if payment["status"] == CAPTURED else None,
            authorization_token=payment.get("authorization_token") if payment["status"] == AUTHORIZED else None,
        )
    except PayHereAPIError as exc:
        await payments_col.update_one(
            {"_id": payment["_id"]},
            {"$unset": {"refund_in_progress": "", "refund_started_at": ""}},
        )
        await payment_escrows_col.update_one(
            {"payment_id": str(payment["_id"]), "status": "REFUND_PENDING"},
            {"$set": {"status": escrow.get("status_before_refund", "FUNDED" if payment["status"] == AUTHORIZED else "RELEASED"), "settlement_status": "NOT_DUE" if payment["status"] == AUTHORIZED else "PAYABLE", "updated_at": now_utc()}},
        )
        raise HTTPException(status_code=502, detail=str(exc))

    updated = await payments_col.find_one_and_update(
        {"_id": payment["_id"], "refund_in_progress": True},
        {
            "$set": {
                "status": REFUNDED,
                "protection_status": "REFUNDED",
                "editor_earning_status": "REFUNDED",
                "refund_id": str(result.get("data") or ""),
                "refund_reason": body.reason,
                "refunded_at": now_utc(),
                "updated_at": now_utc(),
            },
            "$unset": {
                "refund_in_progress": "",
                "refund_started_at": "",
                "authorization_token": "",
                "active_request_key": "",
            },
        },
        return_document=ReturnDocument.AFTER,
    )
    await append_ledger(updated, "refunded", amount=updated.get("captured_amount") or updated.get("authorized_amount", 0), metadata={"reason": body.reason})
    await payment_escrows_col.update_one(
        {"payment_id": str(payment["_id"])},
        {"$set": {"status": "REFUNDED", "settlement_status": "NOT_DUE", "refunded_at": now_utc(), "updated_at": now_utc()}},
    )
    await transition_project(project, "refunded", current_user, reason=body.reason, extra={"paid": False, "payment_status": "REFUNDED"})
    return {"message": "Sandbox refund processed", "payment": _public_payment(updated)}


@router.get("/status/{request_id}")
async def payment_status(request_id: str, current_user: dict = Depends(get_current_user)):
    payment = await payments_col.find_one(
        {"request_id": request_id, "payment_type": "project_payment"},
        sort=[("created_at", -1)],
    )
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    if current_user["role"] != "admin" and current_user["_id"] not in (
        payment["user_id"], payment["editor_user_id"]
    ):
        raise HTTPException(status_code=403, detail="Not authorized to view this payment")
    return _public_payment(payment)


@router.get("/payhere/status/{order_id}")
async def payhere_status(order_id: str, current_user: dict = Depends(get_current_user)):
    payment = await payments_col.find_one({"order_id": order_id})
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    if current_user.get("role") != "admin" and current_user["_id"] not in (
        payment.get("user_id"), payment.get("editor_user_id")
    ):
        raise HTTPException(status_code=403, detail="Not authorized to view this payment")
    return _public_payment(payment)




@router.get("/{order_id}/status")
async def payment_order_status(order_id: str, current_user: dict = Depends(get_current_user)):
    payment = await payments_col.find_one({"order_id": order_id})
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    if current_user["role"] != "admin" and current_user["_id"] not in (
        payment.get("user_id"), payment.get("editor_user_id")
    ):
        raise HTTPException(status_code=403, detail="Not authorized to view this payment")
    project = await requests_col.find_one({"_id": oid(payment["request_id"])}, {"status": 1}) if payment.get("request_id") else None
    return {
        "order_id": payment["order_id"],
        "project_id": payment.get("request_id"),
        "amount": payment.get("amount"),
        "currency": payment.get("currency"),
        "status": payment.get("status"),
        "paid_at": payment.get("captured_at") or payment.get("authorized_at"),
        "conversation_status": project.get("status") if project else None,
    }


@router.get("/mine")
async def my_payments(current_user: dict = Depends(get_current_user)):
    query = (
        {"editor_user_id": current_user["_id"], "payment_type": "project_payment"}
        if current_user["role"] == "editor"
        else {"user_id": current_user["_id"]}
    )
    docs = await payments_col.find(query).sort("created_at", -1).to_list(200)
    return {"payments": [_public_payment(doc) for doc in docs]}


@router.get("/earnings/mine")
async def editor_earnings(month: str | None = None, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != "editor":
        raise HTTPException(status_code=403, detail="Editor account required")
    if month and (len(month) != 7 or month[4] != "-"):
        raise HTTPException(status_code=422, detail="Month must use YYYY-MM")
    quote_docs = await payments_col.find({
        "editor_id": current_user["_id"], "payment_type": "project_quote_payment", "status": {"$in": ["SUCCESS", "CHARGEDBACK"]},
    }).sort("created_at", -1).to_list(500)
    if month:
        from zoneinfo import ZoneInfo
        quote_docs = [p for p in quote_docs if (p.get("callback_received_at") or p.get("created_at")).astimezone(ZoneInfo("Asia/Colombo")).strftime("%Y-%m") == month]
    if quote_docs:
        payouts = await editor_payouts_col.find({"editor_id": current_user["_id"], **({"month": month} if month else {})}).to_list(500)
        eligible_payment_ids = {
            p.get("payment_id") for p in payouts
            if p.get("payout_eligible", p.get("payout_status") != "CALCULATING")
        }
        successful = [p for p in quote_docs if p["status"] == "SUCCESS" and str(p["_id"]) in eligible_payment_ids]
        reversed_docs = [p for p in quote_docs if p["status"] == "CHARGEDBACK"]
        gross_minor = sum(p["project_amount_minor"] for p in successful)
        commission_minor = sum(p["editor_commission_minor"] for p in successful)
        reversed_minor = sum(p["editor_net_payable_minor"] for p in reversed_docs)
        net_minor = sum(p["editor_net_payable_minor"] for p in successful) - reversed_minor
        paid_minor = sum(p.get("net_amount_minor", 0) + p.get("adjustments_minor", 0) for p in payouts if p.get("payout_status") == "PAID")
        return {
            "currency": "LKR", "month": month, "gross_earnings": gross_minor / 100,
            "commission": commission_minor / 100, "net_payout": net_minor / 100,
            "paid_projects": len(successful), "pending_payout": max(net_minor - paid_minor, 0) / 100,
            "paid_payout": paid_minor / 100, "reversed_earnings": reversed_minor / 100,
            "transactions": [_public_payment(p) for p in quote_docs], "payouts": serialize_list(payouts),
            "total_earnings": net_minor / 100, "pending_earnings": max(net_minor - paid_minor, 0) / 100,
            "available": max(net_minor - paid_minor, 0) / 100, "earnings": [_public_payment(p) for p in quote_docs],
        }
    docs = await payments_col.find({
        "editor_user_id": current_user["_id"],
        "payment_type": "project_payment",
        "status": {"$in": [AUTHORIZED, CAPTURED, REFUNDED]},
    }).sort("created_at", -1).to_list(200)
    hold_cutoff = now_utc() - timedelta(days=settings.PAYOUT_HOLD_DAYS)
    matured = sum(p.get("editor_earning_amount", 0) for p in docs if p["status"] == CAPTURED and p.get("captured_at", now_utc()) <= hold_cutoff)
    pending_capture = sum(p.get("editor_earning_amount", 0) for p in docs if p["status"] == AUTHORIZED)
    pending_hold = sum(p.get("editor_earning_amount", 0) for p in docs if p["status"] == CAPTURED and p.get("captured_at", now_utc()) > hold_cutoff)
    available = max(matured, 0)
    on_hold = pending_capture + pending_hold
    total_earnings = sum(p.get("editor_earning_amount", 0) for p in docs if p["status"] == CAPTURED)
    return {
        "currency": settings.PLATFORM_CURRENCY,
        "available": round(available, 2),
        "on_hold": round(on_hold, 2),
        "total_earnings": round(total_earnings, 2),
        "pending_earnings": round(on_hold, 2),
        "earnings": [_public_payment(doc) for doc in docs],
    }


@router.get("/{request_id}")
async def get_payment_for_request(request_id: str, current_user: dict = Depends(get_current_user)):
    return await payment_status(request_id, current_user)
