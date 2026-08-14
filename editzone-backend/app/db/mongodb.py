import logging
import time as stdlib_time

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket
from bson import ObjectId
from datetime import datetime, time, timedelta, timezone
from pymongo import monitoring
from pymongo.errors import (
    AutoReconnect,
    ConfigurationError,
    ConnectionFailure,
    OperationFailure,
    PyMongoError,
)

from app.config import settings
from app.core.utils import now_utc

logger = logging.getLogger(__name__)
startup_logger = logging.getLogger("uvicorn.error")


class _MongoStartupCommandLogger(monitoring.CommandListener):
    """Make index startup failures attributable without logging credentials."""

    def started(self, event):
        if event.command_name == "createIndexes":
            specs = event.command.get("indexes", [])
            names = ", ".join(spec.get("name", "<unnamed>") for spec in specs)
            startup_logger.info("Creating index: %s(%s)", event.command.get("createIndexes"), names)

    def succeeded(self, event):
        if event.command_name == "createIndexes":
            startup_logger.info("Index created successfully")

    def failed(self, event):
        if event.command_name == "createIndexes":
            startup_logger.error("MongoDB createIndexes command failed: %s", event.failure)


def _create_mongo_client() -> AsyncIOMotorClient:
    """Create the process-wide Motor client with bounded network behavior.

    Motor/PyMongo clients are thread-safe, maintain their own connection pool,
    and are intentionally created once. SRV parsing can resolve DNS in the
    constructor; server discovery is then forced by lifespan ``ping``.
    """
    attempts = settings.MONGO_STARTUP_RETRIES if settings.MONGO_URI.startswith("mongodb+srv://") else 1
    for attempt in range(1, attempts + 1):
        try:
            return AsyncIOMotorClient(
                settings.MONGO_URI,
                appname=settings.APP_NAME,
                connectTimeoutMS=settings.MONGO_CONNECT_TIMEOUT_MS,
                serverSelectionTimeoutMS=settings.MONGO_SERVER_SELECTION_TIMEOUT_MS,
                socketTimeoutMS=settings.MONGO_SOCKET_TIMEOUT_MS,
                maxPoolSize=settings.MONGO_MAX_POOL_SIZE,
                minPoolSize=settings.MONGO_MIN_POOL_SIZE,
                waitQueueTimeoutMS=settings.MONGO_WAIT_QUEUE_TIMEOUT_MS,
                retryReads=True,
                retryWrites=True,
                event_listeners=[_MongoStartupCommandLogger()],
            )
        except ConfigurationError as exc:
            if attempt == attempts:
                raise RuntimeError(
                    "MongoDB client initialization failed: DNS/SRV lookup failed; "
                    f"verify the Atlas hostname and Ubuntu DNS ({exc})"
                ) from exc
            logger.warning(
                "MongoDB SRV DNS lookup failed (attempt %s/%s): %s; retrying",
                attempt,
                attempts,
                exc,
            )
            stdlib_time.sleep(attempt)

    raise RuntimeError("MongoDB client initialization exhausted all attempts")


client: AsyncIOMotorClient = _create_mongo_client()
db = client[settings.MONGO_DB_NAME]
uploads_bucket = AsyncIOMotorGridFSBucket(db, bucket_name="uploads")

