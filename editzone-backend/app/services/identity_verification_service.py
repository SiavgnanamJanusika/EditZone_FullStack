import logging
import re
import uuid
import hashlib
import hmac
import struct
from io import BytesIO
from datetime import timedelta

import boto3
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    NoCredentialsError,
    PartialCredentialsError,
    ReadTimeoutError,
)
from pymongo import ReturnDocument
from starlette.concurrency import run_in_threadpool
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError

from app.config import settings
from app.services.aws_face_errors import AwsFaceVerificationError, raise_aws_face_error
from app.core.utils import now_utc
from app.db.mongodb import identity_audit_logs_col, identity_rate_limits_col, editors_col

logger = logging.getLogger(__name__)
NIC_PATTERN = re.compile(r"(?<![A-Z0-9])([0-9OISB](?:[^A-Z0-9]*[0-9OISB]){11}|[0-9OISB](?:[^A-Z0-9]*[0-9OISB]){8}[^A-Z0-9]*[VX])(?![A-Z0-9])", re.IGNORECASE)
NIC_LABEL_PATTERN = re.compile(r"\b(NIC|IDENTITY\s*CARD|IDENTITY\s*NUMBER|ID\s*NUMBER|NO)\b", re.IGNORECASE)


class IdentityValidationError(ValueError):
    pass


class IdentityServiceError(RuntimeError):
    pass


ALLOWED_NIC_TYPES = {"image/jpeg": ".jpg", "image/png": ".png"}


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
    raise IdentityValidationError("The JPEG image is malformed")


def validate_nic_image(contents: bytes, content_type: str, max_mb: int) -> tuple[str, int, int]:
    normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
    extension = ALLOWED_NIC_TYPES.get(normalized_type)
    if extension is None:
        raise IdentityValidationError("NIC image must be a JPG, JPEG, or PNG image")
    if not contents:
        raise IdentityValidationError("NIC image cannot be empty")
    if len(contents) > max_mb * 1024 * 1024:
        raise IdentityValidationError(f"Image exceeds the {max_mb} MB limit")
    if normalized_type == "image/png":
        if not contents.startswith(b"\x89PNG\r\n\x1a\n") or len(contents) < 24:
            raise IdentityValidationError("The uploaded file is not a valid PNG image")
        if contents[12:16] != b"IHDR" or b"IEND" not in contents[-32:]:
            raise IdentityValidationError("The PNG image is malformed or incomplete")
        width, height = struct.unpack(">II", contents[16:24])
    else:
        if not contents.startswith(b"\xff\xd8\xff") or not contents.endswith(b"\xff\xd9"):
            raise IdentityValidationError("The uploaded file is not a valid JPEG image")
        width, height = _jpeg_dimensions(contents)
    if min(width, height) < 320:
        raise IdentityValidationError("NIC image quality is too low; upload a clearer image")
    if max(width, height) > 12000:
        raise IdentityValidationError("NIC image dimensions are too large")
    return extension, width, height


class OcrProviderError(IdentityServiceError):
    def __init__(self, aws_code: str, internal_reason: str, status: str = "ocr_unavailable", message: str | None = None):
        super().__init__(message or "NIC verification service is temporarily unavailable. Please try again later.")
        self.aws_code = aws_code
        self.internal_reason = internal_reason
        self.status = status


def aws_client(service: str):
    region = settings.AWS_TEXTRACT_REGION or settings.AWS_REGION if service == "textract" else settings.AWS_REGION
    options = {"region_name": region}
    if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
        options.update({
            "aws_access_key_id": settings.AWS_ACCESS_KEY_ID,
            "aws_secret_access_key": settings.AWS_SECRET_ACCESS_KEY,
        })
    return boto3.client(service, **options)


