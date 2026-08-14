from app.core.project_lifecycle import ALLOWED_TRANSITIONS, TERMINAL_STATUSES


def test_required_real_world_statuses_are_reachable():
    required = {
        "revision_requested", "cancel_requested", "cancelled", "disputed",
        "refund_pending", "refunded", "overdue", "expired", "admin_review",
        "payment_failed",
    }
    reachable = set().union(*ALLOWED_TRANSITIONS.values())
    assert required <= reachable


def test_terminal_statuses_do_not_have_outgoing_transitions_except_post_completion_dispute():
    assert TERMINAL_STATUSES == {"rejected", "cancelled", "refunded", "expired", "completed"}
    for status in TERMINAL_STATUSES - {"completed"}:
        assert status not in ALLOWED_TRANSITIONS
    assert ALLOWED_TRANSITIONS["completed"] == {"disputed", "refund_pending"}


def test_delivery_requires_admin_review_and_revision_can_return_to_work():
    assert "admin_review" in ALLOWED_TRANSITIONS["in_progress"]
    assert "delivered" in ALLOWED_TRANSITIONS["admin_review"]
    assert "revision_requested" in ALLOWED_TRANSITIONS["admin_review"]
    assert "in_progress" in ALLOWED_TRANSITIONS["revision_requested"]