# Collections
users_col = db["users"]
editors_col = db["editors"]
requests_col = db["requests"]
messages_col = db["messages"]
payments_col = db["payments"]
reviews_col = db["reviews"]
notifications_col = db["notifications"]
push_subscriptions_col = db["push_subscriptions"]
otps_col = db["otps"]
disputes_col = db["disputes"]
chat_reports_col = db["chat_reports"]
chat_moderation_logs_col = db["chat_moderation_logs"]
chat_audit_logs_col = db["chat_audit_logs"]
content_col = db["content"]
identity_rate_limits_col = db["identity_rate_limits"]
identity_audit_logs_col = db["identity_audit_logs"]
selfie_sessions_col = db["selfie_sessions"]
auth_rate_limits_col = db["auth_rate_limits"]
auth_sessions_col = db["auth_sessions"]
auth_security_events_col = db["auth_security_events"]
account_deletion_audit_logs_col = db["account_deletion_audit_logs"]
# Legacy audit data only. No current business decision reads or writes it.
subscription_usage_col = db["subscription_usage"]
worker_heartbeats_col = db["worker_heartbeats"]
failed_jobs_col = db["failed_jobs"]
media_access_logs_col = db["media_access_logs"]
media_agreements_col = db["media_agreements"]
media_reports_col = db["media_reports"]
multipart_uploads_col = db["multipart_uploads"]
payment_ledger_col = db["payment_ledger"]
payment_webhooks_col = db["payment_webhooks"]
payment_escrows_col = db["payment_escrows"]
deliveries_col = db["deliveries"]
editor_payouts_col = db["editor_payouts"]
project_quotes_col = db["project_quotes"]
editor_statuses_col = db["editor_statuses"]
editor_portfolio_items_col = db["editor_portfolio_items"]
status_likes_col = db["status_likes"]
status_views_col = db["status_views"]


def _default_editor_profile(user_id: ObjectId, created_at=None) -> dict:
    return {
        "user_id": user_id,
        "bio": "",
        "skills": [],
        "hourly_rate": 0,
        "location": "",
        "portfolio_links": [],
        "profile_picture": "",
        "category": "Video Editor",
        "rating_avg": 0,
        "rating_count": 0,
        "total_views": 0,
        "is_available": True,
        "identity_verification_status": "not_started",
        "created_at": created_at or now_utc(),
    }


async def repair_editor_profiles() -> dict:
    """Normalize legacy editor data without deleting the source documents."""
    normalized_ids = 0
    normalized_fields = 0
    migrated_legacy_profiles = 0
    created_profiles = 0

    async for profile in editors_col.find({"user_id": {"$type": "string"}}):
        raw_user_id = profile.get("user_id")
        if not ObjectId.is_valid(raw_user_id):
            continue
        user_id = ObjectId(raw_user_id)
        user = await users_col.find_one({"_id": user_id, "role": "editor"}, {"_id": 1})
        duplicate = await editors_col.find_one({"user_id": user_id}, {"_id": 1})
        if user and not duplicate:
            result = await editors_col.update_one(
                {"_id": profile["_id"], "user_id": raw_user_id},
                {"$set": {"user_id": user_id}},
            )
            normalized_ids += result.modified_count

    legacy_profiles_col = db["editor_profiles"]
    async for legacy in legacy_profiles_col.find({}):
        raw_user_id = legacy.get("user_id")
        if isinstance(raw_user_id, str) and ObjectId.is_valid(raw_user_id):
            user_id = ObjectId(raw_user_id)
        elif isinstance(raw_user_id, ObjectId):
            user_id = raw_user_id
        else:
            continue
        user = await users_col.find_one({"_id": user_id, "role": "editor"}, {"_id": 1})
        existing = await editors_col.find_one({"user_id": user_id}, {"_id": 1})
        if not user or existing:
            continue

        categories = legacy.get("categories") or []
        profile = _default_editor_profile(user_id, legacy.get("created_at"))
        profile.update({
            "bio": legacy.get("bio", ""),
            "skills": legacy.get("skills") or legacy.get("software") or [],
            "hourly_rate": legacy.get("hourly_rate", 0),
            "location": legacy.get("location", ""),
            "portfolio_links": legacy.get("portfolio") or legacy.get("reels") or [],
            "profile_picture": legacy.get("profile_picture", ""),
            "category": categories[0] if categories else "Video Editor",
            "rating_avg": legacy.get("rating", 0),
            "rating_count": legacy.get("review_count", 0),
            "total_views": legacy.get("viewer_count", 0),
            "updated_at": legacy.get("updated_at"),
            "legacy_profile_id": legacy["_id"],
        })
        await editors_col.insert_one(profile)
        migrated_legacy_profiles += 1

    async for user in users_col.find(
        {"role": "editor", "is_deleted": {"$ne": True}, "status": {"$ne": "deleted"}},
        {"_id": 1, "created_at": 1},
    ):
        if not await editors_col.find_one({"user_id": user["_id"]}, {"_id": 1}):
            await editors_col.insert_one(
                _default_editor_profile(user["_id"], user.get("created_at"))
            )
            created_profiles += 1

    category_aliases = {
        "video editors": "Video Editor",
        "image editors": "Image Editor",
        "tiktok editors": "TikTok Editor",
    }
    async for profile in editors_col.find({"deleted": {"$ne": True}, "is_deleted": {"$ne": True}, "status": {"$ne": "deleted"}}):
        updates = {}
        category = profile.get("category")
        if isinstance(category, str) and category.strip().lower() in category_aliases:
            updates["category"] = category_aliases[category.strip().lower()]
        elif not category:
            updates["category"] = "Video Editor"
        if "rating_avg" not in profile:
            updates["rating_avg"] = profile.get("rating", 0)
        if "rating_count" not in profile:
            legacy_count = profile.get("reviews", 0)
            updates["rating_count"] = legacy_count if isinstance(legacy_count, int) else 0
        if "portfolio_links" not in profile:
            updates["portfolio_links"] = profile.get("portfolio") or profile.get("reels") or []
        if "total_views" not in profile:
            updates["total_views"] = profile.get("viewer_count", 0)
        if "is_available" not in profile:
            updates["is_available"] = True
        if "identity_verification_status" not in profile:
            updates["identity_verification_status"] = "verified" if profile.get("nic_ocr_verified") else "not_started"
        if updates:
            result = await editors_col.update_one({"_id": profile["_id"]}, {"$set": updates})
            normalized_fields += result.modified_count

    return {
        "normalized_ids": normalized_ids,
        "normalized_fields": normalized_fields,
        "migrated_legacy_profiles": migrated_legacy_profiles,
        "created_profiles": created_profiles,
    }


