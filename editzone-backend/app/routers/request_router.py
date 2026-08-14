from datetime import timedelta
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.db.mongodb import (
    db, requests_col, editors_col, users_col, notifications_col, payments_col,
    disputes_col, deliveries_col, multipart_uploads_col, messages_col,
)
from app.schemas.schemas import CreateRequestBody, RequestActionBody, LifecycleReasonBody, CancelDecisionBody, ProposalBody, FinalDeliveryBody
from app.core.project_lifecycle import transition_project
from app.core.security import get_current_user, require_user, require_editor
from app.core.utils import serialize_doc, serialize_list, oid, now_utc
from app.core.validators import is_valid_upload_url
from app.sockets.socket_manager import sio
from app.core.accounts import ACTIVE_ACCOUNT_FILTER, ACTIVE_EDITOR_FILTER, account_not_available
from app.core.proposals import payment_eligibility
from app.services.media_metadata import MediaMetadataError, gridfs_video_duration, s3_video_metadata

router = APIRouter(prefix="/api/v1/requests", tags=["Requests"])


async def _notify(user_id: ObjectId, title: str, body: str, request_id: str = None):
    doc = {
        "user_id": user_id,
        "title": title,
        "body": body,
        "request_id": request_id,
        "is_read": False,
        "created_at": now_utc(),
    }
    await notifications_col.insert_one(doc)
    await sio.emit("notification", {"title": title, "body": body}, room=str(user_id))


@router.post("", status_code=201)
async def create_request(body: CreateRequestBody, current_user: dict = Depends(require_user)):
    now = now_utc()
    editor = await editors_col.find_one({"_id": oid(body.editor_id), **ACTIVE_EDITOR_FILTER})
    if not editor:
        raise account_not_available()
    editor_user = await users_col.find_one({
        "_id": editor.get("user_id"), "role": "editor", "is_banned": {"$ne": True},
        **ACTIVE_ACCOUNT_FILTER,
    }, {"_id": 1})
    if not editor_user:
        raise account_not_available()
    if not editor.get("is_available", True):
        raise HTTPException(status_code=409, detail="This editor is not currently accepting new projects")

    doc = {
        "user_id": current_user["_id"],
        "editor_id": editor["_id"],
        "editor_user_id": editor["user_id"],
        "project_title": body.project_title,
        "project_description": body.project_description,
        "brief": body.model_dump(exclude={"editor_id", "project_title", "project_description"}),
        "status": "pending",  # pending -> accepted/rejected -> in_progress -> delivered -> completed
        "delivered_file_url": None,
        "admin_approved": False,
        "proposal_required": True,
        "proposal_status": "not_started",
        "proposal_version": 0,
        "revision_count": 0,
        "created_at": now,
        "status_history": [{"from": None, "to": "pending", "reason": "Project created", "actor_id": current_user["_id"], "actor_role": "user", "created_at": now}],
    }
    result = await requests_col.insert_one(doc)
    doc["_id"] = result.inserted_id

    await _notify(editor["user_id"], "New Project Request",
                  f"{current_user['username']} sent you a project request: {body.project_title}",
                  str(doc["_id"]))
    return serialize_doc(doc)


@router.get("/mine")
async def my_requests(current_user: dict = Depends(get_current_user)):
    if current_user["role"] == "editor":
        editor = await editors_col.find_one({"user_id": current_user["_id"]})
        if not editor:
            return {"requests": []}
        docs = await requests_col.find({"editor_id": editor["_id"]}).sort("created_at", -1).to_list(200)
    else:
        docs = await requests_col.find({"user_id": current_user["_id"]}).sort("created_at", -1).to_list(200)
    return {"requests": serialize_list(docs)}


