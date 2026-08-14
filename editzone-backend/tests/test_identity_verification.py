import struct
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError

from bson import ObjectId
from fastapi import HTTPException
from starlette.requests import Request

from app.routers.auth_router import complete_profile
from app.routers.identity_verification_router import verify_entered_nic_image
from app.routers.editor_verification_router import create_capture_session, submit_live_selfie, _release_session
from app.core.security import get_current_user
from app.schemas.auth_schema import CompleteProfileRequest
from app.services.identity_verification_service import (
    IdentityServiceError, IdentityValidationError, OcrProviderError, _translate_ocr_exception,
    analyze_nic_front, classify_nic_match, extract_nic_candidates, normalize_nic,
    validate_nic_image,
    validate_ocr_configuration,
)


def valid_png(width: int = 640, height: int = 480) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x02\x00\x00\x00"
        + b"x" * 20
        + b"IEND"
        + b"x" * 8
    )


class IdentityServiceTests(unittest.TestCase):
    def test_extracts_new_and_old_sri_lankan_nic(self):
        candidates, confidence = extract_nic_candidates([
            {"BlockType": "LINE", "Text": "NIC 200012345678", "Confidence": 97.5},
            {"BlockType": "LINE", "Text": "Old: 991234567V", "Confidence": 92},
        ])
        self.assertEqual(candidates, {"200012345678", "991234567V"})
        self.assertEqual(confidence, 97.5)

    def test_searches_word_blocks_from_complete_textract_response(self):
        candidates, _ = extract_nic_candidates([
            {"BlockType": "WORD", "Text": "200012345678", "Confidence": 99},
        ])
        self.assertEqual(candidates, {"200012345678"})

    def test_image_validation_checks_type_size_and_dimensions(self):
        extension, width, height = validate_nic_image(valid_png(), "image/png", 5)
        self.assertEqual((extension, width, height), (".png", 640, 480))
        with self.assertRaises(IdentityValidationError):
            validate_nic_image(b"not-an-image", "image/jpeg", 5)
        with self.assertRaises(IdentityValidationError):
            validate_nic_image(valid_png(100, 100), "image/png", 5)

    def test_normalizes_old_and_new_nic_formats(self):
        self.assertEqual(normalize_nic(" 991-234-567v "), "991234567V")
        self.assertEqual(normalize_nic("2000 1234 5678"), "200012345678")

    def test_extracts_punctuation_split_and_common_ocr_mistakes(self):
        candidates, _ = extract_nic_candidates([
            {"BlockType": "LINE", "Text": "NIC: 2O0O.I234-5678", "Confidence": 91},
            {"BlockType": "WORD", "Text": "99S23456BV", "Confidence": 88},
        ])
        self.assertEqual(candidates, {"200012345678", "995234568V"})

    def test_rejects_invalid_entered_nic(self):
        with self.assertRaises(IdentityValidationError):
            normalize_nic("200O12345678")

    def test_match_mismatch_unreadable_multiple_and_low_confidence(self):
        self.assertEqual(classify_nic_match("991234567V", {"991234567V": 95})["status"], "verified")
        self.assertEqual(classify_nic_match("200012345678", {"200012345678": 97})["status"], "verified")
        self.assertEqual(classify_nic_match("200012345678", {"200012345679": 97})["status"], "mismatch")
        self.assertEqual(classify_nic_match("200012345678", {})["status"], "unreadable")
        self.assertEqual(classify_nic_match("200012345678", {"200012345678": 98, "199912345678": 97})["status"], "verified")
        self.assertEqual(classify_nic_match("200012345678", {"200012345678": 30})["status"], "manual_review_required")
        self.assertEqual(classify_nic_match("200012345678", {}, detected_line_count=3)["status"], "nic_not_found")

    def test_rejects_unsupported_oversized_and_fake_images(self):
        with self.assertRaises(IdentityValidationError):
            validate_nic_image(valid_png(), "image/gif", 5)
        with self.assertRaises(IdentityValidationError):
            validate_nic_image(b"x" * (5 * 1024 * 1024 + 1), "image/png", 5)
        with self.assertRaises(IdentityValidationError):
            validate_nic_image(b"fake png content", "image/png", 5)


class TextractFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_textract_response_returns_candidate_and_line_count(self):
        response = {"Blocks": [{"BlockType": "LINE", "Text": "NIC 200012345678", "Confidence": 98}]}
        with patch("app.services.identity_verification_service.run_in_threadpool", AsyncMock(return_value=response)):
            result = await analyze_nic_front(valid_png(), "image/png")
        self.assertEqual(result["candidate_confidences"], {"200012345678": 98})
        self.assertEqual(result["detected_line_count"], 1)

    async def test_aws_textract_failure_is_translated(self):
        error = ClientError({"Error": {"Code": "InternalServerError", "Message": "failed"}}, "DetectDocumentText")
        with patch("app.services.identity_verification_service.run_in_threadpool", AsyncMock(side_effect=error)):
            with self.assertRaises(IdentityServiceError):
                await analyze_nic_front(valid_png())

    def test_credentials_access_denied_invalid_object_and_timeout_are_classified(self):
        self.assertEqual(_translate_ocr_exception(NoCredentialsError()).aws_code, "NoCredentialsError")
        self.assertEqual(_translate_ocr_exception(EndpointConnectionError(endpoint_url="https://textract.invalid")).status, "ocr_unavailable")
        for code in ("InvalidClientTokenId", "AccessDeniedException", "InvalidS3ObjectException"):
            error = ClientError({"Error": {"Code": code, "Message": "redacted"}}, "DetectDocumentText")
            self.assertEqual(_translate_ocr_exception(error).aws_code, code)
        unsupported = ClientError({"Error": {"Code": "UnsupportedDocumentException", "Message": "redacted"}}, "DetectDocumentText")
        self.assertEqual(_translate_ocr_exception(unsupported).status, "unreadable")

    def test_missing_ocr_configuration_is_reported_without_credentials(self):
        with patch("app.services.identity_verification_service.settings.AWS_ACCESS_KEY_ID", ""):
            health = validate_ocr_configuration()
        self.assertFalse(health["configured"])
        self.assertIn("AWS_ACCESS_KEY_ID", health["missing"])


class NicEndpointSecurityTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def request():
        return Request({
            "type": "http", "method": "POST", "path": "/api/v1/verification/nic",
            "headers": [(b"x-forwarded-proto", b"https")], "client": ("127.0.0.1", 1),
            "scheme": "http", "server": ("test", 80),
        })

    async def test_missing_authentication_is_rejected(self):
        with self.assertRaises(HTTPException) as raised:
            await get_current_user(self.request(), None)
        self.assertEqual(raised.exception.status_code, 401)

    async def test_duplicate_verified_nic_is_rejected_without_owner_disclosure(self):
        user = {"_id": ObjectId(), "role": "editor", "registration_complete": False}
        image = type("Image", (), {
            "content_type": "image/png",
            "read": AsyncMock(return_value=valid_png()),
            "close": AsyncMock(),
        })()
        with (
            patch("app.routers.identity_verification_router.enforce_rate_limit", AsyncMock()),
            patch("app.routers.identity_verification_router.validate_nic_image", return_value=(".png", 640, 480)),
            patch("app.routers.identity_verification_router.analyze_nic_front", AsyncMock(return_value={"candidate_confidences": {"200012345678": 99}, "ocr_provider": "aws_textract"})),
            patch("app.routers.identity_verification_router.upload_identity_image", AsyncMock(return_value="private/key.png")),
            patch("app.routers.identity_verification_router.editors_col.find_one", AsyncMock(side_effect=[{}, {"_id": ObjectId()}])),
            patch("app.routers.identity_verification_router.delete_identity_key", AsyncMock()),
        ):
            with self.assertRaises(HTTPException) as raised:
                await verify_entered_nic_image(self.request(), "200012345678", image, user)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertNotIn("owner", str(raised.exception.detail).lower())

    async def test_successful_front_nic_verification_enables_selfie_stage(self):
        user = {"_id": ObjectId(), "role": "editor", "registration_complete": False}
        image = type("Image", (), {
            "filename": "nic-front.png", "content_type": "image/png",
            "read": AsyncMock(return_value=valid_png()), "close": AsyncMock(),
        })()
        update_result = type("UpdateResult", (), {"modified_count": 1})()
        with (
            patch("app.routers.identity_verification_router.enforce_rate_limit", AsyncMock()),
            patch("app.routers.identity_verification_router.validate_nic_image", return_value=(".png", 640, 480)),
            patch("app.routers.identity_verification_router.analyze_nic_front", AsyncMock(return_value={
                "candidate_confidences": {"200012345678": 99}, "detected_line_count": 1,
                "ocr_provider": "aws_textract",
            })),
            patch("app.routers.identity_verification_router.upload_identity_image", AsyncMock(return_value="private/front.png")),
            patch("app.routers.identity_verification_router.editors_col.find_one", AsyncMock(side_effect=[{}, None])),
            patch("app.routers.identity_verification_router.editors_col.update_one", AsyncMock(return_value=update_result)) as editor_update,
            patch("app.routers.identity_verification_router.users_col.update_one", AsyncMock()),
            patch("app.routers.identity_verification_router.write_audit", AsyncMock()),
        ):
            response = await verify_entered_nic_image(self.request(), "200012345678", image, user)
        self.assertTrue(response["success"])
        saved_fields = editor_update.await_args.args[1]["$set"]
        self.assertTrue(saved_fields["nic_ocr_verified"])
        self.assertEqual(saved_fields["identity_verification_status"], "nic_verified")
        self.assertFalse(response["registration_allowed"])


class RegistrationGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_editor_cannot_finish_before_full_identity_verification(self):
        body = CompleteProfileRequest(
            username="Editor",
            nic="200012345678",
            district="Colombo",
            gender="Male",
            phone="0712345678",
        )
        user = {
            "_id": ObjectId(),
            "role": "editor",
            "nic": "200012345678",
            "registration_complete": False,
            "is_email_verified": True,
        }
        with (
            patch(
                "app.routers.auth_router.editors_col.find_one",
                AsyncMock(return_value={"identity_verification_status": "manual_review"}),
            ),
            patch(
                "app.routers.auth_router.users_col.update_one",
                AsyncMock(),
            ) as update,
        ):
            with self.assertRaises(HTTPException) as raised:
                await complete_profile(body, user)
            self.assertEqual(raised.exception.status_code, 409)
            update.assert_not_awaited()

    async def test_verified_editor_can_finish_registration(self):
        body = CompleteProfileRequest(
            username="Editor",
            nic="200012345678",
            district="Colombo",
            gender="Male",
            phone="0712345678",
        )
        user = {
            "_id": ObjectId(),
            "role": "editor",
            "nic": "200012345678",
            "registration_complete": False,
            "is_email_verified": True,
        }
        with (
            patch(
                "app.routers.auth_router.editors_col.find_one",
                AsyncMock(return_value={
                    "identity_verification_status": "selfie_verified",
                    "nic_ocr_verified": True,
                    "selfie_verified": True,
                }),
            ),
            patch(
                "app.routers.auth_router.users_col.update_one",
                AsyncMock(),
            ),
        ):
            response = await complete_profile(body, user)
        self.assertEqual(response["redirect_to"], "editor-dashboard")


class LiveSelfieSessionReliabilityTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def request():
        return Request({
            "type": "http", "method": "POST", "path": "/api/v1/editor/selfie/session",
            "headers": [(b"x-forwarded-proto", b"https")], "client": ("127.0.0.1", 1),
            "scheme": "http", "server": ("test", 80),
        })

    async def test_session_uses_camera_session_minutes_not_twenty_second_ttl(self):
        user = {"_id": ObjectId(), "role": "editor", "registration_complete": False}
        inserted = type("InsertResult", (), {"inserted_id": ObjectId()})()
        with (
            patch("app.routers.editor_verification_router.editors_col.find_one", AsyncMock(return_value={
                "nic_front_key": "private/front.png", "nic_ocr_verified": True,
            })),
            patch("app.routers.editor_verification_router.enforce_rate_limit", AsyncMock()),
            patch("app.routers.editor_verification_router.selfie_sessions_col.update_many", AsyncMock()),
            patch("app.routers.editor_verification_router.selfie_sessions_col.insert_one", AsyncMock(return_value=inserted)) as insert,
            patch("app.routers.editor_verification_router.settings.LIVE_SELFIE_SESSION_MINUTES", 10),
        ):
            response = await create_capture_session(self.request(), user)
        saved = insert.await_args.args[0]
        self.assertGreaterEqual((saved["expires_at"] - saved["created_at"]).total_seconds(), 600)
        self.assertEqual(response["expires_at"], saved["expires_at"])

    async def test_cleanup_failure_does_not_mask_original_selfie_error(self):
        from pymongo.errors import PyMongoError
        with patch(
            "app.routers.editor_verification_router.selfie_sessions_col.find_one",
            AsyncMock(side_effect=PyMongoError("temporary database failure")),
        ):
            await _release_session(ObjectId(), "face validation failed")

    async def test_upload_normalizes_naive_mongo_session_datetime(self):
        user = {"_id": ObjectId(), "role": "editor", "registration_complete": False}
        session_id = ObjectId()
        captured_at = datetime.now(timezone.utc)
        session = {
            "_id": session_id,
            "challenge": ["blink"],
            # Motor's default BSON decoding returns a naive UTC datetime.
            "created_at": (captured_at - timedelta(seconds=5)).replace(tzinfo=None),
        }
        image = type("Image", (), {
            "content_type": "image/png", "read": AsyncMock(return_value=valid_png()),
            "close": AsyncMock(),
        })()
        update_result = type("UpdateResult", (), {"modified_count": 1})()
        event_data = '[{"action":"blink","confidence":0.8,"completed_at":"' + captured_at.isoformat() + '"}]'
        with (
            patch("app.routers.editor_verification_router.selfie_sessions_col.find_one_and_update", AsyncMock(return_value=session)),
            patch("app.routers.editor_verification_router.validate_selfie", return_value=(".png", 640, 480)),
            patch("app.routers.editor_verification_router.verify_one_clear_face", AsyncMock()),
            patch("app.routers.editor_verification_router.editors_col.find_one", AsyncMock(return_value={
                "nic_front_key": "private/front.png", "nic_ocr_verified": True,
                "manual_review_reasons": None,
            })),
            patch("app.routers.editor_verification_router.compare_identity_faces", AsyncMock(return_value=99.0)),
            patch("app.routers.editor_verification_router.upload_selfie", AsyncMock(return_value="live-selfies/user/selfie.png")),
            patch("app.routers.editor_verification_router.editors_col.update_one", AsyncMock(return_value=update_result)),
            patch("app.routers.editor_verification_router.selfie_sessions_col.update_one", AsyncMock()),
            patch("app.routers.editor_verification_router.write_audit", AsyncMock()),
        ):
            response = await submit_live_selfie(
                self.request(), image, str(session_id), "capture-token", captured_at.isoformat(),
                1, event_data, "camera", user,
            )
        self.assertTrue(response["selfie_verified"])


if __name__ == "__main__":
    unittest.main()
