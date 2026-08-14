import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from bson import ObjectId
from fastapi import HTTPException

from app.config import Settings
from app.routers.auth_router import login, reset_password, send_email_otp
from app.routers.upload_router import upload_scan_status
from app.schemas.auth_schema import LoginRequest, ResetPasswordRequest
from app.services.financial_records import webhook_event_key
from app.services.otp_service import store_otp
from app.services.auth_throttle_service import require_captcha
from app.core.security import get_current_user


class ProductionConfigurationTests(unittest.TestCase):
    def test_jwt_secret_has_no_insecure_default(self):
        self.assertTrue(Settings.model_fields["JWT_SECRET_KEY"].is_required())

    def test_production_reports_missing_security_dependencies(self):
        with self.assertRaises(ValueError) as raised:
            Settings(
                _env_file=None, ENV="production", JWT_SECRET_KEY="x" * 64,
                MONGO_URI="", REDIS_URL="", AWS_S3_BUCKET="", SMTP_HOST="",
                SMTP_USER="", SMTP_PASSWORD="", TURNSTILE_SECRET_KEY="",
                PAYHERE_MERCHANT_ID="", PAYHERE_MERCHANT_SECRET="",
                PAYHERE_APP_ID="", PAYHERE_APP_SECRET="", PAYHERE_NOTIFY_URL="",
                FRONTEND_URL="", CLAMAV_HOST="",
            )
        message = str(raised.exception)
        for name in ("MONGO_URI", "REDIS_URL", "AWS_S3_BUCKET", "TURNSTILE_SECRET_KEY", "CLAMAV_HOST"):
            self.assertIn(name, message)

    def test_production_cors_does_not_add_localhost(self):
        from app import main

        with patch.object(main.settings, "ENV", "production"), patch.object(
            main.settings, "CORS_ORIGINS", ["https://app.editzone.example"]
        ), patch.object(main.settings, "FRONTEND_URL", "https://app.editzone.example"):
            self.assertEqual(main._cors_origins(), ["https://app.editzone.example"])


class StartupSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_security_headers_are_applied(self):
        from app.main import security_headers

        response = type("Response", (), {"headers": {}})()
        result = await security_headers(MagicMock(), AsyncMock(return_value=response))
        self.assertEqual(result.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(result.headers["X-Frame-Options"], "DENY")
        self.assertEqual(result.headers["Referrer-Policy"], "no-referrer")

    async def test_readiness_does_not_claim_unconfigured_payment_sandbox(self):
        from app import main

        redis = type("Redis", (), {"ping": AsyncMock(), "aclose": AsyncMock()})()
        with (
            patch.object(main.client.admin, "command", AsyncMock()),
            patch.object(main.Redis, "from_url", return_value=redis),
            patch.object(main.worker_heartbeats_col, "find_one", AsyncMock(return_value=None)),
            patch.object(main.settings, "PAYHERE_MERCHANT_ID", ""),
            patch.object(main.settings, "PAYHERE_MERCHANT_SECRET", ""),
        ):
            response = await main.health_ready()
        self.assertIn(b'"payment":"not_configured"', response.body)


class LoginFlowTests(unittest.IsolatedAsyncioTestCase):
    def _request(self):
        return type("Request", (), {
            "client": type("Client", (), {"host": "127.0.0.1"})(),
            "headers": {},
        })()

    async def test_naive_next_allowed_keeps_login_throttled_without_datetime_error(self):
        next_allowed = datetime.now() + timedelta(seconds=20)
        with (
            patch("app.routers.auth_router.get_scope_counts", AsyncMock(return_value={"email": 1, "ip": 1})),
            patch("app.routers.auth_router.get_counter", AsyncMock(return_value={"next_allowed_at": next_allowed})),
        ):
            with self.assertRaises(HTTPException) as raised:
                await login(LoginRequest(email="member@example.com", password="Password1"), MagicMock(), self._request())
        self.assertEqual(raised.exception.status_code, 429)
        self.assertIn("Too many attempts", raised.exception.detail)

    async def test_aware_expired_next_allowed_returns_only_invalid_credentials(self):
        next_allowed = datetime.now(timezone.utc) - timedelta(seconds=1)
        with (
            patch("app.routers.auth_router.get_scope_counts", AsyncMock(return_value={"email": 1, "ip": 1})),
            patch("app.routers.auth_router.get_counter", AsyncMock(return_value={"next_allowed_at": next_allowed})),
            patch("app.routers.auth_router.users_col.find_one", AsyncMock(return_value=None)),
            patch("app.routers.auth_router.increment_counter", AsyncMock(return_value={"_id": ObjectId(), "count": 2})),
            patch("app.routers.auth_router.auth_rate_limits_col.update_one", AsyncMock()),
        ):
            with self.assertRaises(HTTPException) as raised:
                await login(LoginRequest(email="unknown@example.com", password="Password1"), MagicMock(), self._request())
        self.assertEqual(raised.exception.status_code, 401)


class CaptchaVerificationTests(unittest.IsolatedAsyncioTestCase):
    def _request(self):
        return type("Request", (), {
            "client": type("Client", (), {"host": "127.0.0.1"})(),
            "headers": {},
        })()

    async def test_missing_captcha_token_returns_required_contract(self):
        with self.assertRaises(HTTPException) as raised:
            await require_captcha(None, "127.0.0.1")
        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(raised.exception.headers["X-Captcha-Required"], "true")

    async def test_invalid_turnstile_token_is_rejected(self):
        response = type("Response", (), {"json": lambda self: {"success": False}})()
        client = type("Client", (), {
            "__aenter__": AsyncMock(), "__aexit__": AsyncMock(return_value=None),
            "post": AsyncMock(return_value=response),
        })()
        client.__aenter__.return_value = client
        with (
            patch("app.services.auth_throttle_service.settings.TURNSTILE_SECRET_KEY", "server-secret"),
            patch("app.services.auth_throttle_service.httpx.AsyncClient", return_value=client),
        ):
            with self.assertRaises(HTTPException) as raised:
                await require_captcha("invalid-token", "127.0.0.1")
        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(raised.exception.detail, "CAPTCHA verification failed")

    async def test_valid_turnstile_token_is_accepted(self):
        response = type("Response", (), {"json": lambda self: {"success": True}})()
        client = type("Client", (), {
            "__aenter__": AsyncMock(), "__aexit__": AsyncMock(return_value=None),
            "post": AsyncMock(return_value=response),
        })()
        client.__aenter__.return_value = client
        with (
            patch("app.services.auth_throttle_service.settings.TURNSTILE_SECRET_KEY", "server-secret"),
            patch("app.services.auth_throttle_service.httpx.AsyncClient", return_value=client),
        ):
            await require_captcha("valid-token", "127.0.0.1")
        self.assertEqual(client.post.await_args.kwargs["data"]["response"], "valid-token")

    async def test_valid_client_and_editor_login_preserve_returned_role(self):
        for role in ("user", "editor"):
            user = {
                "_id": ObjectId(), "email": f"{role}@example.com", "role": role,
                "password_hash": "stored-hash", "is_email_verified": True,
                "registration_complete": True,
            }
            expected = {"role": role, "user": {"role": role}}
            with self.subTest(role=role):
                with (
                    patch("app.routers.auth_router.get_scope_counts", AsyncMock(return_value={"email": 0, "ip": 0})),
                    patch("app.routers.auth_router.get_counter", AsyncMock(return_value=None)),
                    patch("app.routers.auth_router.users_col.find_one", AsyncMock(return_value=user)),
                    patch("app.routers.auth_router.verify_password", return_value=True),
                    patch("app.routers.auth_router.clear_counter", AsyncMock()),
                    patch("app.routers.auth_router._issue_tokens", AsyncMock(return_value=expected)),
                ):
                    result = await login(LoginRequest(email=user["email"], password="Password1", role=role), MagicMock(), self._request())
                self.assertEqual(result["role"], role)

    async def test_valid_password_with_wrong_selected_role_returns_role_mismatch(self):
        user = {
            "_id": ObjectId(), "email": "editor@example.com", "role": "editor",
            "password_hash": "stored-hash", "is_email_verified": True,
            "registration_complete": True,
        }
        with (
            patch("app.routers.auth_router.get_scope_counts", AsyncMock(return_value={"email": 0, "ip": 0})),
            patch("app.routers.auth_router.get_counter", AsyncMock(return_value=None)),
            patch("app.routers.auth_router.users_col.find_one", AsyncMock(return_value=user)),
            patch("app.routers.auth_router.verify_password", return_value=True),
        ):
            with self.assertRaises(HTTPException) as raised:
                await login(
                    LoginRequest(email="editor@example.com", password="Password1", role="user"),
                    MagicMock(), self._request(),
                )
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(raised.exception.detail["code"], "ROLE_MISMATCH")
        self.assertEqual(raised.exception.detail["role"], "editor")

    async def test_login_normalizes_email_before_lookup(self):
        user = {
            "_id": ObjectId(), "email": "member@example.com", "role": "user",
            "password_hash": "stored-hash", "is_email_verified": True,
            "registration_complete": True,
        }
        find_one = AsyncMock(return_value=user)
        with (
            patch("app.routers.auth_router.get_scope_counts", AsyncMock(return_value={"email": 0, "ip": 0})),
            patch("app.routers.auth_router.get_counter", AsyncMock(return_value=None)),
            patch("app.routers.auth_router.users_col.find_one", find_one),
            patch("app.routers.auth_router.verify_password", return_value=True),
            patch("app.routers.auth_router.clear_counter", AsyncMock()),
            patch("app.routers.auth_router._issue_tokens", AsyncMock(return_value={"role": "user"})),
        ):
            await login(
                LoginRequest(email="MEMBER@EXAMPLE.COM", password="Password1", role="user"),
                MagicMock(), self._request(),
            )
        query = find_one.await_args.args[0]
        self.assertEqual(query["email"]["$regex"], "^member@example\\.com$")

    async def test_login_threshold_verifies_supplied_captcha_before_password(self):
        user = {
            "_id": ObjectId(), "email": "member@example.com", "role": "user",
            "password_hash": "stored-hash", "is_email_verified": True,
            "registration_complete": True,
        }
        with (
            patch("app.routers.auth_router.get_scope_counts", AsyncMock(return_value={"email": 3, "ip": 3})),
            patch("app.routers.auth_router.get_counter", AsyncMock(return_value=None)),
            patch("app.routers.auth_router.require_captcha", AsyncMock()) as verify_captcha,
            patch("app.routers.auth_router.users_col.find_one", AsyncMock(return_value=user)),
            patch("app.routers.auth_router.verify_password", return_value=True),
            patch("app.routers.auth_router.clear_counter", AsyncMock()),
            patch("app.routers.auth_router._issue_tokens", AsyncMock(return_value={"role": "user"})),
        ):
            result = await login(
                LoginRequest(email=user["email"], password="Password1", captcha_token="turnstile-token"),
                MagicMock(), self._request(),
            )
        verify_captcha.assert_awaited_once_with("turnstile-token", "127.0.0.1")
        self.assertEqual(result["role"], "user")

    async def test_login_without_existing_throttle_counter_returns_auth_error(self):
        request = type("Request", (), {
            "client": type("Client", (), {"host": "127.0.0.1"})(),
        })()
        response = type("Response", (), {})()
        with (
            patch("app.routers.auth_router.get_scope_counts", AsyncMock(return_value={"email": 0, "ip": 0})),
            patch("app.routers.auth_router.get_counter", AsyncMock(return_value=None)),
            patch("app.routers.auth_router.users_col.find_one", AsyncMock(return_value=None)),
            patch("app.routers.auth_router.increment_counter", AsyncMock(return_value={"_id": ObjectId(), "count": 1})),
            patch("app.routers.auth_router.auth_rate_limits_col.update_one", AsyncMock()),
        ):
            with self.assertRaises(HTTPException) as raised:
                await login(LoginRequest(email="unknown@example.com", password="Password1"), response, request)
        self.assertEqual(raised.exception.status_code, 401)

    async def test_access_token_requires_an_active_server_session(self):
        user_id = ObjectId()
        request = type("Request", (), {"cookies": {}})()
        with (
            patch("app.core.security.decode_token", return_value={"type": "access", "sub": str(user_id), "sid": "revoked-session"}),
            patch("app.core.security.users_col.find_one", AsyncMock(return_value={"_id": user_id, "role": "user", "is_email_verified": True})),
            patch("app.core.security.auth_sessions_col.find_one", AsyncMock(return_value=None)),
        ):
            with self.assertRaises(HTTPException) as raised:
                await get_current_user(request, "access-token")
        self.assertEqual(raised.exception.status_code, 401)
        self.assertIn("revoked", raised.exception.detail.lower())


class OtpStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_otp_is_hashed_in_redis_with_five_minute_ttl(self):
        redis = type("Redis", (), {
            "hset": AsyncMock(),
            "expire": AsyncMock(return_value=True),
            "aclose": AsyncMock(),
        })()
        with patch("app.services.otp_service._redis", return_value=redis):
            result = await store_otp("member@example.com", "verify_email", "hashed-value")
        self.assertEqual(result["backend"], "redis")
        mapping = redis.hset.await_args.kwargs["mapping"]
        self.assertEqual(mapping["otp_hash"], "hashed-value")
        self.assertNotIn("otp", mapping)
        self.assertEqual(redis.expire.await_args.args[1], 300)

    async def test_resend_during_cooldown_keeps_existing_otp(self):
        request = type("Request", (), {
            "client": type("Client", (), {"host": "127.0.0.1"})(),
        })()
        body = type("Body", (), {"email": "member@example.com", "captcha_token": None})()
        with (
            patch("app.routers.auth_router.users_col.find_one", AsyncMock(return_value={
                "_id": ObjectId(), "email": "member@example.com", "is_email_verified": False,
            })),
            patch("app.routers.auth_router.get_otp", AsyncMock(return_value={
                "created_at": __import__("app.core.utils", fromlist=["now_utc"]).now_utc(),
                "locked": False,
            })),
            patch("app.routers.auth_router.increment_counter", AsyncMock()) as increment,
            patch("app.routers.auth_router.store_otp", AsyncMock()) as store,
        ):
            result = await send_email_otp(body, request)
        self.assertIn("already sent", result["message"])
        increment.assert_not_awaited()
        store.assert_not_awaited()

    async def test_verified_account_cannot_request_registration_otp(self):
        request = type("Request", (), {
            "client": type("Client", (), {"host": "127.0.0.1"})(),
        })()
        body = type("Body", (), {"email": "member@example.com", "captcha_token": None})()
        with (
            patch("app.routers.auth_router.users_col.find_one", AsyncMock(return_value={
                "_id": ObjectId(), "email": "member@example.com", "is_email_verified": True,
            })),
            patch("app.routers.auth_router.delete_otp", AsyncMock()) as delete,
            patch("app.routers.auth_router.store_otp", AsyncMock()) as store,
        ):
            with self.assertRaises(HTTPException) as raised:
                await send_email_otp(body, request)
        self.assertEqual(raised.exception.status_code, 409)
        delete.assert_awaited_once_with("member@example.com", "verify_email")
        store.assert_not_awaited()


class PasswordResetFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_password_reset_revokes_existing_sessions(self):
        user_id = ObjectId()
        otp_id = ObjectId()
        request = type("Request", (), {
            "client": type("Client", (), {"host": "127.0.0.1"})(),
        })()
        body = ResetPasswordRequest(
            email="member@example.com",
            otp="123456",
            new_password="NewPassword1",
        )
        with (
            patch("app.routers.auth_router.get_otp", AsyncMock(return_value={"_id": otp_id, "backend": "mongodb"})),
            patch("app.routers.auth_router._check_otp", AsyncMock()),
            patch("app.routers.auth_router.hash_password", return_value="new-password-hash"),
            patch("app.routers.auth_router.users_col.find_one_and_update", AsyncMock(return_value={"_id": user_id})),
            patch("app.routers.auth_router.auth_sessions_col.update_many", AsyncMock()) as revoke_sessions,
            patch("app.routers.auth_router.delete_otp", AsyncMock()),
        ):
            result = await reset_password(body, request)

        self.assertEqual(result["message"], "Password reset successfully")
        revoke_sessions.assert_awaited_once()
        query, update = revoke_sessions.await_args.args
        self.assertEqual(query, {"user_id": user_id, "revoked_at": None})
        self.assertEqual(update["$set"]["revoke_reason"], "password_reset")


class UploadQuarantineTests(unittest.IsolatedAsyncioTestCase):
    async def test_scanning_upload_status_does_not_call_unavailable_scanner(self):
        upload_id = ObjectId()
        user = {"_id": ObjectId(), "role": "user"}
        record = {
            "_id": upload_id,
            "filename": "delivery.mp4",
            "metadata": {"owner_id": user["_id"], "scan_status": "scanning"},
        }
        collection = type("Collection", (), {"find_one": AsyncMock(return_value=record)})()
        with (
            patch("app.routers.upload_router.multipart_uploads_col.find_one", AsyncMock(return_value=None)),
            patch("app.routers.upload_router.db", {"uploads.files": collection}),
            patch("app.routers.upload_router.scan_gridfs_upload", AsyncMock()) as scanner,
        ):
            result = await upload_scan_status(str(upload_id), user)

        self.assertEqual(result["scan_status"], "scanning")
        self.assertIsNone(result["media_url"])
        scanner.assert_not_awaited()

    async def test_infected_upload_status_is_never_reported_safe(self):
        upload_id = ObjectId()
        user = {"_id": ObjectId(), "role": "user"}
        record = {"_id": upload_id, "metadata": {"owner_id": user["_id"], "scan_status": "infected"}}
        collection = type("Collection", (), {"find_one": AsyncMock(return_value=record)})()
        with (
            patch("app.routers.upload_router.multipart_uploads_col.find_one", AsyncMock(return_value=None)),
            patch("app.routers.upload_router.db", {"uploads.files": collection}),
        ):
            result = await upload_scan_status(str(upload_id), user)
        self.assertEqual(result["scan_status"], "infected")

    async def test_upload_status_requires_owner(self):
        upload_id = ObjectId()
        record = {"_id": upload_id, "metadata": {"owner_id": ObjectId(), "scan_status": "pending"}}
        collection = type("Collection", (), {"find_one": AsyncMock(return_value=record)})()
        with (
            patch("app.routers.upload_router.multipart_uploads_col.find_one", AsyncMock(return_value=None)),
            patch("app.routers.upload_router.db", {"uploads.files": collection}),
        ):
            with self.assertRaises(HTTPException) as raised:
                await upload_scan_status(str(upload_id), {"_id": ObjectId(), "role": "user"})
        self.assertEqual(raised.exception.status_code, 403)


class PaymentIdempotencyTests(unittest.TestCase):
    def test_webhook_key_is_stable_and_changes_with_gateway_status(self):
        fields = {"merchant_id": "m", "order_id": "o", "payment_id": "p", "status_code": "3", "payhere_amount": "10.00", "payhere_currency": "LKR", "md5sig": "sig"}
        self.assertEqual(webhook_event_key(fields), webhook_event_key(dict(fields)))
        changed = dict(fields, status_code="2")
        self.assertNotEqual(webhook_event_key(fields), webhook_event_key(changed))