@router.get("/{request_id}/suggestions")
async def suggested_editors(request_id: str, current_user: dict = Depends(require_user)):
    request_doc = await requests_col.find_one({"_id": oid(request_id)})
    if not request_doc:
        raise HTTPException(status_code=404, detail="Request not found")
    if request_doc["user_id"] != current_user["_id"]:
        raise HTTPException(status_code=403, detail="Not authorized to view these suggestions")
    if request_doc["status"] != "rejected":
        raise HTTPException(status_code=409, detail="Suggestions are available after an editor rejects the request")

    original_editor = await editors_col.find_one({"_id": request_doc["editor_id"]})
    query = {
        "_id": {"$ne": request_doc["editor_id"]},
        "is_available": {"$ne": False},
        **ACTIVE_EDITOR_FILTER,
    }
    if original_editor and original_editor.get("category"):
        query["category"] = original_editor["category"]

    candidates = await editors_col.find(query).sort([
        ("rating_avg", -1),
        ("rating_count", -1),
        ("total_views", -1),
    ]).limit(4).to_list(4)
    if not candidates and query.get("category"):
        query.pop("category")
        candidates = await editors_col.find(query).sort([
            ("rating_avg", -1),
            ("rating_count", -1),
        ]).limit(4).to_list(4)

    user_ids = [candidate["user_id"] for candidate in candidates]
    users = await users_col.find(
        {"_id": {"$in": user_ids}, "role": "editor", "is_banned": {"$ne": True}, **ACTIVE_ACCOUNT_FILTER},
        {"username": 1},
    ).to_list(4)
    users_by_id = {user["_id"]: user for user in users}
    suggestions = []
    for candidate in candidates:
        user = users_by_id.get(candidate["user_id"])
        if not user:
            continue
        item = serialize_doc(candidate)
        item["username"] = user.get("username", "Editor")
        suggestions.append(item)
    return {"editors": suggestions}


