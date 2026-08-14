from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Literal

from app.db.mongodb import (
    users_col, editors_col, requests_col, payments_col, reviews_col,
    messages_col, disputes_col, chat_reports_col, chat_audit_logs_col, content_col,
    deliveries_col, editor_payouts_col, notifications_col,
)
from app.schemas.schemas import BanUserBody, ApproveDeliveryBody, IdentityReviewDecision, AdminLifecycleBody
from app.core.project_lifecycle import transition_project
from app.core.security import require_admin
from app.core.utils import serialize_list, serialize_doc, oid, now_utc
from app.sockets.socket_manager import sio, disconnect_user
from app.config import settings
from app.services.identity_verification_service import (
    IdentityServiceError,
    create_private_review_url,
    mask_nic,
    write_audit,
)
from app.services.financial_records import reconcile_payments
from app.core.accounts import ACTIVE_ACCOUNT_FILTER, ACTIVE_EDITOR_FILTER
from app.db.mongodb import account_deletion_audit_logs_col
from app.services.admin_account_service import admin_delete_account, admin_restore_account
from app.services.project_settlement import capture_and_record_project_payment

router = APIRouter(prefix="/api/v1/admin", tags=["Admin"])


class ChatReportDecision(BaseModel):
    action: Literal["warn", "temporary_suspend", "permanent_block", "dismiss"]
    reason: str = Field(min_length=5, max_length=1000)
    suspension_days: int = Field(default=7, ge=1, le=365)


class AdminAccountAction(BaseModel):
    reason: str = Field(min_length=5, max_length=1000)
    confirmation: Literal["DELETE", "RESTORE"]


class PayoutRecordBody(BaseModel):
    status: Literal["APPROVED", "PROCESSING", "PAID", "FAILED", "ADJUSTED"]
    reference: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=1000)


def _account_filter(role: str, state: str) -> dict:
    query = {"role": role}
    if state == "active":
        query.update({"is_banned": {"$ne": True}, "is_deleted": {"$ne": True}, "status": {"$ne": "deleted"}})
    elif state == "suspended":
        query["is_banned"] = True
        query["status"] = {"$ne": "deleted"}
    elif state == "deleted":
        query["$or"] = [{"is_deleted": True}, {"status": "deleted"}]
    return query


async def _safe_identity_review_url(key: str):
    try:
        return await create_private_review_url(key)
    except IdentityServiceError:
        return None


@router.get("/users")
async def list_users(current_user: dict = Depends(require_admin), status: Literal["all", "active", "suspended", "deleted"] = "active"):
    docs = await users_col.find(_account_filter("user", status)).sort("created_at", -1).to_list(500)
    return {"users": serialize_list(docs)}


@router.get("/editors")
async def list_all_editors(current_user: dict = Depends(require_admin), status: Literal["all", "active", "suspended", "deleted"] = "active"):
    users = await users_col.find(_account_filter("editor", status)).sort("created_at", -1).to_list(500)
    user_ids = [user["_id"] for user in users]
    docs = await editors_col.find({"user_id": {"$in": user_ids}}).to_list(500)
    users_by_id = {user["_id"]: user for user in users}
    results = []
    for doc in docs:
        user = users_by_id.get(doc.get("user_id"))
        if not user:
            continue
        item = serialize_doc(doc)
        for sensitive_field in ("nic_front_key",):
            item.pop(sensitive_field, None)
        item.update({
            "account_id": str(user["_id"]),
            "username": user.get("username", "Unknown editor"),
            "email": user.get("email", ""),
            "is_banned": user.get("is_banned", False),
            "role": "editor", "status": user.get("status", "active"),
            "is_deleted": user.get("is_deleted", False), "is_active": user.get("is_active", True),
            "created_at": user.get("created_at"), "deleted_at": user.get("deleted_at"),
        })
        results.append(item)
    return {"editors": results}