def describe_mongodb_error(exc: BaseException) -> str:
    """Return an actionable, credential-safe classification for startup."""
    message = str(exc)
    lowered = message.lower()
    if isinstance(exc, ConfigurationError) or any(
        marker in lowered for marker in ("dns", "name or service not known", "resolution lifetime", "srv")
    ):
        cause = "DNS/SRV failure; verify the mongodb+srv hostname and Ubuntu DNS resolution"
    elif isinstance(exc, OperationFailure) and (getattr(exc, "code", None) in {13, 18} or "auth" in lowered):
        cause = "authentication failure; verify the Atlas database user, password encoding, and authSource"
    elif any(marker in lowered for marker in ("ssl", "tls", "certificate")):
        cause = "TLS failure; verify the system CA certificates, clock, and Atlas TLS requirements"
    elif any(marker in lowered for marker in ("server selection timeout", "timed out", "timeout")):
        cause = "connection timeout; verify Atlas Network Access/IP allowlist, DNS, firewall, and cluster availability"
    elif isinstance(exc, ConnectionFailure):
        cause = "network connection failure; Atlas IP allowlist/firewall or an interrupted socket is likely"
    elif isinstance(exc, OperationFailure) and getattr(exc, "code", None) in {85, 86}:
        cause = "index definition conflict; inspect the named existing index before changing or dropping it"
    else:
        cause = "MongoDB operation failure"
    return f"{cause} ({type(exc).__name__}: {message})"


