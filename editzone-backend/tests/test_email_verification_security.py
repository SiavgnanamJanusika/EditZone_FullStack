import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from bson import ObjectId
from fastapi import HTTPException, Response
from starlette.requests import Request

from app.core.security import get_current_user
from app.core.utils import now_utc
from app.routers.auth_router import _check_otp, _issue_tokens, verify_email_otp
from app.schemas.auth_schema import VerifyOtpRequest


def request(path="/api/v1/auth/verify-otp"):
    return Request({
        "type": "http", "method": "POST", "path": path,
        "headers": [], "client": ("127.0.0.1", 1234),
    })


class EmailVerificationSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_normal_session_cannot_be_issued_for_explicitly_unverified_account(self):
        user = {"_id": ObjectId(), "email": "member@example.com", "role": "user", "is_email_verified": False}
        with patch("app.routers.auth_router.auth_sessions_col.insert_one", AsyncMock()) as insert:
            with self.assertRaises(HTTPException) as raised:
                await _issue_tokens(user, Response(), request())
        self.assertEqual(raised.exception.status_code, 403)
        insert.assert_not_awaited()

    async def test_wrong_otp_is_rejected_without_consuming_or_verifying(self):
        record = {"backend": "mongodb", "_id": ObjectId(), "otp_hash": "not-the-submitted-hash", "attempts": 0, "created_at": now_utc()}
        with (
            patch("app.routers.auth_router.increment_otp_attempts", AsyncMock(return_value=1)),
            patch("app.routers.auth_router.consume_otp", AsyncMock()) as consume,
            patch("app.routers.auth_router.users_col.find_one_and_update", AsyncMock()) as update,
        ):
            with self.assertRaises(HTTPException) as raised:
                await _check_otp(record, "123456", "member@example.com", "verify_email", "127.0.0.1", None)
        self.assertEqual(raised.exception.detail, "Invalid verification code.")
        consume.assert_not_awaited()
        update.assert_not_awaited()

    async def test_expired_otp_is_deleted_and_rejected(self):
        record = {"backend": "mongodb", "_id": ObjectId(), "otp_hash": "hash", "attempts": 0, "created_at": now_utc() - timedelta(hours=1)}
        with patch("app.routers.auth_router.delete_otp", AsyncMock()) as delete:
            with self.assertRaises(HTTPException) as raised:
                await _check_otp(record, "123456", "member@example.com", "verify_email", "127.0.0.1", None)
        self.assertIn("expired", raised.exception.detail.lower())
        delete.assert_awaited_once_with("member@example.com", "verify_email")

    async def test_correct_otp_is_consumed_before_account_is_verified_and_session_issued(self):
        user = {"_id": ObjectId(), "email": "member@example.com", "role": "user", "is_email_verified": False, "registration_complete": False}
        verified = {**user, "is_email_verified": True, "email_verified": True}
        with (
            patch("app.routers.auth_router.users_col.find_one", AsyncMock(return_value=user)),
            patch("app.routers.auth_router.get_otp", AsyncMock(return_value={"backend": "mongodb", "_id": ObjectId()})),
            patch("app.routers.auth_router._check_otp", AsyncMock(return_value="submitted-hash")),
            patch("app.routers.auth_router.consume_otp", AsyncMock(return_value=True)) as consume,
            patch("app.routers.auth_router.users_col.find_one_and_update", AsyncMock(return_value=verified)) as update,
            patch("app.routers.auth_router._issue_tokens", AsyncMock(return_value={"role": "user"})) as issue,
        ):
            result = await verify_email_otp(VerifyOtpRequest(email=user["email"], otp="123456"), request(), Response())
        self.assertEqual(result["role"], "user")
        consume.assert_awaited_once()
        update_payload = update.await_args.args[1]["$set"]
        self.assertIs(update_payload["is_email_verified"], True)
        self.assertIs(update_payload["email_verified"], True)
        self.assertIn("email_verified_at", update_payload)
        issue.assert_awaited_once()

    async def test_reused_or_concurrent_otp_cannot_verify_or_create_session(self):
        user = {"_id": ObjectId(), "email": "member@example.com", "role": "editor", "is_email_verified": False}
        with (
            patch("app.routers.auth_router.users_col.find_one", AsyncMock(return_value=user)),
            patch("app.routers.auth_router.get_otp", AsyncMock(return_value={"backend": "mongodb", "_id": ObjectId()})),
            patch("app.routers.auth_router._check_otp", AsyncMock(return_value="submitted-hash")),
            patch("app.routers.auth_router.consume_otp", AsyncMock(return_value=False)),
            patch("app.routers.auth_router.users_col.find_one_and_update", AsyncMock()) as update,
            patch("app.routers.auth_router._issue_tokens", AsyncMock()) as issue,
        ):
            with self.assertRaises(HTTPException) as raised:
                await verify_email_otp(VerifyOtpRequest(email=user["email"], otp="123456"), request(), Response())
        self.assertEqual(raised.exception.detail, "Invalid verification code.")
        update.assert_not_awaited()
        issue.assert_not_awaited()

    async def test_unverified_session_is_denied_by_shared_backend_dependency(self):
        user_id = ObjectId()
        unverified = {"_id": user_id, "role": "user", "is_email_verified": False}
        fake_request = MagicMock(cookies={})
        with (
            patch("app.core.security.decode_token", return_value={"type": "access", "sub": str(user_id), "sid": "session"}),
            patch("app.core.security.users_col.find_one", AsyncMock(return_value=unverified)),
            patch("app.core.security.auth_sessions_col.find_one", AsyncMock(return_value={"_id": ObjectId()})),
        ):
            with self.assertRaises(HTTPException) as raised:
                await get_current_user(fake_request, "access-token")
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(raised.exception.detail, "Email verification is required")


if __name__ == "__main__":
    unittest.main()
