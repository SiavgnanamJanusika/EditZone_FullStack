from decimal import Decimal
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.services.payhere_service import amount_to_minor, format_amount, split_project_amount
from app.routers import quote_payment_router
from app.routers.quote_payment_router import STATUS_MAP, public_quote, quote_money


def test_commission_model_uses_original_project_amount():
    gross, commission, client_fee, revenue, editor_net = split_project_amount(Decimal("10000.00"))
    assert (gross, commission, client_fee, revenue, editor_net) == (1_000_000, 100_000, 100_000, 200_000, 900_000)
    assert gross + client_fee == 1_100_000


def test_money_rounding_never_uses_binary_float():
    assert format_amount(Decimal("10.005")) == "10.01"
    assert amount_to_minor(Decimal("10.005")) == 1001


def test_quote_breakdown_charges_client_fee_without_reducing_project_amount():
    breakdown = quote_money(Decimal("2500.00"))
    assert breakdown == {
        "project_amount_minor": 250_000,
        "client_service_fee_minor": 25_000,
        "client_total_minor": 275_000,
        "editor_commission_minor": 25_000,
        "editor_net_payable_minor": 225_000,
        "editzone_gross_revenue_minor": 50_000,
    }


def test_payhere_statuses_cover_success_pending_failure_cancel_and_chargeback():
    assert STATUS_MAP == {"2": "SUCCESS", "0": "PENDING", "-1": "CANCELLED", "-2": "FAILED", "-3": "CHARGEDBACK"}


def test_quote_expiry_days_supports_default_seven_and_exactly_thirty(monkeypatch):
    now = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(quote_payment_router, "now_utc", lambda: now)
    assert quote_payment_router._normalise_expiry(None, 7) == now + timedelta(days=7)
    assert quote_payment_router._normalise_expiry(None, 30) == now + timedelta(days=30)


def test_quote_expiry_rejects_more_than_thirty_days_with_field_error(monkeypatch):
    now = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(quote_payment_router, "now_utc", lambda: now)
    with pytest.raises(HTTPException) as raised:
        quote_payment_router._normalise_expiry(now + timedelta(days=31))
    assert raised.value.status_code == 422
    assert raised.value.detail[0]["loc"] == ["body", "expires_at"]


def test_public_quote_exposes_checkout_and_editor_net_contract():
    result = public_quote({
        "status": "SENT", "editor_net_payable_minor": 900_000,
        "project_amount_minor": 1_000_000, "client_service_fee_minor": 100_000,
        "client_total_minor": 1_100_000, "editor_commission_minor": 100_000,
    })
    assert result["editor_net_earning"] == "9000.00"
    assert result["can_pay"] is True
    assert result["can_edit"] is True