async def ensure_indexes():
    await client.admin.command("ping")
    startup_logger.info("MongoDB connected successfully")
    await repair_editor_profiles()
    await users_col.create_index("email", unique=True)
    await users_col.create_index("google_id", unique=True, sparse=True)
    await users_col.create_index("email_hash", unique=True, sparse=True)
    await users_col.create_index("google_id_hash", unique=True, sparse=True)
    await users_col.create_index("nic", unique=True, sparse=True)
    await users_col.create_index([("role", 1), ("is_deleted", 1), ("status", 1)])
    await editors_col.create_index("user_id", unique=True)
    await editors_col.create_index("nic_hash", unique=True, sparse=True)
    await editors_col.create_index([("category", 1), ("hourly_rate", 1)])
    await editors_col.create_index([("is_available", 1), ("is_deleted", 1), ("status", 1)])
    await requests_col.create_index([("user_id", 1), ("created_at", -1)])
    await requests_col.create_index([("user_id", 1), ("status", 1), ("responded_at", -1)])
    await requests_col.create_index([("editor_user_id", 1), ("status", 1), ("responded_at", -1)])
    await requests_col.create_index([("editor_id", 1)])
    await requests_col.create_index([("status", 1), ("created_at", -1)])
    await messages_col.create_index([("request_id", 1), ("created_at", 1)])
    await messages_col.create_index([("sender_id", 1), ("created_at", -1)])
    await ensure_message_idempotency_index(messages_col)
    await payments_col.create_index([("request_id", 1)])
    await payments_col.create_index("order_id", unique=True, sparse=True)
    await payments_col.create_index(
        "payment_id",
        unique=True,
        partialFilterExpression={"payment_id": {"$gt": ""}},
    )
    await payments_col.create_index("active_request_key", unique=True, sparse=True)
    await reviews_col.create_index([("request_id", 1)], unique=True)
    await notifications_col.create_index([("user_id", 1), ("created_at", -1)])
    await notifications_col.create_index([("user_id", 1), ("is_read", 1)])
    await push_subscriptions_col.create_index("endpoint", unique=True)
    await push_subscriptions_col.create_index([("user_id", 1), ("active", 1)])
    await disputes_col.create_index([("status", 1), ("created_at", -1)])
    await chat_reports_col.create_index([("status", 1), ("created_at", -1)])
    await chat_reports_col.create_index("dedupe_key", unique=True, sparse=True)
    await chat_moderation_logs_col.create_index([("user_id", 1), ("created_at", -1)])
    await chat_moderation_logs_col.create_index([("request_id", 1), ("created_at", -1)])
    await chat_audit_logs_col.create_index([("request_id", 1), ("created_at", -1)])
    await messages_col.create_index([("request_id", 1), ("receiver_id", 1), ("read_at", 1)])
    await content_col.create_index("slug", unique=True)
    await identity_rate_limits_col.create_index("expires_at", expireAfterSeconds=0)
    await identity_rate_limits_col.create_index(
        [("user_id", 1), ("action", 1), ("window_key", 1)], unique=True
    )
    await identity_audit_logs_col.create_index([("user_id", 1), ("created_at", -1)])
    await selfie_sessions_col.create_index("expires_at", expireAfterSeconds=0)
    await selfie_sessions_col.create_index([("user_id", 1), ("status", 1), ("created_at", -1)])
    await auth_rate_limits_col.create_index("expires_at", expireAfterSeconds=0)
    await auth_rate_limits_col.create_index([("key", 1), ("action", 1), ("window", 1)], unique=True)
    await auth_sessions_col.create_index("session_id", unique=True)
    await auth_sessions_col.create_index("refresh_token_hash", unique=True)
    await auth_sessions_col.create_index([("user_id", 1), ("token_family", 1)])
    await auth_sessions_col.create_index("expires_at", expireAfterSeconds=0)
    await auth_security_events_col.create_index([("user_id", 1), ("created_at", -1)])
    await account_deletion_audit_logs_col.create_index([("account_id", 1), ("deleted_at", -1)])
    await worker_heartbeats_col.create_index("worker", unique=True)
    await failed_jobs_col.create_index([("status", 1), ("created_at", -1)])
    await media_access_logs_col.create_index([("request_id", 1), ("created_at", -1)])
    await media_agreements_col.create_index([("request_id", 1), ("user_id", 1)], unique=True)
    await media_reports_col.create_index([("request_id", 1), ("created_at", -1)])
    await multipart_uploads_col.create_index("upload_id", unique=True)
    multipart_indexes = await multipart_uploads_col.index_information()
    multipart_expiry = multipart_indexes.get("expires_at_1")
    if multipart_expiry and "expireAfterSeconds" in multipart_expiry:
        await multipart_uploads_col.drop_index("expires_at_1")
    await multipart_uploads_col.create_index("expires_at")
    await payment_ledger_col.create_index([("payment_id", 1), ("sequence", 1)], unique=True)
    await payment_ledger_col.create_index("dedupe_key", unique=True, sparse=True)
    await payment_ledger_col.create_index([("editor_user_id", 1), ("created_at", -1)])
    await payment_webhooks_col.create_index("event_key", unique=True)
    await payment_webhooks_col.create_index("received_at", expireAfterSeconds=60 * 60 * 24 * 365)
    await payment_escrows_col.create_index("payment_id", unique=True)
    await editor_statuses_col.create_index([("editor_id", 1), ("expires_at", 1), ("created_at", 1)])
    await editor_portfolio_items_col.create_index([("editor_id", 1), ("created_at", -1)])
    await editor_portfolio_items_col.create_index("upload_id", unique=True, sparse=True)
    await editor_statuses_col.create_index([("expires_at", 1), ("is_active", 1)])
    await editor_statuses_col.create_index("upload_id", unique=True)
    await status_likes_col.create_index([("status_id", 1), ("user_id", 1)], unique=True)
    await status_likes_col.create_index([("status_id", 1), ("created_at", -1)])
    await status_views_col.create_index([("status_id", 1), ("viewer_id", 1)], unique=True)
    await status_views_col.create_index([("status_id", 1), ("viewed_at", -1)])
    await payment_escrows_col.create_index("request_id", unique=True)
    await payment_escrows_col.create_index([("status", 1), ("updated_at", -1)])
    await deliveries_col.create_index([("project_id", 1), ("version", 1)], unique=True)
    await deliveries_col.create_index([("delivery_status", 1), ("uploaded_at", 1)])
    await deliveries_col.create_index("upload_id", unique=True)
    await deliveries_col.create_index("delivery_id", unique=True)
    await deliveries_col.create_index([("project_id", 1), ("delivery_status", 1), ("created_at", -1)])
    await editor_payouts_col.create_index("payment_id", unique=True)
    await editor_payouts_col.create_index([("payout_status", 1), ("created_at", -1)])
    await project_quotes_col.create_index([("project_id", 1), ("quote_version", 1)], unique=True)
    await project_quotes_col.create_index([("project_id", 1), ("status", 1), ("created_at", -1)])
    await payments_col.create_index(
        "payhere_payment_id", unique=True,
        partialFilterExpression={"payhere_payment_id": {"$type": "string", "$gt": ""}},
    )
    # Additive, idempotent migration for pre-escrow project payments. Existing
    # payment records remain untouched; integer minor-unit fields become the
    # authoritative representation for new comparisons and accounting.
    from app.services.payhere_service import amount_to_minor
    async for payment in payments_col.find({"payment_type": "project_payment"}):
        fields = {}
        mappings = {
            "amount_minor": "amount", "platform_fee_minor": "platform_fee_amount",
            "editor_earning_minor": "editor_earning_amount",
        }
        for target, source in mappings.items():
            if payment.get(target) is None and payment.get(source) is not None:
                fields[target] = amount_to_minor(payment[source])
        if fields:
            await payments_col.update_one({"_id": payment["_id"]}, {"$set": fields})
        if payment.get("status") in {"AUTHORIZED", "CAPTURED"} and payment.get("request_id"):
            escrow_status = "FUNDED" if payment["status"] == "AUTHORIZED" else "RELEASED"
            await payment_escrows_col.update_one(
                {"payment_id": str(payment["_id"])},
                {"$setOnInsert": {
                    "payment_id": str(payment["_id"]), "order_id": payment.get("order_id"),
                    "request_id": payment["request_id"], "payer_id": payment.get("user_id"),
                    "editor_user_id": payment.get("editor_user_id"), "currency": payment.get("currency", "LKR"),
                    "gross_minor": fields.get("amount_minor", payment.get("amount_minor", 0)),
                    "platform_fee_minor": fields.get("platform_fee_minor", payment.get("platform_fee_minor", 0)),
                    "editor_amount_minor": fields.get("editor_earning_minor", payment.get("editor_earning_minor", 0)),
                    "status": escrow_status, "settlement_status": "NOT_DUE" if escrow_status == "FUNDED" else "PAYABLE",
                    "funded_at": payment.get("authorized_at", now_utc()), "created_at": now_utc(), "updated_at": now_utc(),
                    "migration_source": "legacy_authorized_payment",
                }}, upsert=True,
            )
    # OTPs for email verification and password resets may coexist for one
    # address. Older installations used a unique index on email alone, which
    # makes the second purpose fail with DuplicateKeyError.
    otp_indexes = await otps_col.index_information()
    email_index = otp_indexes.get("email_1")
    if email_index and email_index.get("unique"):
        await otps_col.drop_index("email_1")
    await otps_col.create_index([("email", 1), ("purpose", 1)], unique=True)
    created_index = otp_indexes.get("created_at_1")
    if created_index and created_index.get("expireAfterSeconds") != settings.OTP_EXPIRE_SECONDS:
        await otps_col.drop_index("created_at_1")
    await otps_col.create_index("created_at", expireAfterSeconds=settings.OTP_EXPIRE_SECONDS)


