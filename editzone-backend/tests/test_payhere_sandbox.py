import hashlib
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from bson import ObjectId

from app.config import settings
from app.routers.payment_router import _public_payment, _with_order_id, payhere_notification
from app.services.payhere_service import (
    PayHereAPIError, amount_to_minor, capture_authorization, checkout_hash, format_amount,
    notification_is_valid, refund_payment,
    retrieve_payment,
    split_amount,
    split_project_amount,
)


class FakeResponse:
    def __init__(self, payload, error=False):
        self.payload = payload
        self.error = error

    def raise_for_status(self):
        if self.error:
            raise httpx.HTTPStatusError("sandbox failure", request=None, response=None)

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def post(self, *_, **__):
        return self.response

    async def get(self, *_, **__):
        return self.response


def notify_signature(merchant, order, amount, currency, status, secret):
    secret_hash = hashlib.md5(secret.encode()).hexdigest().upper()
    return hashlib.md5(f"{merchant}{order}{amount}{currency}{status}{secret_hash}".encode()).hexdigest().upper()


class PayHereSandboxServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_amount_formatting_is_canonical_for_checkout_hash(self):
        self.assertEqual(format_amount(2750), "2750.00")
        self.assertEqual(format_amount("2750"), "2750.00")
        self.assertEqual(format_amount("2750.5"), "2750.50")

    def test_checkout_hash_uses_identical_normalized_wire_values(self):
        merchant, secret, order = "1210000", "sandbox-secret", "EZ-TEST-1"
        secret_hash = hashlib.md5(secret.encode()).hexdigest().upper()
        expected = hashlib.md5(f"{merchant}{order}2750.00LKR{secret_hash}".encode()).hexdigest().upper()
        self.assertEqual(
            checkout_hash(order, "2750", "lkr", merchant_id=f" {merchant} ", merchant_secret=f" {secret} "),
            expected,
        )

    async def test_card_authorization_signature(self):
        with patch.object(settings, "PAYHERE_MERCHANT_ID", "1210000"), patch.object(settings, "PAYHERE_MERCHANT_SECRET", "sandbox-secret"):
            signature = notify_signature("1210000", "EZ-1", "1000.00", "LKR", "3", "sandbox-secret")
            self.assertTrue(notification_is_valid("1210000", "EZ-1", "1000.00", "LKR", "3", signature))
            self.assertFalse(notification_is_valid("1210000", "EZ-1", "999.00", "LKR", "3", signature))

    async def test_capture_success_releases_authorization(self):
        response = FakeResponse({"status": 1, "data": {"status_code": 2, "payment_id": "PH-1", "captured_amount": 1000}})
        with patch("app.services.payhere_service._access_token", AsyncMock(return_value="token")), patch("app.services.payhere_service.httpx.AsyncClient", return_value=FakeClient(response)):
            result = await capture_authorization("auth-token", format_amount(1000))
        self.assertEqual(result["payment_id"], "PH-1")

    async def test_capture_failure_is_not_treated_as_release(self):
        response = FakeResponse({"status": -1, "msg": "Sandbox card declined"})
        with patch("app.services.payhere_service._access_token", AsyncMock(return_value="token")), patch("app.services.payhere_service.httpx.AsyncClient", return_value=FakeClient(response)):
            with self.assertRaises(PayHereAPIError):
                await capture_authorization("auth-token", "1000.00")

    async def test_refund_api_success_and_failure(self):
        success = FakeResponse({"status": 1, "data": "RF-1"})
        with patch("app.services.payhere_service._access_token", AsyncMock(return_value="token")), patch("app.services.payhere_service.httpx.AsyncClient", return_value=FakeClient(success)):
            self.assertEqual((await refund_payment(reason="Client cancellation", payment_id="PH-1"))["data"], "RF-1")
        failure = FakeResponse({"status": -1, "msg": "Refund rejected"})
        with patch("app.services.payhere_service._access_token", AsyncMock(return_value="token")), patch("app.services.payhere_service.httpx.AsyncClient", return_value=FakeClient(failure)):
            with self.assertRaises(PayHereAPIError):
                await refund_payment(reason="Client cancellation", payment_id="PH-1")

    async def test_retrieval_api_returns_provider_records(self):
        response = FakeResponse({"status": 1, "data": [{"order_id": "EZ-1", "status": "RECEIVED"}]})
        with patch("app.services.payhere_service._access_token", AsyncMock(return_value="token")), patch("app.services.payhere_service.httpx.AsyncClient", return_value=FakeClient(response)):
            records = await retrieve_payment("EZ-1")
        self.assertEqual(records[0]["status"], "RECEIVED")

    def test_public_payment_status_mapping(self):
        self.assertEqual(_public_payment({"status": "AUTHORIZED", "payment_type": "project_payment"})["protection_status"], "PROTECTED")
        self.assertEqual(_public_payment({"status": "CAPTURED", "payment_type": "project_payment"})["protection_status"], "RELEASED")

    def test_return_url_contains_server_order_reference(self):
        self.assertEqual(
            _with_order_id("http://localhost:5173/payment/success", "EZ-123"),
            "http://localhost:5173/payment/success?order_id=EZ-123",
        )

    def test_integer_minor_units_and_commission_invariant(self):
        self.assertEqual(amount_to_minor("1000.10"), 100010)
        gross, fee, editor = split_amount("1000.10", "15")
        self.assertEqual(gross, fee + editor)
        self.assertEqual((gross, fee, editor), (100010, 15002, 85008))

    def test_project_settlement_is_ten_percent_each_and_editor_ninety(self):
        gross, platform, service, admin_total, editor = split_project_amount("1000.00")
        self.assertEqual((gross, platform, service, admin_total, editor), (100000, 10000, 10000, 20000, 90000))
        self.assertEqual(gross + service, admin_total + editor)


class PayHereCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_signed_checkout_callback_marks_payment_paid_and_protected(self):
        merchant, secret = "1210000", "sandbox-secret"
        fields = {
            "merchant_id": merchant, "order_id": "EZ-CALLBACK", "payhere_amount": "1500.00",
            "payhere_currency": "LKR", "status_code": "2",
            "payment_id": "PH-PAID", "status_message": "Paid",
        }
        fields["md5sig"] = notify_signature(merchant, fields["order_id"], fields["payhere_amount"], "LKR", "2", secret)
        request = SimpleNamespace(form=AsyncMock(return_value=fields))
        client_id, editor_id = ObjectId(), ObjectId()
        payment = {"_id": ObjectId(), "order_id": fields["order_id"], "authorized_amount": 1500, "currency": "LKR", "status": "PENDING", "request_id": str(ObjectId()), "payment_type": "project_payment", "user_id": client_id, "editor_user_id": editor_id}
        fields["custom_1"] = payment["request_id"]
        fields["custom_2"] = payment["request_id"]
        update = AsyncMock(return_value=SimpleNamespace(modified_count=1))
        with (
            patch.object(settings, "PAYHERE_SANDBOX", True),
            patch.object(settings, "PAYHERE_MERCHANT_ID", merchant),
            patch.object(settings, "PAYHERE_MERCHANT_SECRET", secret),
            patch("app.routers.payment_router.payments_col.find_one", AsyncMock(return_value=payment)),
            patch("app.routers.payment_router.payments_col.update_one", update),
            patch("app.routers.payment_router.requests_col.find_one", AsyncMock(return_value={"_id": ObjectId(payment["request_id"]), "status": "accepted", "user_id": client_id, "editor_user_id": editor_id, "project_title": "Test project"})),
            patch("app.routers.payment_router.transition_project", AsyncMock()),
            patch("app.routers.payment_router.payment_webhooks_col.insert_one", AsyncMock()),
            patch("app.routers.payment_router.payment_webhooks_col.update_one", AsyncMock()),
            patch("app.routers.payment_router.payment_escrows_col.update_one", AsyncMock()),
            patch("app.routers.payment_router.append_ledger", AsyncMock()),
            patch("app.routers.payment_router.notifications_col.insert_many", AsyncMock()),
            patch("app.routers.payment_router.sio.emit", AsyncMock()),
        ):
            response = await payhere_notification(request)
        self.assertEqual(response.status_code, 200)
        saved = update.await_args_list[0].args[1]["$set"]
        self.assertEqual(saved["status"], "CAPTURED")
        self.assertEqual(saved["protection_status"], "PROTECTED")
        self.assertTrue(saved["signature_verified"])
        self.assertIn("paid_at", saved)


if __name__ == "__main__":
    unittest.main()
