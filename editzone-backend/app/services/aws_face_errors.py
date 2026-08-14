import logging

from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    NoCredentialsError,
    PartialCredentialsError,
    ReadTimeoutError,
)

from app.config import settings


class AwsFaceVerificationError(RuntimeError):
    """A safe, structured AWS face-verification failure for the API layer."""

    def __init__(self, code: str, message: str, status_code: int = 503):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def raise_aws_face_error(exc: Exception, operation: str, logger: logging.Logger) -> None:
    error_code = type(exc).__name__
    error_message = str(exc)
    http_status = None
    if isinstance(exc, ClientError):
        error = exc.response.get("Error", {})
        error_code = str(error.get("Code") or error_code)
        error_message = str(error.get("Message") or error_message)
        http_status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    access_denied = error_code in {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation"}
    diagnostic_code = "AWS_REKOGNITION_ACCESS_DENIED" if access_denied and operation.startswith("Rekognition.") else error_code

    # These fields are deliberately limited to safe operational metadata.
    logger.exception(
        "AWS face verification failed operation=%s region=%s exception_class=%s "
        "error_code=%s classification=%s aws_message=%s http_status=%s",
        operation,
        settings.AWS_REGION,
        type(exc).__name__,
        error_code,
        diagnostic_code,
        error_message[:500],
        http_status,
    )

    if isinstance(exc, (NoCredentialsError, PartialCredentialsError)) or error_code in {
        "InvalidClientTokenId", "InvalidSignatureException", "UnrecognizedClientException",
        "ExpiredToken", "ExpiredTokenException",
    }:
        raise AwsFaceVerificationError(
            "AWS_CREDENTIALS_INVALID",
            "Face verification service credentials are invalid or expired. Please contact support.",
        ) from exc
    if access_denied:
        raise AwsFaceVerificationError(
            "FACE_VERIFICATION_NOT_CONFIGURED",
            "Face verification is not configured correctly. Please contact support.",
        ) from exc
    if error_code in {"InvalidS3ObjectException", "NoSuchKey", "NotFound", "ResourceNotFoundException"}:
        raise AwsFaceVerificationError(
            "INVALID_REFERENCE_IMAGE",
            "The verified NIC reference image is unavailable. Upload the NIC image again.",
            409,
        ) from exc
    if error_code in {"InvalidImageFormatException", "ImageTooLargeException", "InvalidParameterException"}:
        raise AwsFaceVerificationError(
            "INVALID_SELFIE_IMAGE",
            "AWS could not read the captured image. Capture a clear JPG or PNG selfie.",
            400,
        ) from exc
    if isinstance(exc, (ConnectTimeoutError, ReadTimeoutError, EndpointConnectionError, BotoCoreError)) or error_code in {
        "ProvisionedThroughputExceededException", "Throttling", "ThrottlingException",
        "InternalServerError", "ServiceUnavailable",
    }:
        raise AwsFaceVerificationError(
            "AWS_UNAVAILABLE",
            "Face verification service is temporarily unavailable. Please try again.",
        ) from exc
    raise AwsFaceVerificationError(
        "FACE_VERIFICATION_FAILED",
        "Face verification could not be completed. Please try again.",
    ) from exc