MESSAGE_IDEMPOTENCY_INDEX = "uq_message_client_id"
MESSAGE_IDEMPOTENCY_KEYS = [
    ("request_id", 1),
    ("sender_id", 1),
    ("client_message_id", 1),
]
# `$gt: ""` excludes null/missing/empty values and, unlike `$ne`, is supported
# in partial indexes by every MongoDB version supported by Motor/PyMongo here.
MESSAGE_IDEMPOTENCY_FILTER = {
    "client_message_id": {"$type": "string", "$gt": ""},
}


async def ensure_message_idempotency_index(collection=messages_col) -> dict:
    """Replace only mismatched client-message indexes; safe to run repeatedly."""
    indexes = await collection.index_information()
    dropped = []
    expected_keys = list(MESSAGE_IDEMPOTENCY_KEYS)
    for name, definition in indexes.items():
        if name == "_id_":
            continue
        same_keys = list(definition.get("key", [])) == expected_keys
        is_expected = (
            name == MESSAGE_IDEMPOTENCY_INDEX
            and same_keys
            and definition.get("unique") is True
            and definition.get("partialFilterExpression") == MESSAGE_IDEMPOTENCY_FILTER
        )
        if (same_keys or name == MESSAGE_IDEMPOTENCY_INDEX) and not is_expected:
            await collection.drop_index(name)
            dropped.append(name)
    await collection.create_index(
        MESSAGE_IDEMPOTENCY_KEYS,
        unique=True,
        name=MESSAGE_IDEMPOTENCY_INDEX,
        partialFilterExpression=MESSAGE_IDEMPOTENCY_FILTER,
    )
    return {"index": MESSAGE_IDEMPOTENCY_INDEX, "dropped": dropped}


