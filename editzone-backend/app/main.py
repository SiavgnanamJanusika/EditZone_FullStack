import socketio
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from pymongo.errors import AutoReconnect, ConfigurationError, ConnectionFailure, OperationFailure, PyMongoError

from app.config import settings
from app.db.mongodb import describe_mongodb_error, ensure_indexes, client, worker_heartbeats_col
from app.core.utils import now_utc
from redis.asyncio import Redis
from app.sockets.socket_manager import sio
from app.services.malware_scanner import scanner_health

logger = logging.getLogger(__name__)
startup_logger = logging.getLogger("uvicorn.error")


def _redis_is_required() -> bool:
    return settings.ENV.lower() in {"production", "staging"}


def _cors_origins() -> list[str]:
    origins = [*settings.CORS_ORIGINS, settings.FRONTEND_URL.rstrip("/")]
    if settings.ENV.lower() == "development":
        origins.extend(("http://localhost:5173", "http://127.0.0.1:5173"))
    return list(dict.fromkeys(origin for origin in origins if origin))

from app.routers import (
    auth_router,
    user_router,
    editor_router,
    request_router,
    chat_router,
    payment_router,
    review_router,
    admin_router,
    upload_router,
    notification_router,
    editor_verification_router,
    identity_verification_router,
    status_router,
    quote_payment_router,
)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        for attempt in range(1, settings.MONGO_STARTUP_RETRIES + 1):
            try:
                await ensure_indexes()
                startup_logger.info("MongoDB indexes initialized successfully")
                break
            except (ConfigurationError, OperationFailure) as exc:
                # Configuration/auth/index conflicts do not improve by retrying.
                raise RuntimeError(f"MongoDB startup check failed: {describe_mongodb_error(exc)}") from exc
            except (AutoReconnect, ConnectionFailure) as exc:
                if attempt == settings.MONGO_STARTUP_RETRIES:
                    raise RuntimeError(f"MongoDB startup check failed after {attempt} attempts: {describe_mongodb_error(exc)}") from exc
                logger.warning(
                    "MongoDB startup attempt %s/%s failed: %s; retrying",
                    attempt,
                    settings.MONGO_STARTUP_RETRIES,
                    describe_mongodb_error(exc),
                )
                await asyncio.sleep(attempt)
            except PyMongoError as exc:
                raise RuntimeError(f"MongoDB startup check failed: {describe_mongodb_error(exc)}") from exc

        redis_client = Redis.from_url(settings.REDIS_URL, socket_connect_timeout=5, socket_timeout=5)
        try:
            await redis_client.ping()
        except Exception as exc:
            if _redis_is_required():
                raise RuntimeError(f"Redis/Socket.IO startup check failed for {settings.REDIS_URL}: {exc}") from exc
            logger.warning(
                "Redis is unavailable at %s; development will use single-process Socket.IO. %s",
                settings.REDIS_URL,
                exc,
            )
        finally:
            await redis_client.aclose()
        startup_logger.info("Application startup complete")
        yield
    finally:
        client.close()
        startup_logger.info("MongoDB connection closed")