def validate_ocr_configuration() -> dict:
    provider = settings.NIC_OCR_PROVIDER.strip().lower()
    missing = []
    if provider != "aws_textract":
        return {"provider": provider or "unconfigured", "configured": False, "available": False, "reason": "unsupported_provider"}
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION", "AWS_S3_BUCKET_NAME"):
        if name == "AWS_S3_BUCKET_NAME" and settings.AWS_S3_BUCKET:
            continue
        if not str(getattr(settings, name, "")).strip():
            missing.append(name)
    region = (settings.AWS_TEXTRACT_REGION or settings.AWS_REGION).strip()
    if not region:
        missing.append("AWS_TEXTRACT_REGION")
    return {
        "provider": provider,
        "configured": not missing,
        "available": False,
        "region": region or None,
        "missing": missing,
        "check": "configuration_only",
    }


def _probe_ocr_credentials() -> None:
    options = {
        "region_name": settings.AWS_TEXTRACT_REGION or settings.AWS_REGION,
        "config": Config(connect_timeout=3, read_timeout=3, retries={"max_attempts": 0}),
    }
    if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
        options.update({"aws_access_key_id": settings.AWS_ACCESS_KEY_ID, "aws_secret_access_key": settings.AWS_SECRET_ACCESS_KEY})
    boto3.client("sts", **options).get_caller_identity()


async def probe_ocr_health() -> dict:
    health = validate_ocr_configuration()
    if not health["configured"]:
        return health
    try:
        await run_in_threadpool(_probe_ocr_credentials)
        return {**health, "available": True, "check": "aws_credentials"}
    except (BotoCoreError, ClientError) as exc:
        error = _translate_ocr_exception(exc)
        logger.warning("NIC OCR health probe failed provider=aws_textract stage=credentials aws_code=%s", error.aws_code)
        return {**health, "available": False, "check": "aws_credentials", "error_code": error.aws_code}


def _compare_faces(nic_front_key: str, selfie: bytes) -> float:
    # Confirm that the exact private reference key is readable before asking
    # Rekognition to compare it. This distinguishes S3 reference failures from
    # Rekognition IAM failures in development logs.
    try:
        aws_client("s3").head_object(Bucket=settings.AWS_S3_BUCKET, Key=nic_front_key)
    except Exception as exc:
        raise_aws_face_error(exc, "S3.HeadObject", logger)
    response = aws_client("rekognition").compare_faces(
        SourceImage={"S3Object": {"Bucket": settings.AWS_S3_BUCKET, "Name": nic_front_key}},
        TargetImage={"Bytes": selfie},
        SimilarityThreshold=0,
        QualityFilter="AUTO",
    )
    return max((float(match.get("Similarity", 0)) for match in response.get("FaceMatches", [])), default=0.0)


async def compare_identity_faces(nic_front_key: str, selfie: bytes) -> float:
    logger.info("Identity verification stage=rekognition_comparison_started")
    try:
        score = await run_in_threadpool(_compare_faces, nic_front_key, selfie)
        logger.info("Identity verification stage=similarity_result_received result_bucket=%s", "pass" if score >= settings.AWS_REKOGNITION_SIMILARITY_THRESHOLD else "below_threshold")
        return score
    except AwsFaceVerificationError:
        raise
    except Exception as exc:
        raise_aws_face_error(exc, "Rekognition.CompareFaces", logger)


def _translate_ocr_exception(exc: Exception) -> OcrProviderError:
    if isinstance(exc, NoCredentialsError):
        return OcrProviderError("NoCredentialsError", "AWS credentials are missing")
    if isinstance(exc, PartialCredentialsError):
        return OcrProviderError("PartialCredentialsError", "AWS credentials are incomplete")
    if isinstance(exc, (ConnectTimeoutError, ReadTimeoutError, EndpointConnectionError)):
        return OcrProviderError(type(exc).__name__, "AWS Textract network request failed")
    if isinstance(exc, ClientError):
        code = str(exc.response.get("Error", {}).get("Code") or "ClientError")
        if code == "UnsupportedDocumentException":
            return OcrProviderError(code, "Textract rejected the image format", "unreadable", "The NIC image could not be read clearly. Please upload a clearer image.")
        reasons = {
            "InvalidClientTokenId": "AWS access key is invalid",
            "UnrecognizedClientException": "AWS credentials were not recognized",
            "ExpiredToken": "AWS credentials have expired",
            "AccessDeniedException": "IAM denied Textract DetectDocumentText",
            "InvalidS3ObjectException": "Textract could not access the S3 object",
            "UnsupportedDocumentException": "Textract rejected the image format",
            "ProvisionedThroughputExceededException": "Textract capacity was exceeded",
            "ThrottlingException": "Textract throttled the request",
        }
        return OcrProviderError(code, reasons.get(code, "AWS Textract request failed"))
    return OcrProviderError(type(exc).__name__, "AWS Textract SDK request failed")