async def purge_expired_project_media() -> int:
    """Idempotently remove eligible chat media/content while retaining evidence."""
    import asyncio
    import boto3
    deleted = 0
    now = now_utc()
    async for project in requests_col.find({"status": "completed", "completed_at": {"$exists": True}}):
        retention_days = project.get("media_policy", {}).get("retention_days", settings.MEDIA_RETENTION_DAYS_AFTER_COMPLETION)
        if project["completed_at"] + timedelta(days=retention_days) > now:
            continue
        request_id = str(project["_id"])
        async for record in db["uploads.files"].find({"metadata.request_id": request_id}, {"_id": 1}):
            await uploads_bucket.delete(record["_id"])
            deleted += 1
        s3 = boto3.client("s3", region_name=settings.AWS_REGION, aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None, aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None)
        async for record in multipart_uploads_col.find({"request_id": request_id, "cleanup_deleted_at": {"$exists": False}}):
            try:
                await asyncio.to_thread(s3.delete_object, Bucket=record["bucket"], Key=record["key"])
                await multipart_uploads_col.update_one({"_id": record["_id"]}, {"$set": {"cleanup_deleted_at": now, "state": "deleted"}})
                deleted += 1
            except Exception as exc:
                await failed_jobs_col.insert_one({"job": "chat_s3_cleanup", "request_id": request_id, "upload_id": record.get("upload_id"), "attempts": 1, "status": "retry", "error_type": type(exc).__name__, "created_at": now})
        unresolved = await disputes_col.find_one({"request_id": request_id, "status": {"$in": ["open", "under_review"]}})
        report = await chat_reports_col.find_one({"request_id": request_id, "status": {"$in": ["open", "under_review"]}})
        if not unresolved and not report:
            await messages_col.update_many(
                {"request_id": request_id, "cleanup_completed_at": {"$exists": False}},
                {"$set": {"text": None, "file_url": None, "upload_id": None, "cleanup_completed_at": now, "moderation_status": "retention_deleted"}},
            )
        await requests_col.update_one({"_id": project["_id"]}, {"$set": {"media_purged_at": now, "chat_cleanup_status": "completed" if not unresolved and not report else "evidence_hold", "chat_cleanup_updated_at": now}})
    return deleted