@router.get("/{request_id}")
async def get_request(request_id: str, current_user: dict = Depends(get_current_user)):
    doc = await requests_col.find_one({"_id": oid(request_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Request not found")
    if current_user["_id"] not in (doc["user_id"], doc["editor_user_id"]) and current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Not authorized to view this request")
    return serialize_doc(doc)


@router.patch("/{request_id}/respond")
async def respond_to_request(request_id: str, body: RequestActionBody, current_user: dict = Depends(require_editor)):
    doc = await requests_col.find_one({"_id": oid(request_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Request not found")
    if doc["editor_user_id"] != current_user["_id"]:
        raise HTTPException(status_code=403, detail="Not your request to respond to")
    if doc["status"] != "pending":
        raise HTTPException(status_code=400, detail="Request already responded to")

    new_status = "accepted" if body.action == "accept" else "rejected"
    transition_time = now_utc()
    response_data = {"status": new_status, "responded_at": transition_time, "status_updated_at": transition_time, f"{new_status}_at": transition_time}
    updated = await requests_col.find_one_and_update(
        {"_id": doc["_id"], "status": "pending"},
        {"$set": response_data, "$push": {"status_history": {"from": "pending", "to": new_status, "reason": f"Editor {new_status} the project request", "actor_id": current_user["_id"], "actor_role": "editor", "created_at": transition_time}}},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise HTTPException(status_code=409, detail="Request was already responded to")

    if new_status == "accepted":
        await _notify(doc["user_id"], "Request Accepted",
                      f"Your project '{doc['project_title']}' was accepted. Open the chat to continue.",
                      str(doc["_id"]))
    else:
        await _notify(doc["user_id"], "Request Rejected",
                      f"Your project '{doc['project_title']}' was rejected. Check out similar editors.",
                      str(doc["_id"]))

    return serialize_doc(updated)


async def _save_proposal(doc: dict, body: ProposalBody, current_user: dict, kind: str):
    if doc["status"] not in ("accepted", "payment_failed") or doc.get("payment_authorized"):
        raise HTTPException(status_code=409, detail="Price negotiation is closed after payment authorization")
    version = int(doc.get("proposal_version", 0)) + 1
    proposal_id = ObjectId()
    client_accepted = kind == "client_counter"
    editor_accepted = kind == "editor_proposal"
    proposal = {"_id": proposal_id, "version": version, "kind": kind, "amount": body.amount, "delivery_days": body.delivery_days, "included_revisions": body.included_revisions, "message": body.message, "created_by": current_user["_id"], "client_accepted": client_accepted, "editor_accepted": editor_accepted, "created_at": now_utc()}
    updated = await requests_col.find_one_and_update(
        {"_id": doc["_id"], "proposal_version": doc.get("proposal_version", {"$exists": False})},
        {"$set": {"proposal_id": proposal_id, "proposal_amount": body.amount, "proposal_delivery_days": body.delivery_days, "proposal_revision_limit": body.included_revisions, "proposal_message": body.message, "proposal_status": "awaiting_client" if kind == "editor_proposal" else "awaiting_editor", "proposal_version": version, "proposal_client_accepted": client_accepted, "proposal_editor_accepted": editor_accepted}, "$unset": {"proposal_accepted_at": "", "proposal_accepted_by": ""}, "$push": {"proposal_history": proposal}},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise HTTPException(status_code=409, detail="Proposal changed; refresh before submitting another offer")
    await sio.emit("proposal_updated", serialize_doc(updated), room=str(doc["_id"]))
    return updated


@router.post("/{request_id}/proposal")
async def submit_proposal(request_id: str, body: ProposalBody, current_user: dict = Depends(require_editor)):
    doc = await requests_col.find_one({"_id": oid(request_id)})
    if not doc or doc.get("editor_user_id") != current_user["_id"]:
        raise HTTPException(status_code=404, detail="Project not found")
    return serialize_doc(await _save_proposal(doc, body, current_user, "editor_proposal"))


@router.post("/{request_id}/counter-offer")
async def counter_offer(request_id: str, body: ProposalBody, current_user: dict = Depends(require_user)):
    doc = await requests_col.find_one({"_id": oid(request_id)})
    if not doc or doc.get("user_id") != current_user["_id"]:
        raise HTTPException(status_code=404, detail="Project not found")
    return serialize_doc(await _save_proposal(doc, body, current_user, "client_counter"))


@router.post("/{request_id}/proposal/accept")
async def accept_proposal(request_id: str, current_user: dict = Depends(get_current_user)):
    doc = await requests_col.find_one({"_id": oid(request_id)})
    if not doc or current_user["_id"] not in (doc.get("user_id"), doc.get("editor_user_id")):
        raise HTTPException(status_code=404, detail="Proposal not found")
    state = payment_eligibility(doc)
    if not state["proposal_id"]:
        raise HTTPException(status_code=409, detail="No proposal has been submitted")
    is_client = current_user["_id"] == doc["user_id"]
    field = "proposal_client_accepted" if is_client else "proposal_editor_accepted"
    other_field = "proposal_editor_accepted" if is_client else "proposal_client_accepted"
    if state["client_accepted" if is_client else "editor_accepted"]:
        raise HTTPException(status_code=409, detail="You already accepted this proposal revision")
    history_field = "proposal_history.$[latest].client_accepted" if is_client else "proposal_history.$[latest].editor_accepted"
    set_fields = {field: True}
    # Materialize only acceptance that can be proved from a legacy proposal's
    # creator/accepted_by actors; this is not acceptance on another user's behalf.
    if doc.get(other_field) is None and state["editor_accepted" if is_client else "client_accepted"]:
        set_fields[other_field] = True
    if any(int(item.get("version") or 0) == int(doc["proposal_version"]) for item in doc.get("proposal_history") or []):
        set_fields[history_field] = True
    updated = await requests_col.find_one_and_update(
        {"_id": doc["_id"], "proposal_version": doc["proposal_version"], field: {"$ne": True}},
        {"$set": set_fields},
        **({"array_filters": [{"latest.version": doc["proposal_version"]}]} if history_field in set_fields else {}),
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise HTTPException(status_code=409, detail="Proposal changed while it was being accepted")
    accepted_at = now_utc()
    finalized = await requests_col.find_one_and_update(
        {"_id": doc["_id"], "proposal_version": doc["proposal_version"], "proposal_client_accepted": True, "proposal_editor_accepted": True},
        {"$set": {"proposal_status": "accepted", "proposal_accepted_at": accepted_at}},
        return_document=ReturnDocument.AFTER,
    )
    updated = finalized or updated
    await sio.emit("proposal_updated", serialize_doc(updated), room=str(doc["_id"]))
    return serialize_doc(updated)


@router.post("/{request_id}/deliver")
async def deliver_video(
    request_id: str,
    body: FinalDeliveryBody | None = Body(default=None),
    file_url: str | None = Query(default=None),
    current_user: dict = Depends(require_editor),
):
    """Validate the editor's private final output and request client payment."""
    doc = await requests_col.find_one({"_id": oid(request_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Request not found")
    if doc["editor_user_id"] != current_user["_id"]:
        raise HTTPException(status_code=403, detail="Not your request")
    upload_id = body.upload_id if body else None
    upload = None
    storage_type = None
    if upload_id:
        upload = await multipart_uploads_col.find_one({"upload_id": upload_id})
        storage_type = "s3" if upload else None
        if not upload and ObjectId.is_valid(upload_id):
            grid_record = await db["uploads.files"].find_one({"_id": ObjectId(upload_id)})
            if grid_record:
                metadata = grid_record.get("metadata") or {}
                upload = {
                    "upload_id": upload_id,
                    "owner_id": metadata.get("owner_id"),
                    "request_id": metadata.get("request_id"),
                    "purpose": metadata.get("purpose"),
                    "original_name": metadata.get("original_name") or grid_record.get("filename"),
                    "content_type": metadata.get("content_type"),
                    "size": grid_record.get("length", 0),
                    "scan_status": metadata.get("scan_status"),
                    "key": grid_record.get("filename"),
                }
                storage_type = "gridfs"
        if not upload:
            raise HTTPException(status_code=404, detail="Final video upload not found")
        if upload.get("owner_id") != current_user["_id"] or upload.get("request_id") != request_id:
            raise HTTPException(status_code=403, detail="This upload does not belong to this editor project")
        if upload.get("purpose") != "final_delivery":
            raise HTTPException(status_code=400, detail="The selected upload is not a final delivery")
        if upload.get("scan_status") != "safe":
            raise HTTPException(status_code=423, detail="Final video is still undergoing security scanning")
    elif file_url and is_valid_upload_url(file_url):
        # Backwards-compatible lookup for clients deployed before upload_id was
        # added. The database record, never the submitted URL, remains trusted.
        upload_id = file_url.rstrip("/").rsplit("/", 1)[-1]
        upload = await multipart_uploads_col.find_one({"upload_id": upload_id})
        storage_type = "s3"
        if not upload and ObjectId.is_valid(upload_id):
            grid_record = await db["uploads.files"].find_one({"_id": ObjectId(upload_id)})
            if grid_record:
                metadata = grid_record.get("metadata") or {}
                upload = {"upload_id": upload_id, "owner_id": metadata.get("owner_id"), "request_id": metadata.get("request_id"), "purpose": metadata.get("purpose"), "original_name": metadata.get("original_name") or grid_record.get("filename"), "content_type": metadata.get("content_type"), "size": grid_record.get("length", 0), "scan_status": metadata.get("scan_status"), "key": grid_record.get("filename")}
                storage_type = "gridfs"
        if not upload or upload.get("owner_id") != current_user["_id"] or upload.get("request_id") != request_id or upload.get("purpose") != "final_delivery" or upload.get("scan_status") != "safe":
            raise HTTPException(status_code=400, detail="Final delivery must be a safe upload owned by this editor")
    else:
        raise HTTPException(status_code=400, detail="Select a completed final video upload")
    # Retrying an upload request after the first submission must be idempotent. This can
    # happen when a client loses the response after the database update has succeeded.
    if doc["status"] in ("admin_review", "delivered"):
        delivery = await deliveries_col.find_one({"project_id": request_id}, sort=[("version", -1)])
        return {
            "message": "Final work was already submitted and is awaiting client approval.",
            "status": doc["status"],
            "delivery": serialize_doc(delivery) if delivery else None,
        }
    if doc["status"] == "completed":
        raise HTTPException(status_code=409, detail="This delivery has already been approved and completed")
    if doc["status"] not in ("accepted", "in_progress", "overdue", "revision_requested"):
        raise HTTPException(
            status_code=409,
            detail=f"Final delivery is unavailable while the request is {doc['status'].replace('_', ' ')}",
        )
    previous = await deliveries_col.find_one({"project_id": request_id}, sort=[("version", -1)])
    version = int((previous or {}).get("version", 0)) + 1
    uploaded_at = now_utc()
    delivery = {
        "delivery_id": f"{request_id}-v{version}",
        "project_id": request_id,
        "chat_id": request_id,
        "order_id": None,
        "editor_id": doc["editor_user_id"],
        "client_id": doc["user_id"],
        "upload_id": upload_id,
        "storage_type": storage_type,
        "storage_key": upload.get("key"),
        "original_filename": upload.get("original_name") or "final-video",
        "file_size": int(upload.get("size") or 0),
        "content_type": upload.get("content_type") or "video/mp4",
        "duration": None,
        "upload_status": "processing",
        "payment_status": "not_started",
        "access_status": "locked",
        "delivery_message": (body.delivery_message or "").strip() if body else "",
        "created_at": uploaded_at,
        "uploaded_at": uploaded_at,
        "delivery_status": "PROCESSING",
        "version": version,
        "admin_reviewed_at": None,
        "released_at": None,
    }
    try:
        metadata = await (
            s3_video_metadata(upload["bucket"], upload["key"])
            if storage_type == "s3"
            else gridfs_video_duration(ObjectId(upload_id))
        )
        delivery["duration"] = metadata["duration"] if isinstance(metadata, dict) else metadata
        delivery["upload_status"] = "ready_for_payment"
        delivery["delivery_status"] = "READY_FOR_PAYMENT"
        await deliveries_col.insert_one(delivery)
    except MediaMetadataError as exc:
        raise HTTPException(status_code=422, detail=f"Final video validation failed: {exc}") from exc
    except DuplicateKeyError:
        existing_delivery = await deliveries_col.find_one({"project_id": request_id}, sort=[("version", -1)])
        if existing_delivery:
            return {"message": "Final work was already submitted for admin review.", "delivery": serialize_doc(existing_delivery)}
        raise HTTPException(status_code=409, detail="A duplicate final delivery was blocked")
    target_status = "admin_review" if doc["status"] in {"in_progress", "overdue", "revision_requested"} else "in_progress"
    updated_project = await transition_project(doc, target_status, current_user, reason="Editor submitted final output; client payment requested", extra={
        "delivered_file_url": None,
        "final_delivery_id": delivery["delivery_id"],
        "delivery_status": "READY_FOR_PAYMENT",
        "delivered_at": uploaded_at,
        "admin_approved": False,
    })
    system_text = "Your final edited video is ready. Complete the payment to unlock and download the output."
    message = {
        "request_id": request_id, "sender_id": "system", "receiver_id": str(doc["user_id"]),
        "text": system_text, "file_url": None, "file_type": None,
        "message_type": "system", "delivery_id": delivery["delivery_id"],
        "delivery_status": "sent", "created_at": uploaded_at,
    }
    inserted_message = await messages_col.insert_one(message)
    message["_id"] = inserted_message.inserted_id
    await sio.emit("new_message", serialize_doc(message), room=f"chat_{request_id}")
    await _notify(
        doc["user_id"],
        "Final Output Ready — Payment Required",
        system_text,
        request_id,
    )
    await _notify(current_user["_id"], "Final Output Uploaded", "Final output validated. Waiting for client payment.", request_id)
    return {"message": "Final output is ready for client payment.", "delivery": serialize_doc(delivery), "project": serialize_doc(updated_project)}


@router.get("/{request_id}/delivery")
async def get_final_delivery(request_id: str, current_user: dict = Depends(get_current_user)):
    project = await requests_col.find_one({"_id": oid(request_id)})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if current_user.get("role") != "admin" and current_user["_id"] not in (project["user_id"], project["editor_user_id"]):
        raise HTTPException(status_code=403, detail="Not authorized for this delivery")
    delivery = await deliveries_col.find_one({"project_id": request_id}, sort=[("version", -1)])
    if not delivery:
        return {"delivery": None, "message": "Final video has not been submitted."}
    result = serialize_doc(delivery)
    result.pop("storage_key", None)
    result["access_path"] = (
        f"/api/v1/uploads/s3/file/{delivery['upload_id']}"
        if delivery.get("storage_type") == "s3"
        else f"/api/v1/uploads/file/{delivery.get('storage_key', '')}"
    )
    result["can_access"] = (
        current_user.get("role") == "admin"
        or current_user["_id"] == project["editor_user_id"]
        or (current_user["_id"] == project["user_id"] and delivery.get("delivery_status") == "RELEASED")
    )
    return {"delivery": result}


@router.post("/{request_id}/revision")
async def request_revision(request_id: str, body: LifecycleReasonBody, current_user: dict = Depends(require_user)):
    doc = await requests_col.find_one({"_id": oid(request_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Request not found")
    if doc["user_id"] != current_user["_id"]:
        raise HTTPException(status_code=403, detail="Only the project owner can request a revision")
    revision_limit = int(doc.get("proposal_revision_limit", doc.get("brief", {}).get("requested_revision_limit", 2)))
    if doc.get("revision_count", 0) >= revision_limit:
        raise HTTPException(status_code=409, detail=f"The agreed {revision_limit}-revision limit was reached; open a dispute for admin review")
    updated = await transition_project(doc, "revision_requested", current_user, reason=body.reason, extra={"revision_reason": body.reason, "admin_approved": False, "revision_count": doc.get("revision_count", 0) + 1})
    await _notify(doc["editor_user_id"], "Revision Requested", body.reason[:180], request_id)
    return serialize_doc(updated)


@router.post("/{request_id}/revision/accept")
async def accept_revision(request_id: str, current_user: dict = Depends(require_editor)):
    doc = await requests_col.find_one({"_id": oid(request_id)})
    if not doc or doc.get("editor_user_id") != current_user["_id"]:
        raise HTTPException(status_code=404, detail="Project revision not found")
    return serialize_doc(await transition_project(doc, "in_progress", current_user, reason="Editor accepted the requested revision"))


@router.post("/{request_id}/cancel")
async def request_cancellation(request_id: str, body: LifecycleReasonBody, current_user: dict = Depends(get_current_user)):
    doc = await requests_col.find_one({"_id": oid(request_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Request not found")
    if current_user["_id"] not in (doc["user_id"], doc["editor_user_id"]):
        raise HTTPException(status_code=403, detail="Only project members can request cancellation")
    if doc["status"] == "pending" or (doc["status"] in ("accepted", "payment_failed") and not doc.get("payment_authorized")):
        updated = await transition_project(doc, "cancelled", current_user, reason=body.reason, extra={"cancelled_by": current_user["_id"]})
    else:
        updated = await transition_project(doc, "cancel_requested", current_user, reason=body.reason, extra={"cancel_requested_by": current_user["_id"], "cancel_reason": body.reason})
        other = doc["editor_user_id"] if current_user["_id"] == doc["user_id"] else doc["user_id"]
        await _notify(other, "Cancellation Requested", body.reason[:180], request_id)
    return serialize_doc(updated)


@router.patch("/{request_id}/cancel")
async def decide_cancellation(request_id: str, body: CancelDecisionBody, current_user: dict = Depends(get_current_user)):
    doc = await requests_col.find_one({"_id": oid(request_id)})
    if not doc or doc.get("status") != "cancel_requested":
        raise HTTPException(status_code=404, detail="Cancellation request not found")
    if current_user["_id"] not in (doc["user_id"], doc["editor_user_id"]) or current_user["_id"] == doc.get("cancel_requested_by"):
        raise HTTPException(status_code=403, detail="The other project member must decide this request")
    if body.approve:
        payment = await payments_col.find_one({"request_id": request_id, "status": "AUTHORIZED"})
        target = "refund_pending" if payment else "cancelled"
    else:
        target = "overdue" if doc.get("was_overdue") else "in_progress"
    return serialize_doc(await transition_project(doc, target, current_user, reason=body.reason or ("Cancellation accepted" if body.approve else "Cancellation declined")))


@router.post("/{request_id}/dispute")
async def open_dispute(request_id: str, body: LifecycleReasonBody, current_user: dict = Depends(get_current_user)):
    doc = await requests_col.find_one({"_id": oid(request_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Request not found")
    if current_user["_id"] not in (doc["user_id"], doc["editor_user_id"]):
        raise HTTPException(status_code=403, detail="Only project members can open a dispute")
    if doc["status"] == "completed" and doc.get("completed_at") and doc["completed_at"] < now_utc() - timedelta(days=7):
        raise HTTPException(status_code=409, detail="The 7-day post-completion dispute window has closed")
    updated = await transition_project(doc, "disputed", current_user, reason=body.reason, extra={"dispute_reason": body.reason, "pre_dispute_status": doc["status"]})
    payment = await payments_col.find_one({"request_id": request_id, "payment_type": "project_payment"}, {"status": 1, "protection_status": 1, "authorized_amount": 1})
    await payments_col.update_one(
        {"request_id": request_id, "payment_type": "project_payment"},
        {"$set": {"dispute_status": "OPEN", "disputed_at": now_utc()}},
    )
    await disputes_col.update_one(
        {"request_id": request_id, "status": "open"},
        {"$setOnInsert": {"request_id": request_id, "project_id": doc["_id"], "opened_by": current_user["_id"], "against_user_id": doc["editor_user_id"] if current_user["_id"] == doc["user_id"] else doc["user_id"], "reason": body.reason, "status": "open", "proposal_snapshot": {"amount": doc.get("proposal_amount"), "delivery_days": doc.get("proposal_delivery_days"), "revisions": doc.get("proposal_revision_limit"), "version": doc.get("proposal_version")}, "revision_count": doc.get("revision_count", 0), "payment_snapshot": payment, "created_at": now_utc()}},
        upsert=True,
    )
    await _notify(doc["user_id"], "Project Dispute Opened", "An admin will review the project evidence.", request_id)
    await _notify(doc["editor_user_id"], "Project Dispute Opened", "An admin will review the project evidence.", request_id)
    return serialize_doc(updated)
