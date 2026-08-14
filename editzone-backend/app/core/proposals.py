"""Revision-bound proposal state shared by negotiation and payments."""

from typing import Any


def latest_proposal(request_doc: dict) -> dict | None:
    """Return the newest embedded proposal, including legacy request documents."""
    version = int(request_doc.get("proposal_version") or 0)
    if version <= 0:
        # Older accepted requests stored the editor's submitted offer directly,
        # before proposal revisions and acceptance flags existed. Use only that
        # real stored offer; never derive a charge from an estimated budget.
        legacy_amount = request_doc.get("proposal_amount")
        legacy_delivery = request_doc.get("proposal_delivery_days")
        legacy_accepted = (
            request_doc.get("status") in {"accepted", "payment_failed"}
            and request_doc.get("proposal_required") is not True
            and request_doc.get("proposal_submitted_at") is not None
        )
        if not legacy_accepted or legacy_amount is None or legacy_delivery is None:
            return None
        return {
            "proposal_id": str(request_doc.get("proposal_id") or f"{request_doc.get('_id')}:legacy"),
            "revision": 0,
            "price": legacy_amount,
            "delivery_days": legacy_delivery,
            "description": request_doc.get("proposal_message"),
            "created_by": request_doc.get("editor_user_id"),
            "client_accepted": True,
            "editor_accepted": True,
        }
    history = request_doc.get("proposal_history") or []
    matching = [item for item in history if int(item.get("version") or 0) == version]
    stored = max(matching, key=lambda item: item.get("created_at")) if matching else {}

    proposal = {
        "proposal_id": str(stored.get("_id") or request_doc.get("proposal_id") or f"{request_doc.get('_id')}:{version}"),
        "revision": version,
        "price": stored.get("amount", request_doc.get("proposal_amount")),
        "delivery_days": stored.get("delivery_days", request_doc.get("proposal_delivery_days")),
        "description": stored.get("message", request_doc.get("proposal_message")),
        "created_by": stored.get("created_by"),
    }

    client_accepted = stored.get("client_accepted", request_doc.get("proposal_client_accepted"))
    editor_accepted = stored.get("editor_accepted", request_doc.get("proposal_editor_accepted"))

    # Backward-compatible factual derivation for proposals written before the
    # two explicit flags existed: submitting terms accepts them for the author,
    # and proposal_accepted_by identifies the counterparty acceptance.
    creator = stored.get("created_by")
    accepted_by = request_doc.get("proposal_accepted_by") if request_doc.get("proposal_status") == "accepted" else None
    if client_accepted is None:
        client_accepted = creator == request_doc.get("user_id") or accepted_by == request_doc.get("user_id")
    if editor_accepted is None:
        editor_accepted = creator == request_doc.get("editor_user_id") or accepted_by == request_doc.get("editor_user_id")
    proposal["client_accepted"] = client_accepted is True
    proposal["editor_accepted"] = editor_accepted is True
    return proposal


def payment_eligibility(request_doc: dict) -> dict[str, Any]:
    proposal = latest_proposal(request_doc)
    result = {
        "payment_allowed": False,
        "proposal_id": proposal["proposal_id"] if proposal else None,
        "revision": proposal["revision"] if proposal else None,
        "amount": proposal["price"] if proposal else None,
        "delivery_days": proposal["delivery_days"] if proposal else None,
        "client_accepted": proposal["client_accepted"] if proposal else False,
        "editor_accepted": proposal["editor_accepted"] if proposal else False,
    }
    if not proposal:
        result["message"] = "No proposal has been submitted."
        return result
    if not proposal["client_accepted"]:
        result["message"] = "Client must accept the latest proposal."
        return result
    if not proposal["editor_accepted"]:
        result["message"] = "Editor must accept the latest proposal."
        return result
    try:
        valid_amount = float(proposal["price"]) > 0
        valid_delivery = int(proposal["delivery_days"]) > 0
    except (TypeError, ValueError):
        valid_amount = valid_delivery = False
    if not valid_amount:
        result["message"] = "The latest proposal does not have a valid price."
        return result
    if not valid_delivery:
        result["message"] = "The latest proposal does not have a valid delivery period."
        return result
    result["payment_allowed"] = True
    result["message"] = "Proposal accepted by both parties. Payment is ready."
    return result
