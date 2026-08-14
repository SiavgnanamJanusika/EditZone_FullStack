import logging
import struct
import uuid

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.services.aws_face_errors import AwsFaceVerificationError, raise_aws_face_error

logger = logging.getLogger(__name__)

ALLOWED_SELFIE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png"}
MIN_IMAGE_DIMENSION = 320
MAX_IMAGE_DIMENSION = 4096


class SelfieValidationError(ValueError):
    def __init__(self, message: str, code: str = "INVALID_SELFIE_IMAGE"):
        super().__init__(message)
        self.code = code


class SelfieServiceError(RuntimeError):
    pass


def _aws_client(service: str):
    options = {"region_name": settings.AWS_REGION}
    if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
        options.update({
            "aws_access_key_id": settings.AWS_ACCESS_KEY_ID,
            "aws_secret_access_key": settings.AWS_SECRET_ACCESS_KEY,
        })
    return boto3.client(service, **options)


def log_face_verification_configuration() -> None:
    if settings.ENV.lower() != "development":
        return
    initialized = False
    try:
        client = _aws_client("rekognition")
        initialized = client.meta.region_name == settings.AWS_REGION
    except Exception as exc:
        logger.warning(
            "AWS face configuration client initialization failed region=%s exception_class=%s",
            settings.AWS_REGION,
            type(exc).__name__,
        )
    logger.info(
        "AWS face configuration credentials_configured=%s region=%s s3_bucket=%s "
        "rekognition_client_initialized=%s",
        bool(settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY),
        settings.AWS_REGION or "missing",
        settings.AWS_S3_BUCKET or "missing",
        initialized,
    )


def _jpeg_dimensions(contents: bytes) -> tuple[int, int]:
    position = 2
    while position + 9 < len(contents):
        if contents[position] != 0xFF:
            position += 1
            continue
        marker = contents[position + 1]
        position += 2
        if marker in {0xD8, 0xD9}:
            continue
        if position + 2 > len(contents):
            break
        segment_length = struct.unpack(">H", contents[position:position + 2])[0]
        if segment_length < 2 or position + segment_length > len(contents):
            break
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            height, width = struct.unpack(">HH", contents[position + 3:position + 7])
            return width, height
        position += segment_length
    raise SelfieValidationError("The JPEG image is malformed")


def _image_dimensions(contents: bytes, content_type: str) -> tuple[int, int]:
    if content_type == "image/png":
        if not contents.startswith(b"\x89PNG\r\n\x1a\n") or len(contents) < 24:
            raise SelfieValidationError("The uploaded file is not a valid PNG image")
        if contents[12:16] != b"IHDR" or b"IEND" not in contents[-32:]:
            raise SelfieValidationError("The PNG image is malformed or incomplete")
        return struct.unpack(">II", contents[16:24])
    if not contents.startswith(b"\xff\xd8\xff") or not contents.endswith(b"\xff\xd9"):
        raise SelfieValidationError("The uploaded file is not a valid JPEG image")
    return _jpeg_dimensions(contents)


def validate_selfie(
    contents: bytes,
    content_type: str,
    max_mb: int | None = None,
) -> tuple[str, int, int]:
    normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
    extension = ALLOWED_SELFIE_TYPES.get(normalized_type)
    if extension is None:
        raise SelfieValidationError("Live selfie must be a JPG, JPEG, or PNG image")
    if not contents:
        raise SelfieValidationError("Live selfie cannot be empty")
    size_limit_mb = max_mb or settings.SELFIE_MAX_UPLOAD_MB
    max_bytes = size_limit_mb * 1024 * 1024
    if len(contents) > max_bytes:
        raise SelfieValidationError(
            f"Image exceeds the {size_limit_mb} MB limit"
        )
    width, height = _image_dimensions(contents, normalized_type)
    if min(width, height) < MIN_IMAGE_DIMENSION:
        raise SelfieValidationError("Move closer to the camera and capture a clearer selfie")
    if max(width, height) > MAX_IMAGE_DIMENSION:
        raise SelfieValidationError("Selfie dimensions are too large")
    return extension, width, height


def _detect_one_clear_face(contents: bytes) -> None:
    response = _aws_client("rekognition").detect_faces(
        Image={"Bytes": contents},
        Attributes=["DEFAULT"],
    )
    faces = response.get("FaceDetails", [])
    if not faces:
        raise SelfieValidationError("No face detected. Center your face and try again", "NO_FACE_DETECTED")
    if len(faces) != 1:
        raise SelfieValidationError("Multiple faces detected. Only one person may be in the frame", "MULTIPLE_FACES")

    face = faces[0]
    box = face.get("BoundingBox") or {}
    quality = face.get("Quality") or {}
    pose = face.get("Pose") or {}
    if (
        face.get("Confidence", 0) < 90
        or box.get("Width", 0) < 0.15
        or box.get("Height", 0) < 0.15
        or quality.get("Brightness", 0) < 20
        or quality.get("Sharpness", 0) < 20
        or abs(pose.get("Yaw", 0)) > 35
        or abs(pose.get("Pitch", 0)) > 35
    ):
        raise SelfieValidationError(
            "One face was found, but it is not clear. Face the camera in good lighting",
            "LOW_IMAGE_QUALITY",
        )


async def verify_one_clear_face(contents: bytes) -> None:
    try:
        await run_in_threadpool(_detect_one_clear_face, contents)
    except SelfieValidationError:
        raise
    except Exception as exc:
        raise_aws_face_error(exc, "Rekognition.DetectFaces", logger)


def _put_selfie(key: str, contents: bytes, content_type: str) -> None:
    _aws_client("s3").put_object(
        Bucket=settings.AWS_S3_BUCKET,
        Key=key,
        Body=contents,
        ContentType=content_type,
        ContentLength=len(contents),
        ServerSideEncryption="AES256",
        CacheControl="no-store",
        Metadata={"purpose": "editor-live-selfie"},
    )


async def upload_selfie(
    user_id: str,
    contents: bytes,
    content_type: str,
    extension: str,
) -> str:
    if not settings.AWS_S3_BUCKET:
        raise SelfieServiceError("AWS S3 live-selfie storage is not configured")
    key = f"live-selfies/{user_id}/{uuid.uuid4().hex}{extension}"
    try:
        await run_in_threadpool(_put_selfie, key, contents, content_type)
    except Exception as exc:
        raise_aws_face_error(exc, "S3.PutObject", logger)
    return key


async def delete_selfie(selfie_key: str) -> None:
    if not settings.AWS_S3_BUCKET or not selfie_key or "/" not in selfie_key:
        return
    try:
        await run_in_threadpool(
            _aws_client("s3").delete_object,
            Bucket=settings.AWS_S3_BUCKET,
            Key=selfie_key,
        )
    except Exception:
        # Cleanup is best-effort and must not replace the original API result
        # with an unrelated internal-server error.
        logger.exception("Live selfie cleanup failed key_suffix=%s", selfie_key.rsplit("/", 1)[-1][-12:])