@router.delete("/accounts/{account_id}")
async def delete_account_by_admin(account_id: str, body: AdminAccountAction, request: Request, current_user: dict = Depends(require_admin)):
    if body.confirmation != "DELETE":
        raise HTTPException(status_code=422, detail="Type DELETE to confirm account deletion")
    account = await users_col.find_one({"_id": oid(account_id)})
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    result = await admin_delete_account(account, current_user, reason=body.reason.strip(), ip_address=request.client.host if request.client else "unknown")
    await disconnect_user(account_id)
    return {"message": "Account deleted successfully", **result}


@router.patch("/accounts/{account_id}/restore")
async def restore_account_by_admin(account_id: str, body: AdminAccountAction, request: Request, current_user: dict = Depends(require_admin)):
    if body.confirmation != "RESTORE":
        raise HTTPException(status_code=422, detail="Type RESTORE to confirm account restoration")
    account = await users_col.find_one({"_id": oid(account_id)})
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    result = await admin_restore_account(account, current_user, reason=body.reason.strip(), ip_address=request.client.host if request.client else "unknown")
    return {"message": "Account restored. The account owner must log in again", **result}


@router.get("/editors/identity-review")
async def pending_identity_reviews(current_user: dict = Depends(require_admin)):
    docs = await editors_col.find(
        {"identity_verification_status": "manual_review", **ACTIVE_EDITOR_FILTER}
    ).sort("identity_updated_at", 1).to_list(200)
    user_ids = [doc["user_id"] for doc in docs]
    users = await users_col.find(
        {"_id": {"$in": user_ids}, "role": "editor", **ACTIVE_ACCOUNT_FILTER}, {"username": 1, "email": 1, "nic": 1}
    ).to_list(200)
    users_by_id = {user["_id"]: user for user in users}
    items = []
    for doc in docs:
        user = users_by_id.get(doc["user_id"], {})
        front_url = await _safe_identity_review_url(doc.get("nic_front_key", ""))
        selfie_url = await _safe_identity_review_url(doc.get("selfie_s3_key", ""))
        items.append({
            "editor_id": str(doc["_id"]),
            "username": user.get("username", "Unknown editor"),
            "email": user.get("email", ""),
            "nic": mask_nic(user.get("nic", "")),
            "reasons": doc.get("manual_review_reasons", []),
            "ocr_confidence": doc.get("nic_ocr_confidence"),
            "nic_front_review_url": front_url,
            "selfie_review_url": selfie_url,
            "face_match_score": doc.get("face_match_score"),
            "liveness_status": doc.get("liveness_status"),
            "selfie_verified_at": doc.get("selfie_verified_at"),
            "review_urls_expire_seconds": 300,
        })
        await write_audit(doc["user_id"], "identity_review_urls", "admin_access_granted", {"admin_id": str(current_user["_id"])})
    return {"items": items}


@router.get("/identity-access-logs")
async def identity_access_logs(current_user: dict = Depends(require_admin)):
    from app.db.mongodb import identity_audit_logs_col
    docs = await identity_audit_logs_col.find({"event": {"$in": ["identity_review_urls", "manual_review", "identity_retention_purge", "data_export", "account_deletion_request"]}}).sort("created_at", -1).limit(500).to_list(500)
    return {"items": serialize_list(docs)}


@router.patch("/editors/{editor_id}/identity-review")
async def review_editor_identity(
    editor_id: str,
    body: IdentityReviewDecision,
    current_user: dict = Depends(require_admin),
):
    editor = await editors_col.find_one({"_id": oid(editor_id)})
    if not editor:
        raise HTTPException(status_code=404, detail="Editor not found")
    if editor.get("identity_verification_status") != "manual_review":
        raise HTTPException(status_code=409, detail="Editor is not awaiting identity review")
    if body.decision == "approve" and not editor.get("nic_front_key"):
        raise HTTPException(
            status_code=409,
            detail="A securely stored NIC front image is required before approval",
        )
    has_selfie = bool(editor.get("selfie_s3_key"))
    status = ("selfie_verified" if has_selfie else "nic_verified") if body.decision == "approve" else "failed"
    await editors_col.update_one(
        {"_id": editor["_id"], "identity_verification_status": "manual_review"},
        {
            "$set": {
                "identity_verification_status": status,
                "identity_review_note": body.note,
                "identity_reviewed_at": now_utc(),
                "identity_reviewed_by": current_user["_id"],
                "nic_ocr_verified": body.decision == "approve" or bool(editor.get("nic_ocr_verified")),
                "selfie_verified": body.decision == "approve" and has_selfie,
            }
        },
    )
    await write_audit(
        editor["user_id"], "manual_review", status,
        {"reviewed_by": str(current_user["_id"])},
    )
    return {"message": f"Editor identity {status}", "status": status}


