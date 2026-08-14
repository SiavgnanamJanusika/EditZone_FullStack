import logging
import struct
import unittest
from unittest.mock import AsyncMock, patch

from botocore.exceptions import ClientError, EndpointConnectionError

from app.services.aws_face_errors import AwsFaceVerificationError, raise_aws_face_error
from app.services.live_selfie_service import (
    SelfieValidationError,
    validate_selfie,
    verify_one_clear_face,
)


def client_error(code: str, message: str = "safe test message", status: int = 400) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": message}, "ResponseMetadata": {"HTTPStatusCode": status}},
        "FaceOperation",
    )


def valid_png(width: int = 640, height: int = 480) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
        + struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"
        + b"x" * 20 + b"IEND" + b"x" * 8
    )


class AwsFaceErrorTests(unittest.TestCase):
    def classify(self, exc: Exception) -> AwsFaceVerificationError:
        with self.assertRaises(AwsFaceVerificationError) as raised:
            raise_aws_face_error(exc, "Rekognition.DetectFaces", logging.getLogger(__name__))
        return raised.exception

    def test_access_denied_is_a_safe_configuration_error(self):
        error = self.classify(client_error("AccessDeniedException"))
        self.assertEqual(error.code, "FACE_VERIFICATION_NOT_CONFIGURED")
        self.assertEqual(error.status_code, 503)

    def test_invalid_reference_and_credentials_are_distinct(self):
        self.assertEqual(self.classify(client_error("InvalidS3ObjectException")).code, "INVALID_REFERENCE_IMAGE")
        self.assertEqual(self.classify(client_error("UnrecognizedClientException")).code, "AWS_CREDENTIALS_INVALID")

    def test_network_and_throttling_are_temporary(self):
        self.assertEqual(self.classify(EndpointConnectionError(endpoint_url="https://rekognition.invalid")).code, "AWS_UNAVAILABLE")
        self.assertEqual(self.classify(client_error("ThrottlingException", status=429)).code, "AWS_UNAVAILABLE")


class SelfieInputAndFaceTests(unittest.IsolatedAsyncioTestCase):
    def test_empty_malformed_and_unsupported_images_are_rejected(self):
        for contents, content_type in ((b"", "image/jpeg"), (b"not-image", "image/jpeg"), (valid_png(), "image/webp")):
            with self.assertRaises(SelfieValidationError):
                validate_selfie(contents, content_type)

    async def test_no_face_and_multiple_faces_have_distinct_codes(self):
        for expected in ("NO_FACE_DETECTED", "MULTIPLE_FACES"):
            with patch(
                "app.services.live_selfie_service.run_in_threadpool",
                AsyncMock(side_effect=SelfieValidationError("test", expected)),
            ):
                with self.assertRaises(SelfieValidationError) as raised:
                    await verify_one_clear_face(b"image")
            self.assertEqual(raised.exception.code, expected)

    async def test_access_denied_is_not_retried(self):
        aws_call = AsyncMock(side_effect=client_error("AccessDeniedException"))
        with patch("app.services.live_selfie_service.run_in_threadpool", aws_call):
            with self.assertRaises(AwsFaceVerificationError) as raised:
                await verify_one_clear_face(b"real-image-bytes")
        self.assertEqual(raised.exception.code, "FACE_VERIFICATION_NOT_CONFIGURED")
        self.assertEqual(aws_call.await_count, 1)