async def process_project_deadlines() -> dict:
    """Apply deterministic expiry/overdue states; safe to run repeatedly."""
    now = now_utc()
    expired = 0
    overdue = 0
    expiry_before = now - timedelta(days=settings.PROJECT_REQUEST_EXPIRY_DAYS)
    async for project in requests_col.find({"$or": [{"status": "pending", "created_at": {"$lt": expiry_before}}, {"status": {"$in": ["accepted", "payment_failed"]}, "payment_authorized": {"$ne": True}, "status_updated_at": {"$lt": expiry_before}}]}):
        source = project["status"]
        reason = "Editor did not respond before request expiry" if source == "pending" else "Payment was not completed before project expiry"
        event = {"from": source, "to": "expired", "reason": reason, "actor_id": None, "actor_role": "system", "created_at": now}
        result = await requests_col.update_one({"_id": project["_id"], "status": source}, {"$set": {"status": "expired", "expired_at": now, "status_updated_at": now}, "$push": {"status_history": event}})
        expired += result.modified_count
        if result.modified_count:
            await payments_col.update_many(
                {"request_id": str(project["_id"]), "payment_type": "project_payment", "status": "PENDING"},
                {"$set": {"status": "CANCELLED", "cancelled_at": now, "gateway_status_message": "Project expired before payment confirmation", "updated_at": now}, "$unset": {"active_request_key": ""}},
            )
    active = {"$in": ["in_progress", "revision_requested"]}
    async for payment in payments_col.find({"payment_type": "project_payment", "status": "AUTHORIZED", "delivery_date": {"$type": "string"}}):
        try:
            deadline = datetime.combine(datetime.fromisoformat(payment["delivery_date"]).date(), time.max, tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if deadline >= now:
            continue
        project = await requests_col.find_one({"_id": ObjectId(payment["request_id"]), "status": active}) if ObjectId.is_valid(payment.get("request_id", "")) else None
        if not project:
            continue
        event = {"from": project["status"], "to": "overdue", "reason": "Contract delivery deadline passed", "actor_id": None, "actor_role": "system", "created_at": now}
        result = await requests_col.update_one({"_id": project["_id"], "status": project["status"]}, {"$set": {"status": "overdue", "overdue_at": now, "was_overdue": True, "status_updated_at": now}, "$push": {"status_history": event}})
        overdue += result.modified_count
    return {"expired": expired, "overdue": overdue}