app = FastAPI(
    title=settings.APP_NAME,
    description="EditZone — connecting clients with professional video editors.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_origin_regex=(
        r"https?://(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?"
        if settings.ENV == "development"
        else None
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(self), geolocation=()")
    if settings.ENV.lower() == "production":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code in {401, 403, 404}:
        logger.warning(
            "API request denied status=%s method=%s path=%s reason=%s",
            exc.status_code, request.method, request.url.path,
            str(exc.detail)[:300],
        )
    if isinstance(exc.detail, dict):
        content = {"success": False, **exc.detail}
        content.setdefault("message", "Request failed")
    else:
        # Preserve FastAPI's conventional `detail` field while retaining the
        # existing `message` contract used by older EditZone clients.
        content = {"success": False, "message": exc.detail, "detail": exc.detail}
    if request.url.path == "/api/v1/account" and "code" not in content:
        content["code"] = {401: "UNAUTHORIZED", 403: "FORBIDDEN", 410: "ALREADY_DELETED", 422: "VALIDATION_ERROR"}.get(exc.status_code, "REQUEST_FAILED")
    return JSONResponse(status_code=exc.status_code, content=content)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = [{"field": ".".join(str(x) for x in e["loc"]), "message": e["msg"]} for e in exc.errors()]
    message = errors[0]["message"].removeprefix("Value error, ") if errors else "Validation error"
    content = {"success": False, "message": message, "errors": errors}
    if request.url.path == "/api/v1/account":
        content["code"] = "INVALID_CONFIRMATION" if any(error["field"].endswith("confirmation") for error in errors) else "VALIDATION_ERROR"
    return JSONResponse(status_code=422, content=content)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception(
        "Unhandled API exception method=%s path=%s exception_type=%s exception=%s",
        request.method, request.url.path, type(exc).__name__, exc,
    )
    code = "ACCOUNT_DELETION_FAILED" if request.url.path == "/api/v1/account" else "INTERNAL_SERVER_ERROR"
    message = "The account could not be deleted. Please try again later." if request.url.path == "/api/v1/account" else "An internal server error occurred."
    return JSONResponse(status_code=500, content={"success": False, "code": code, "message": message})


async def health_live():
    return {"status": "healthy"}


async def health_ready():
    services = {}
    critical_failure = False
    try:
        await client.admin.command("ping")
        services["mongodb"] = "healthy"
    except Exception:
        services["mongodb"] = "unavailable"
        critical_failure = True
    redis_client = Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1)
    try:
        await redis_client.ping()
        services["redis"] = "healthy"
        services["socket_manager"] = "healthy"
    except Exception:
        services["redis"] = "unavailable"
        services["socket_manager"] = "single_process"
        if _redis_is_required():
            critical_failure = True
    finally:
        await redis_client.aclose()
    services["storage"] = "configured" if settings.AWS_S3_BUCKET else "not_configured"
    media_scanner = await scanner_health()
    services["media_scanner"] = media_scanner
    services["email"] = "configured" if settings.SMTP_HOST and settings.SMTP_USER else "not_configured"
    payhere_configured = bool(settings.PAYHERE_MERCHANT_ID and settings.PAYHERE_MERCHANT_SECRET)
    services["payment"] = (
        "not_configured"
        if not payhere_configured
        else ("sandbox" if settings.PAYHERE_SANDBOX else "configured")
    )
    heartbeat = await worker_heartbeats_col.find_one({"worker": "scheduler"}) if services["mongodb"] == "healthy" else None
    recent = bool(heartbeat and heartbeat.get("updated_at") and (now_utc() - heartbeat["updated_at"]).total_seconds() < 180)
    services["worker"] = "healthy" if recent else "unavailable"
    if settings.ENV.lower() == "production" and not recent:
        critical_failure = True
    payload = {"status": "unavailable" if critical_failure else ("degraded" if "not_configured" in services.values() or not recent or not media_scanner["ready"] else "healthy"), "services": services}
    return JSONResponse(status_code=503 if critical_failure else 200, content=payload)


app.add_api_route("/health/live", health_live, methods=["GET"], include_in_schema=False)
app.add_api_route("/health/ready", health_ready, methods=["GET"], include_in_schema=False)
app.add_api_route("/api/v1/health", health_live, methods=["GET"])
app.add_api_route("/api/v1/health/live", health_live, methods=["GET"])
app.add_api_route("/api/v1/health/ready", health_ready, methods=["GET"])


app.include_router(auth_router.router)
app.include_router(user_router.router)
app.include_router(user_router.account_router)
app.include_router(editor_router.router)
app.include_router(request_router.router)
app.include_router(chat_router.router)
app.include_router(quote_payment_router.router)
app.include_router(payment_router.router)
app.include_router(review_router.router)
app.include_router(admin_router.router)
app.include_router(upload_router.router)
app.include_router(upload_router.media_router)
app.include_router(notification_router.router)
app.include_router(editor_verification_router.router)
app.include_router(identity_verification_router.router)
app.include_router(identity_verification_router.nic_router)
app.include_router(status_router.router)

# Mount Socket.IO onto FastAPI -> served at /socket.io/.  Export the combined
# ASGI application under both names so either established Uvicorn command
# (`app.main:app` or `app.main:socket_app`) starts chat networking as well as
# REST.  Previously `app.main:app` silently served REST but returned 404 for
# every Socket.IO handshake, leaving the chat composer permanently disabled.
fastapi_app = app
socket_app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app, socketio_path="socket.io")
# Preserve the small introspection surface used by API contract tooling.
socket_app.openapi = fastapi_app.openapi
app = socket_app