async def write_audit(user_id, event: str, outcome: str, metadata: dict | None = None) -> None:
    safe_metadata = {
        key: value for key, value in (metadata or {}).items()
        if key not in {"nic", "text", "capture_token", "image"}
    }
    try:
        await identity_audit_logs_col.insert_one({
            "user_id": user_id,
            "event": event,
            "outcome": outcome,
            "metadata": safe_metadata,
            "created_at": now_utc(),
        })
    except Exception:
        logger.exception("Could not write identity audit event %s for %s", event, user_id)


async def enforce_rate_limit(user_id, action: str) -> None:
    now = now_utc()
    minutes = settings.IDENTITY_RATE_LIMIT_MINUTES
    window_start = now.replace(second=0, microsecond=0)
    window_start -= timedelta(minutes=window_start.minute % minutes)
    window_key = window_start.isoformat()
    record = await identity_rate_limits_col.find_one_and_update(
        {"user_id": user_id, "action": action, "window_key": window_key},
        {
            "$inc": {"count": 1},
            "$setOnInsert": {
                "created_at": now,
                "expires_at": window_start + timedelta(minutes=minutes * 2),
            },
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    if record["count"] > settings.IDENTITY_RATE_LIMIT_ATTEMPTS:
        await write_audit(user_id, action, "rate_limited")
        raise IdentityValidationError(
            f"Too many verification attempts. Try again in {minutes} minutes"
        )


def extract_nic_candidates(blocks: list[dict]) -> tuple[set[str], float]:
    details = extract_nic_candidate_details(blocks)
    return set(details), max(details.values(), default=0.0)


def normalize_nic(value: str) -> str:
    normalized = re.sub(r"[^A-Z0-9]", "", str(value or "").upper())
    if not re.fullmatch(r"(?:\d{12}|\d{9}[VX])", normalized):
        raise IdentityValidationError("Enter a valid Sri Lankan NIC number")
    return normalized


def nic_hash(value: str) -> str:
    normalized = normalize_nic(value)
    return hmac.new(settings.JWT_SECRET_KEY.encode(), normalized.encode(), hashlib.sha256).hexdigest()


def extract_nic_candidate_details(blocks: list[dict]) -> dict[str, float]:
    candidates: dict[str, float] = {}
    text_blocks = [
        (str(block.get("Text", "")), float(block.get("Confidence", 0)))
        for block in blocks if block.get("BlockType") in {"LINE", "WORD"}
    ]

    def collect(original: str, confidence: float) -> None:
        for match in NIC_PATTERN.findall(original.upper()):
            compact = re.sub(r"[^A-Z0-9]", "", match.upper())
            # OCR substitutions are intentionally limited to digit positions.
            if len(compact) == 10 and compact[-1] in "VX":
                normalized = compact[:9].translate(str.maketrans("OISB", "0158")) + compact[-1]
            elif len(compact) == 12:
                normalized = compact.translate(str.maketrans("OISB", "0158"))
            else:
                continue
            if not re.fullmatch(r"(?:\d{12}|\d{9}[VX])", normalized):
                continue
            candidates[normalized] = max(candidates.get(normalized, 0), confidence)

    for index, (text, confidence) in enumerate(text_blocks):
        collect(text, confidence)
        if index + 1 < len(text_blocks):
            next_text, next_confidence = text_blocks[index + 1]
            combined = f"{text} {next_text}"
            collect(combined, min(confidence, next_confidence))
    return candidates


def _preprocess_nic_image(contents: bytes, content_type: str) -> bytes:
    """Create an OCR copy; the original remains the privately stored evidence."""
    try:
        with Image.open(BytesIO(contents)) as source:
            source.verify()
        with Image.open(BytesIO(contents)) as source:
            image = ImageOps.exif_transpose(source).convert("L")
            image.thumbnail((3000, 3000), Image.Resampling.LANCZOS)
            image = ImageOps.autocontrast(image, cutoff=1)
            image = ImageEnhance.Contrast(image).enhance(1.25)
            image = image.filter(ImageFilter.UnsharpMask(radius=1.5, percent=140, threshold=2))
            output = BytesIO()
            image.save(output, format="PNG", optimize=True)
            return output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise IdentityValidationError("Uploaded image quality is too low or the file is invalid") from exc


def _detect_nic_text(front: bytes) -> dict:
    return aws_client("textract").detect_document_text(Document={"Bytes": front})


async def analyze_nic_front(front: bytes, content_type: str = "application/octet-stream") -> dict:
    health = validate_ocr_configuration()
    if not health["configured"]:
        error = OcrProviderError("ConfigurationError", f"Missing OCR configuration: {','.join(health['missing'])}", message="AWS Textract configuration error.")
        logger.error("NIC OCR failed provider=%s stage=configuration aws_code=%s operation=DetectDocumentText", health["provider"], error.aws_code)
        raise error
    try:
        prepared = await run_in_threadpool(_preprocess_nic_image, front, content_type)
        logger.info("NIC verification stage=textract_request_sent provider=aws_textract file_type=%s original_size=%d processed_size=%d", content_type, len(front), len(prepared))
        response = await run_in_threadpool(_detect_nic_text, prepared)
        details = extract_nic_candidate_details(response.get("Blocks", []))
        line_count = sum(1 for block in response.get("Blocks", []) if block.get("BlockType") == "LINE" and str(block.get("Text", "")).strip())
        response_metadata = response.get("ResponseMetadata", {})
        logger.debug(
            "NIC verification stage=text_extracted textract_response={request_id:%s,http_status:%s,block_count:%d,line_count:%d} (raw OCR text omitted)",
            response_metadata.get("RequestId", "unknown"), response_metadata.get("HTTPStatusCode", "unknown"),
            len(response.get("Blocks", [])), line_count,
        )
        logger.info("NIC verification stage=nic_candidate_detected candidate_count=%d candidates=%s", len(details), [mask_nic(value) for value in details])
        return {"candidate_confidences": details, "detected_line_count": line_count, "ocr_provider": "aws_textract"}
    except (BotoCoreError, ClientError) as exc:
        error = _translate_ocr_exception(exc)
        logger.error(
            "NIC OCR failed provider=aws_textract stage=detect_text aws_code=%s operation=DetectDocumentText file_type=%s file_size=%d s3_key=none reason=%s",
            error.aws_code, content_type, len(front), error.internal_reason,
            exc_info=True,
        )
        raise error from exc


def classify_nic_match(entered_nic: str, candidate_confidences: dict[str, float], detected_line_count: int | None = None) -> dict:
    normalized = normalize_nic(entered_nic)
    candidates = set(candidate_confidences)
    confidence = max(candidate_confidences.values(), default=0.0)
    matched = normalized in candidates
    logger.info("NIC verification stage=nic_compared entered=%s candidate_count=%d matched=%s", mask_nic(normalized), len(candidates), matched)
    if not candidates:
        if detected_line_count:
            return {"status": "nic_not_found", "matched": False, "confidence": 0.0, "message": "NIC number not found in the uploaded image.", "reason": "OCR text did not contain a valid NIC number"}
        return {"status": "unreadable", "matched": False, "confidence": 0.0, "message": "Uploaded image quality is too low.", "reason": "OCR did not detect readable text"}
    if not matched:
        return {"status": "mismatch", "matched": False, "confidence": confidence, "message": "Entered NIC number does not match the NIC image.", "reason": "Entered NIC and OCR candidate do not match"}
    confidence = candidate_confidences[normalized]
    if confidence < settings.AWS_TEXTRACT_MIN_CONFIDENCE:
        return {"status": "manual_review_required", "matched": False, "confidence": confidence, "message": "The NIC number was detected with low confidence. Manual review is required.", "reason": "OCR confidence is below the automatic verification threshold"}
    return {"status": "verified", "matched": True, "confidence": confidence, "message": "NIC number successfully verified.", "reason": None}


def _put_private_object(key: str, contents: bytes, content_type: str, purpose: str) -> None:
    aws_client("s3").put_object(
        Bucket=settings.AWS_S3_BUCKET,
        Key=key,
        Body=contents,
        ContentType=content_type,
        ContentLength=len(contents),
        ServerSideEncryption="AES256",
        CacheControl="no-store",
        Metadata={"purpose": purpose},
    )


async def upload_identity_image(
    user_id: str,
    side: str,
    contents: bytes,
    content_type: str,
    extension: str,
) -> str:
    if not settings.AWS_S3_BUCKET:
        raise IdentityServiceError("AWS S3 identity storage is not configured")
    key = f"editzone/identity-documents/{user_id}/{side}-{uuid.uuid4().hex}{extension}"
    try:
        await run_in_threadpool(
            _put_private_object, key, contents, content_type, f"editor-nic-{side}"
        )
    except (BotoCoreError, ClientError) as exc:
        aws_code = str(exc.response.get("Error", {}).get("Code", "ClientError")) if isinstance(exc, ClientError) else type(exc).__name__
        logger.error(
            "NIC storage failed provider=aws_s3 stage=put_object aws_code=%s operation=PutObject file_type=%s file_size=%d s3_key=%s",
            aws_code, content_type, len(contents), key, exc_info=True,
        )
        raise IdentityServiceError("NIC images could not be stored securely") from exc
    return key


async def delete_identity_key(key: str) -> bool:
    if not settings.AWS_S3_BUCKET or not key:
        return not key
    try:
        await run_in_threadpool(
            aws_client("s3").delete_object,
            Bucket=settings.AWS_S3_BUCKET,
            Key=key,
        )
        return True
    except (BotoCoreError, ClientError):
        logger.exception("Could not clean up private identity object %s", key)
        return False


async def create_private_review_url(key: str) -> str:
    if not settings.AWS_S3_BUCKET or not key:
        raise IdentityServiceError("Identity image is unavailable")
    try:
        return await run_in_threadpool(
            aws_client("s3").generate_presigned_url,
            "get_object",
            Params={
                "Bucket": settings.AWS_S3_BUCKET,
                "Key": key,
                "ResponseCacheControl": "no-store",
            },
            ExpiresIn=300,
        )
    except (BotoCoreError, ClientError) as exc:
        logger.exception("Could not create private identity review URL")
        raise IdentityServiceError("Identity image is temporarily unavailable") from exc


def mask_nic(value: str) -> str:
    value = (value or "").strip().upper()
    if len(value) <= 4:
        return "•" * len(value)
    return f"{'•' * (len(value) - 4)}{value[-4:]}"


async def purge_expired_identity_documents() -> int:
    cutoff = now_utc() - timedelta(days=settings.IDENTITY_DOCUMENT_RETENTION_DAYS)
    query = {"identity_verification_status": {"$in": ["selfie_verified", "verified"]}, "identity_documents_deleted_at": {"$exists": False}, "$or": [{"identity_reviewed_at": {"$lt": cutoff}}, {"selfie_verified_at": {"$lt": cutoff}}]}
    deleted = 0
    async for profile in editors_col.find(query, {"nic_front_key": 1, "selfie_s3_key": 1, "user_id": 1}):
        keys = [profile.get("nic_front_key"), profile.get("selfie_s3_key")]
        for key in filter(None, keys):
            await delete_identity_key(key)
        await editors_col.update_one({"_id": profile["_id"], "identity_documents_deleted_at": {"$exists": False}}, {"$set": {"identity_documents_deleted_at": now_utc()}, "$unset": {"nic_front_key": "", "selfie_s3_key": ""}})
        await write_audit(profile["user_id"], "identity_retention_purge", "deleted", {"document_count": len(list(filter(None, keys)))})
        deleted += 1
    return deleted