@router.patch("/users/{user_id}/ban")
async def ban_user(user_id: str, body: BanUserBody, current_user: dict = Depends(require_admin)):
    result = await users_col.update_one({"_id": oid(user_id)}, {"$set": {"is_banned": body.is_banned}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    return {"message": "User ban status updated", "is_banned": body.is_banned}


@router.get("/payments")
async def list_all_payments(current_user: dict = Depends(require_admin)):
    docs = await payments_col.find({}).sort("created_at", -1).to_list(500)
    for doc in docs:
        doc.pop("authorization_token", None)
        if doc.get("payment_type") == "project_payment" and not doc.get("protection_status"):
            doc["protection_status"] = {"AUTHORIZED": "PROTECTED", "CAPTURED": "RELEASED", "REFUNDED": "REFUNDED", "CANCELLED": "FAILED"}.get(doc.get("status"), doc.get("status"))
    return {"payments": serialize_list(docs)}


@router.post("/payments/reconcile")
async def reconcile_all_payments(current_user: dict = Depends(require_admin)):
    return await reconcile_payments()


@router.get("/projects")
async def monitor_projects(current_user: dict = Depends(require_admin)):
    docs = await requests_col.find({}).sort("created_at", -1).to_list(500)
    project_keys = [str(doc["_id"]) for doc in docs]
    deliveries = await deliveries_col.find({"project_id": {"$in": project_keys}}).sort("version", -1).to_list(1000)
    latest_delivery = {}
    for delivery in deliveries:
        latest_delivery.setdefault(delivery["project_id"], delivery)
    payment_docs = await payments_col.find({"request_id": {"$in": project_keys}, "payment_type": "project_payment"}).to_list(500)
    payment_by_project = {payment["request_id"]: payment for payment in payment_docs}
    user_ids = list({uid for doc in docs for uid in (doc.get("user_id"), doc.get("editor_user_id")) if uid})
    users = await users_col.find({"_id": {"$in": user_ids}}, {"username": 1, "email": 1}).to_list(1000)
    users_by_id = {user["_id"]: user for user in users}
    results = []
    for doc in docs:
        item = serialize_doc(doc)
        client = users_by_id.get(doc.get("user_id"), {})
        editor = users_by_id.get(doc.get("editor_user_id"), {})
        item.update({
            "client_name": "Deleted User" if doc.get("user_deleted") else client.get("username", "Unknown client"),
            "client_email": "" if doc.get("user_deleted") else client.get("email", ""),
            "editor_name": "Deleted User" if doc.get("editor_deleted") else editor.get("username", "Unknown editor"),
            "editor_email": "" if doc.get("editor_deleted") else editor.get("email", ""),
        })
        delivery = latest_delivery.get(str(doc["_id"]))
        payment = payment_by_project.get(str(doc["_id"]), {})
        if delivery:
            safe_delivery = serialize_doc(delivery)
            safe_delivery.pop("storage_key", None)
            safe_delivery["access_path"] = (
                f"/api/v1/uploads/s3/file/{delivery['upload_id']}"
                if delivery.get("storage_type") == "s3"
                else f"/api/v1/uploads/file/{delivery.get('storage_key', '')}"
            )
            item["delivery"] = safe_delivery
        item["payment_status"] = payment.get("status", "MISSING")
        item["payment"] = serialize_doc({key: value for key, value in payment.items() if key not in {"authorization_token", "capture_in_progress"}}) if payment else None
        results.append(item)
    return {"projects": results}


@router.get("/projects/pending-verification")
async def pending_verification(current_user: dict = Depends(require_admin)):
    """Videos uploaded by editors, awaiting admin approval before release + delivery."""
    deliveries = await deliveries_col.find({"delivery_status": "PENDING_ADMIN_REVIEW"}).sort("uploaded_at", 1).to_list(200)
    project_ids = [oid(item["project_id"]) for item in deliveries]
    projects = await requests_col.find({"_id": {"$in": project_ids}}).to_list(200)
    projects_by_id = {str(project["_id"]): project for project in projects}
    payments = await payments_col.find({"request_id": {"$in": [item["project_id"] for item in deliveries]}, "payment_type": "project_payment"}).to_list(200)
    payments_by_project = {item["request_id"]: item for item in payments}
    user_ids = list({uid for item in deliveries for uid in (item.get("client_id"), item.get("editor_id")) if uid})
    users = await users_col.find({"_id": {"$in": user_ids}}, {"username": 1}).to_list(400)
    users_by_id = {item["_id"]: item for item in users}
    items = []
    for delivery in deliveries:
        project = projects_by_id.get(delivery["project_id"], {})
        payment = payments_by_project.get(delivery["project_id"], {})
        item = serialize_doc(delivery)
        item.update({
            "project_title": project.get("project_title", "Unknown project"),
            "client_name": users_by_id.get(delivery.get("client_id"), {}).get("username", "Unknown client"),
            "editor_name": users_by_id.get(delivery.get("editor_id"), {}).get("username", "Unknown editor"),
            "amount": payment.get("authorized_amount", payment.get("amount", 0)),
            "currency": payment.get("currency", "LKR"),
            "payment_status": payment.get("status", "MISSING"),
        })
        item.pop("storage_key", None)
        items.append(item)
    return {"deliveries": items, "projects": items}


@router.patch("/projects/{request_id}/verify-delivery")
async def verify_delivery(request_id: str, body: ApproveDeliveryBody, current_user: dict = Depends(require_admin)):
    req_doc = await requests_col.find_one({"_id": oid(request_id)})
    if not req_doc:
        raise HTTPException(status_code=404, detail="Request not found")
    if req_doc.get("status") == "completed":
        released = await deliveries_col.find_one({"project_id": request_id, "delivery_status": "RELEASED"}, sort=[("version", -1)])
        if released:
            return {"message": "Final video was already released to the client", "delivery": serialize_doc(released)}
    if req_doc["status"] != "admin_review":
        raise HTTPException(status_code=400, detail="Project is not awaiting delivery verification")
    delivery = await deliveries_col.find_one({
        "project_id": request_id,
        "delivery_status": "PENDING_ADMIN_REVIEW",
    }, sort=[("version", -1)])
    if not delivery:
        raise HTTPException(status_code=409, detail="No final delivery is pending admin review")

    if not body.approve:
        reviewed_at = now_utc()
        await deliveries_col.update_one(
            {"_id": delivery["_id"], "delivery_status": "PENDING_ADMIN_REVIEW"},
            {"$set": {"delivery_status": "REVISION_REQUESTED", "admin_reviewed_at": reviewed_at, "reviewed_by": current_user["_id"], "admin_note": body.admin_note}},
        )
        await transition_project(req_doc, "revision_requested", current_user, reason=body.admin_note or "Delivery did not pass platform review", extra={"admin_note": body.admin_note, "revision_count": req_doc.get("revision_count", 0) + 1})
        await sio.emit("notification", {"title": "Video Rejected — Revision Required", "body": body.admin_note or "Admin requested changes to the final video."}, room=str(req_doc["editor_user_id"]))
        return {"message": "Delivery rejected, sent back to editor for revision"}

    payment = await capture_and_record_project_payment(request_id, current_user["_id"])
    released_at = now_utc()
    released = await deliveries_col.find_one_and_update(
        {"_id": delivery["_id"], "delivery_status": "PENDING_ADMIN_REVIEW"},
        {"$set": {
            "delivery_status": "RELEASED",
            "admin_reviewed_at": released_at,
            "reviewed_by": current_user["_id"],
            "released_at": released_at,
            "released_by": current_user["_id"],
            "admin_note": body.admin_note,
        }},
        return_document=True,
    )
    if not released:
        latest = await deliveries_col.find_one({"_id": delivery["_id"]})
        if latest and latest.get("delivery_status") == "RELEASED":
            return {"message": "Final video was already released to the client", "delivery": serialize_doc(latest)}
        raise HTTPException(status_code=409, detail="Delivery changed while release was processing")
    completed_project = await transition_project(req_doc, "completed", current_user, reason=body.admin_note or "Admin reviewed and released final delivery", extra={
        "admin_approved": True,
        "admin_verified_at": released_at,
        "delivery_status": "RELEASED",
        "released_at": released_at,
        "released_by": current_user["_id"],
        "paid": True,
        "payment_status": "CAPTURED",
        "work_approved": True,
        "work_approved_at": released_at,
        "completed_at": released_at,
    })
    await editor_payouts_col.update_one(
        {"payment_id": str(payment["_id"]), "payout_status": "CALCULATING"},
        {"$set": {"payout_status": "PENDING", "payout_eligible": True, "eligible_at": released_at, "updated_at": released_at}, "$unset": {"eligibility_reason": ""}},
    )
    notices = [
        (req_doc["user_id"], "Your Final Video Is Ready", "Your final video has been released."),
        (req_doc["editor_user_id"], "Delivery Approved", "Your project delivery was approved. Editor payout is pending."),
    ]
    for user_id, title, message in notices:
        await notifications_col.insert_one({"user_id": user_id, "title": title, "body": message, "request_id": request_id, "is_read": False, "created_at": released_at})
        await sio.emit("notification", {"title": title, "body": message, "request_id": request_id}, room=str(user_id))
    await sio.emit("delivery_released", {"request_id": request_id, "delivery_status": "RELEASED", "payment_status": "CAPTURED"}, room=f"chat_{request_id}")
    return {"message": "Final video released. Payment captured; editor payout is pending.", "project": serialize_doc(completed_project), "delivery": serialize_doc(released), "payment": serialize_doc(payment)}


@router.get("/editor-payouts")
@router.get("/editor-commissions")
async def list_editor_payouts(current_user: dict = Depends(require_admin)):
    docs = await editor_payouts_col.find({}).sort("created_at", -1).to_list(500)
    for doc in docs:
        editor_id = doc.get("editor_id") or doc.get("editor_user_id")
        editor = await users_col.find_one({"_id": editor_id}) if editor_id else None
        if editor:
            doc["editor_name"] = editor.get("username") or editor.get("email")
            # Summary only: never expose full account credentials in this list.
            account = str(editor.get("bank_account_number") or "")
            doc["payment_destination_summary"] = (
                f"{editor.get('bank_name', 'Bank transfer')} · ••••{account[-4:]}"
                if account else "Bank details not provided"
            )
    return {"payouts": serialize_list(docs)}


@router.patch("/editor-payouts/{payout_id}")
async def record_editor_payout(payout_id: str, body: PayoutRecordBody, current_user: dict = Depends(require_admin)):
    if body.status == "PAID" and not (body.reference or "").strip():
        raise HTTPException(status_code=422, detail="A bank/payment reference is required before marking a payout paid")
    payout = await editor_payouts_col.find_one({"_id": oid(payout_id)})
    if not payout:
        raise HTTPException(status_code=404, detail="Editor payout not found")
    if payout.get("payout_status") == "PAID":
        return {"message": "Editor payout was already recorded as paid", "payout": serialize_doc(payout)}
    if body.status == "PAID" and not payout.get("payout_eligible", payout.get("payout_status") != "CALCULATING"):
        raise HTTPException(status_code=409, detail="This payout is not eligible until the project is completed and released")
    now = now_utc()
    updated = await editor_payouts_col.find_one_and_update(
        {"_id": payout["_id"], "payout_status": {"$ne": "PAID"}},
        {"$set": {"payout_status": body.status, "payout_reference": body.reference, "payout_note": body.note, "updated_at": now, "recorded_by": current_user["_id"], **({"paid_at": now} if body.status == "PAID" else {})}, "$push": {"audit_history": {"status": body.status, "reference": body.reference, "note": body.note, "admin_id": current_user["_id"], "created_at": now}}},
        return_document=True,
    )
    await payments_col.update_one({"_id": oid(payout["payment_id"])}, {"$set": {"editor_payout_status": body.status, "settlement_status": "SETTLED" if body.status == "PAID" else "EDITOR_PAYOUT_PENDING", "updated_at": now}})
    return {"message": f"Editor payout status updated to {body.status}", "payout": serialize_doc(updated)}


@router.post("/editor-payouts/{payout_id}/mark-paid")
async def mark_editor_payout_paid(payout_id: str, body: PayoutRecordBody, current_user: dict = Depends(require_admin)):
    body.status = "PAID"
    return await record_editor_payout(payout_id, body, current_user)

@router.patch("/projects/{request_id}/lifecycle")
async def resolve_project_lifecycle(request_id: str, body: AdminLifecycleBody, current_user: dict = Depends(require_admin)):
    project = await requests_col.find_one({"_id": oid(request_id)})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if body.action == "refund":
        target = "refund_pending"
    elif body.action == "cancel":
        target = "cancelled"
    elif body.action == "request_revision":
        target = "revision_requested"
    elif body.action == "complete":
        target = "completed"
    else:
        target = project.get("pre_dispute_status") or ("overdue" if project.get("was_overdue") else "in_progress")
        if target not in ("in_progress", "overdue", "revision_requested", "admin_review"):
            target = "in_progress"
    updated = await transition_project(project, target, current_user, reason=body.reason, extra={"admin_resolution": body.action})
    if project["status"] == "disputed":
        await disputes_col.update_one(
            {"request_id": request_id, "status": "open"},
            {"$set": {"status": "resolved", "resolution": body.action, "resolution_reason": body.reason, "resolved_by": current_user["_id"], "resolved_at": now_utc()}},
        )
        await payments_col.update_one(
            {"request_id": request_id, "payment_type": "project_payment"},
            {"$set": {"dispute_status": "RESOLVED", "dispute_resolution": body.action, "dispute_resolved_at": now_utc()}},
        )
    return serialize_doc(updated)


@router.get("/dashboard-stats")
async def dashboard_stats(current_user: dict = Depends(require_admin)):
    total_users = await users_col.count_documents({"role": "user", **ACTIVE_ACCOUNT_FILTER})
    total_editors = await users_col.count_documents({"role": "editor", **ACTIVE_ACCOUNT_FILTER})
    total_projects = await requests_col.count_documents({})
    new_requests = await requests_col.count_documents({"status": "pending"})
    accepted_projects = await requests_col.count_documents({"status": "accepted"})
    active_projects = await requests_col.count_documents({"status": {"$in": ["in_progress", "overdue", "admin_review", "revision_requested", "delivered", "disputed", "cancel_requested"]}})
    completed_projects = await requests_col.count_documents({"status": "completed"})
    pending_verification_count = await requests_col.count_documents({"status": "admin_review", "admin_approved": False})

    payments = await payments_col.find({"status": "CAPTURED"}).to_list(2000)
    total_revenue = sum(float(p.get("amount", 0)) for p in payments)
    total_commission = sum(
        float(p.get("platform_fee_amount", p.get("commission_amount", 0)))
        for p in payments
    )

    return {
        "total_users": total_users,
        "total_editors": total_editors,
        "total_projects": total_projects,
        "new_requests": new_requests,
        "accepted_projects": accepted_projects,
        "active_projects": active_projects,
        "completed_projects": completed_projects,
        "pending_verification": pending_verification_count,
        "total_revenue": round(total_revenue, 2),
        "total_platform_commission": round(total_commission, 2),
    }


@router.get("/accounts/archived")
async def archived_accounts(current_user: dict = Depends(require_admin)):
    """Dedicated, PII-free deletion audit view; never mixed into active lists."""
    docs = await account_deletion_audit_logs_col.find(
        {}, {"account_id": 1, "role": 1, "deleted_at": 1, "deletion_method": 1}
    ).sort("deleted_at", -1).to_list(500)
    return {"accounts": serialize_list(docs)}


@router.get("/requests")
async def manage_requests(current_user: dict = Depends(require_admin)):
    docs = await requests_col.find({}).sort("created_at", -1).to_list(500)
    return {"items": serialize_list(docs)}


@router.get("/payment-protection")
async def manage_payment_protection(current_user: dict = Depends(require_admin)):
    docs = await payments_col.find({
        "payment_type": "project_payment",
        "status": {"$in": ["PENDING", "AUTHORIZED", "CAPTURED", "REFUNDED"]},
    }).sort("created_at", -1).to_list(500)
    return {"items": serialize_list(docs)}


@router.get("/disputes")
async def manage_disputes(current_user: dict = Depends(require_admin)):
    docs = await disputes_col.find({}).sort("created_at", -1).to_list(500)
    return {"items": serialize_list(docs)}


@router.get("/chat-reports")
async def manage_chat_reports(current_user: dict = Depends(require_admin)):
    reports = await chat_reports_col.find({}).sort("created_at", -1).to_list(500)
    if not reports:
        message_count = await messages_col.count_documents({})
        return {"items": [], "message_count": message_count}
    return {"items": serialize_list(reports)}


@router.patch("/chat-reports/{report_id}")
async def decide_chat_report(report_id: str, body: ChatReportDecision, current_user: dict = Depends(require_admin)):
    report = await chat_reports_col.find_one({"_id": oid(report_id)})
    if not report:
        raise HTTPException(status_code=404, detail="Chat report not found")
    if report.get("status") in {"resolved", "dismissed"}:
        raise HTTPException(status_code=409, detail="This report was already resolved")
    user_update = None
    if body.action == "temporary_suspend":
        from datetime import timedelta
        user_update = {"is_banned": True, "ban_type": "temporary", "suspended_until": now_utc() + timedelta(days=body.suspension_days)}
    elif body.action == "permanent_block":
        user_update = {"is_banned": True, "ban_type": "permanent"}
    elif body.action == "warn":
        user_update = {"chat_warning_at": now_utc(), "chat_warning_reason": body.reason}
    if user_update:
        await users_col.update_one({"_id": report["reported_user_id"]}, {"$set": user_update})
    status = "dismissed" if body.action == "dismiss" else "resolved"
    await chat_reports_col.update_one({"_id": report["_id"], "status": {"$nin": ["resolved", "dismissed"]}}, {"$set": {"status": status, "decision": body.action, "decision_reason": body.reason, "decided_by": current_user["_id"], "decided_at": now_utc()}})
    await chat_audit_logs_col.insert_one({"event": "chat_report_decision", "report_id": report["_id"], "request_id": report["request_id"], "target_user_id": report["reported_user_id"], "admin_id": current_user["_id"], "action": body.action, "reason": body.reason, "created_at": now_utc()})
    if body.action in {"temporary_suspend", "permanent_block"}:
        await disconnect_user(str(report["reported_user_id"]))
    return {"status": status, "action": body.action}


@router.get("/reviews")
async def manage_reviews(current_user: dict = Depends(require_admin)):
    docs = await reviews_col.find({}).sort("created_at", -1).to_list(500)
    return {"items": serialize_list(docs)}


@router.get("/analytics")
async def reports_and_analytics(current_user: dict = Depends(require_admin)):
    stats = await dashboard_stats(current_user)
    status_pipeline = [
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    project_statuses = await requests_col.aggregate(status_pipeline).to_list(50)
    payment_statuses = await payments_col.aggregate(status_pipeline).to_list(50)
    return {
        "summary": stats,
        "project_statuses": [{"status": row["_id"], "count": row["count"]} for row in project_statuses],
        "payment_statuses": [{"status": row["_id"], "count": row["count"]} for row in payment_statuses],
    }


@router.get("/content")
async def manage_content(current_user: dict = Depends(require_admin)):
    docs = await content_col.find({}).sort("updated_at", -1).to_list(100)
    return {"items": serialize_list(docs)}
