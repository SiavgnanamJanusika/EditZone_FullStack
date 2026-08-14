import re
import secrets
import hashlib
import hmac
import smtplib
from datetime import timedelta
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Depends, Response, Request, status
from bson import ObjectId
from pymongo.errors import DuplicateKeyError
from pymongo import ReturnDocument
from google.auth.exceptions import GoogleAuthError
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests

from app.db.mongodb import (
    users_col, editors_col, auth_rate_limits_col,
    auth_sessions_col, auth_security_events_col,
)
from app.schemas.auth_schema import (
    RegisterAccountRequest, LoginRequest, CompleteProfileRequest,
    ForgotPasswordRequest, ResetPasswordRequest, VerifyOtpRequest,
    TokenResponse,
    GoogleLoginRequest,
)
from app.core.security import (
    hash_password, verify_password, create_access_token, create_refresh_token,
    decode_token, get_current_user, create_socket_token,
)
from app.services.admin_account_service import DELETED_MESSAGE
from app.core.utils import ensure_utc, serialize_doc, now_utc
from app.core.validators import is_valid_nic
from app.services.email_service import send_otp_email
from app.services.otp_service import (
    consume_otp,
    delete_otp,
    get_otp,
    increment_otp_attempts,
    lock_otp,
    store_otp,
)
from app.config import settings
from app.services.auth_throttle_service import (
    clear_counter,
    get_counter,
    get_scope_counts,
    increment_counter,
    require_captcha,
)

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])


@router.post("/socket-token")
async def socket_token(request: Request, current_user: dict = Depends(get_current_user)):
    access = request.cookies.get("ez_access_token")
    if not access:
        raise HTTPException(status_code=401, detail="Authenticated session required")
    payload = decode_token(access)
    session_id = payload.get("sid")
    if not session_id:
        raise HTTPException(status_code=401, detail="Session is unavailable")
    session = await auth_sessions_col.find_one({
        "session_id": session_id, "user_id": current_user["_id"], "revoked_at": None,
    }, {"_id": 1})
    if not session:
        raise HTTPException(status_code=401, detail="Session was revoked")
    token = create_socket_token({"sub": str(current_user["_id"]), "role": current_user["role"], "sid": session_id})
    return {"token": token, "expires_in": 120}


def _generate_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _hash_otp(email: str, purpose: str, otp: str) -> str:
    value = f"{email.lower()}:{purpose}:{otp}".encode()
    return hmac.new(settings.JWT_SECRET_KEY.encode(), value, hashlib.sha256).hexdigest()


def _otp_expired(record: dict) -> bool:
    created_at = record.get("created_at")
    if not created_at:
        return True
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=now_utc().tzinfo)
    return now_utc() - created_at > timedelta(seconds=settings.OTP_EXPIRE_SECONDS)


def _otp_age_seconds(record: dict) -> float:
    created_at = record.get("created_at")
    if not created_at:
        return float("inf")
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=now_utc().tzinfo)
    return (now_utc() - created_at).total_seconds()


def _has_verified_email(user: dict) -> bool:
    """Accept explicit verification plus narrow evidence from legacy accounts."""
    if user.get("is_email_verified") is True or user.get("email_verified") is True:
        return True
    if "is_email_verified" in user:
        return False
    return bool(
        user.get("email_verified_at")
        or user.get("google_id")
        or user.get("auth_provider") == "google"
        or user.get("registration_complete") is True
    )


async def _migrate_legacy_email_verification(user: dict) -> bool:
    verified = _has_verified_email(user)
    if verified and user.get("is_email_verified") is not True:
        verified_at = user.get("email_verified_at") or user.get("updated_at") or user.get("created_at") or now_utc()
        await users_col.update_one(
            {"_id": user["_id"], "is_email_verified": {"$ne": True}},
            {"$set": {"is_email_verified": True, "email_verified": True, "email_verified_at": verified_at}},
        )
        user["is_email_verified"] = True
        user["email_verified"] = True
        user["email_verified_at"] = verified_at
    return verified


def _client_ip(request: Request) -> str:
    # Do not trust caller-controlled forwarding headers unless the deployment
    # has an explicit trusted-proxy policy in front of the application.
    return request.client.host if request.client else "unknown"


async def _check_otp(record: dict | None, submitted: str, email: str, purpose: str, ip: str, captcha_token: str | None):
    if not record or record.get("locked"):
        raise HTTPException(status_code=400, detail="Invalid verification code.")
    if _otp_expired(record):
        await delete_otp(email, purpose)
        raise HTTPException(status_code=400, detail="Verification code expired. Please request a new code.")
    attempts = int(record.get("attempts", 0))
    if attempts >= 3:
        await require_captcha(captcha_token, ip)
    submitted_hash = _hash_otp(email, purpose, submitted)
    if not secrets.compare_digest(str(record.get("otp_hash", "")), submitted_hash):
        used = await increment_otp_attempts(record)
        if used >= settings.OTP_MAX_ATTEMPTS:
            await lock_otp(record)
            raise HTTPException(status_code=429, detail="OTP attempt limit reached. Request a new OTP after the cooldown")
        raise HTTPException(status_code=400, detail="Invalid verification code.")
    return submitted_hash


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _cookie_options() -> dict:
    return {
        "httponly": True,
        "secure": settings.ENV.lower() in ("production", "staging"),
        "samesite": "lax",
        "path": "/",
    }


async def _issue_tokens(
    user: dict,
    response: Response,
    request: Request,
    *,
    token_family: str | None = None,
) -> TokenResponse:
    # Central defense in depth: no caller can accidentally create a normal
    # application session for an explicitly unverified account.
    if not _has_verified_email(user):
        raise HTTPException(status_code=403, detail={
            "code": "EMAIL_VERIFICATION_REQUIRED",
            "message": "Verify your email before logging in",
            "email": str(user.get("email", "")).strip().lower(),
        })
    uid = str(user["_id"])
    session_id = str(uuid4())
    family = token_family or str(uuid4())
    access = create_access_token({"sub": uid, "role": user["role"], "sid": session_id})
    refresh = create_refresh_token({"sub": uid, "sid": session_id, "family": family})
    now = now_utc()
    await auth_sessions_col.insert_one({
        "session_id": session_id,
        "user_id": user["_id"],
        "refresh_token_hash": _token_hash(refresh),
        "token_family": family,
        "device_name": (request.headers.get("user-agent") or "Unknown device")[:160],
        "ip_address": _client_ip(request),
        "user_agent": (request.headers.get("user-agent") or "")[:500],
        "created_at": now,
        "expires_at": now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        "last_used_at": now,
        "revoked_at": None,
        "revoke_reason": None,
    })
    cookie_options = _cookie_options()
    response.set_cookie(
        "ez_access_token",
        access,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        **cookie_options,
    )
    response.set_cookie(
        "ez_refresh_token",
        refresh,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        **cookie_options,
    )
    return TokenResponse(
        message="Login successful",
        role=user["role"],
        registration_complete=user.get("registration_complete", False),
        email_verified=bool(user.get("is_email_verified")),
        user_id=uid,
        profile_completion_required=not user.get("registration_complete", False),
        user={
            "id": uid,
            "username": user.get("username", ""),
            "email": user.get("email", ""),
            "role": user["role"],
            "profile_picture": user.get("profile_picture", ""),
            "registration_complete": user.get("registration_complete", False),
            "email_verified": bool(user.get("is_email_verified")),
            "is_email_verified": bool(user.get("is_email_verified")),
            "has_password": bool(user.get("password_hash")),
        },
    )


@router.post("/register", status_code=201)
async def register(body: RegisterAccountRequest, request: Request):
    email = str(body.email).strip().lower()
    nic = body.nic.strip().upper()
    deleted_email_hash = hashlib.sha256(email.encode()).hexdigest()
    existing = await users_col.find_one({
        "$or": [
            {"email": {"$regex": f"^{re.escape(email)}$", "$options": "i"}},
            {"nic": {"$regex": f"^{re.escape(nic)}$", "$options": "i"}},
            {"email_hash": deleted_email_hash, "is_deleted": True},
        ]
    })
    if existing:
        raise HTTPException(status_code=409, detail="Email or NIC already registered")

    user_doc = {
        "username": body.username,
        "email": email,
        "password_hash": hash_password(body.password),
        "nic": nic,
        "role": body.role,
        "registration_complete": False,
        "is_email_verified": False,
        "email_verified": False,
        "is_banned": False,
        "created_at": now_utc(),
    }
    try:
        result = await users_col.insert_one(user_doc)
    except DuplicateKeyError as exc:
        # The initial lookup gives a useful fast-path, but the database index is
        # the final authority when two registrations arrive at the same time.
        raise HTTPException(status_code=409, detail="Email or NIC already registered") from exc
    user_doc["_id"] = result.inserted_id

    if body.role == "editor":
        await editors_col.insert_one({
            "user_id": result.inserted_id,
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
            "identity_verification_status": "not_started",
            "created_at": now_utc(),
        })
    otp = _generate_otp()
    await store_otp(email, "verify_email", _hash_otp(email, "verify_email", otp))
    try:
        await send_otp_email(email, otp, "verify_email")
    except (OSError, smtplib.SMTPException, RuntimeError):
        await users_col.delete_one({"_id": result.inserted_id, "is_email_verified": False})
        await editors_col.delete_one({"user_id": result.inserted_id})
        await delete_otp(email, "verify_email")
        raise HTTPException(status_code=503, detail="Email delivery is temporarily unavailable")
    return {"message": "Account created. Verify the OTP sent to your email.", "email": email}


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, response: Response, request: Request):
    email = str(body.email).strip().lower()
    ip = _client_ip(request)
    counts = await get_scope_counts(email, ip, "login", settings.LOGIN_LOCK_MINUTES)
    failures = counts["email"]
    if failures >= settings.LOGIN_MAX_FAILURES or counts["ip"] >= settings.LOGIN_IP_MAX_FAILURES:
        raise HTTPException(status_code=429, detail="Account temporarily locked after repeated login failures. Try again in 15 minutes", headers={"Retry-After": "900"})
    counter = await get_counter(email, ip, "login", settings.LOGIN_LOCK_MINUTES)
    next_allowed = ensure_utc((counter or {}).get("next_allowed_at"))
    current_time = ensure_utc(now_utc())
    if next_allowed and next_allowed > current_time:
        retry = max(1, int((next_allowed - current_time).total_seconds()))
        raise HTTPException(status_code=429, detail=f"Too many attempts. Try again in {retry} seconds", headers={"Retry-After": str(retry)})
    if failures >= 3:
        await require_captcha(body.captcha_token, ip)
    user = await users_col.find_one({"email": {"$regex": f"^{re.escape(email)}$", "$options": "i"}})
    if user and (user.get("is_deleted") or user.get("status") == "deleted"):
        raise HTTPException(status_code=403, detail=DELETED_MESSAGE)
    if not user or not verify_password(body.password, user.get("password_hash")):
        updated = await increment_counter(email, ip, "login", settings.LOGIN_LOCK_MINUTES)
        delay = min(2 ** max(int(updated.get("count", 1)) - 1, 0), 30)
        await auth_rate_limits_col.update_one({"_id": updated["_id"]}, {"$set": {"next_allowed_at": now_utc() + timedelta(seconds=delay)}})
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if user.get("is_banned"):
        raise HTTPException(status_code=403, detail="Account has been suspended")
    # Validate the visible Client/Editor selector only after the password is
    # accepted, so this check cannot disclose an account's role. Admin login
    # remains backward compatible because the selector has no Admin option.
    stored_role = user.get("role")
    if body.role and stored_role in {"user", "editor"} and body.role != stored_role:
        expected_label = "Editor" if stored_role == "editor" else "Client"
        raise HTTPException(status_code=403, detail={
            "code": "ROLE_MISMATCH",
            "message": f"This account is registered as {expected_label}. Select {expected_label} and try again.",
            "role": stored_role,
        })
    if not await _migrate_legacy_email_verification(user):
        raise HTTPException(status_code=403, detail={
            "code": "EMAIL_VERIFICATION_REQUIRED",
            "message": "Verify your email before logging in",
            "email": email,
        })
    await clear_counter(email, ip, "login")
    return await _issue_tokens(user, response, request)


def _verify_google_credential(credential: str) -> dict:
    if not settings.GOOGLE_CLIENT_ID.strip():
        raise HTTPException(status_code=503, detail="Google login is not configured")
    try:
        claims = google_id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            settings.GOOGLE_CLIENT_ID,
        )
    except (ValueError, GoogleAuthError) as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired Google token") from exc

    # verify_oauth2_token validates signature, expiry, audience, and Google's
    # issuer. Keep explicit checks here as defense in depth and clear errors.
    if claims.get("aud") != settings.GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=401, detail="Invalid Google token audience")
    if claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
        raise HTTPException(status_code=401, detail="Invalid Google token issuer")
    if claims.get("email_verified") is not True:
        raise HTTPException(status_code=403, detail="Google email is not verified")
    if not all(str(claims.get(field, "")).strip() for field in ("sub", "email", "name")):
        raise HTTPException(status_code=401, detail="Google account is missing required profile information")
    return claims


def _new_editor_document(user_id: ObjectId) -> dict:
    return {
        "user_id": user_id, "bio": "", "skills": [], "hourly_rate": 0,
        "location": "", "portfolio_links": [], "profile_picture": "",
        "category": "Video Editor", "rating_avg": 0, "rating_count": 0,
        "total_views": 0,
        "identity_verification_status": "not_started", "created_at": now_utc(),
    }


@router.post("/google", response_model=TokenResponse)
async def google_login(body: GoogleLoginRequest, response: Response, request: Request):
    claims = _verify_google_credential(body.credential)
    google_id = str(claims["sub"]).strip()
    email = str(claims["email"]).strip().lower()
    user = await users_col.find_one({"google_id": google_id, "is_deleted": {"$ne": True}, "status": {"$ne": "deleted"}})
    if user and (user.get("is_deleted") or user.get("status") == "deleted"):
        raise HTTPException(status_code=403, detail=DELETED_MESSAGE)
    if user is None:
        user = await users_col.find_one({
            "email": {"$regex": f"^{re.escape(email)}$", "$options": "i"}
        })
        if user is not None:
            if user.get("is_deleted") or user.get("status") == "deleted":
                raise HTTPException(status_code=403, detail=DELETED_MESSAGE)
            if user.get("role") != body.role:
                raise HTTPException(status_code=409, detail=f"This account is registered as {user['role']}. Continue with that role.")
            existing_google_id = user.get("google_id")
            if existing_google_id and existing_google_id != google_id:
                raise HTTPException(status_code=409, detail="This email is linked to another Google account")
            try:
                linked = await users_col.find_one_and_update(
                    {"_id": user["_id"], "$or": [{"google_id": {"$exists": False}}, {"google_id": None}, {"google_id": google_id}]},
                    {"$set": {"google_id": google_id, "is_email_verified": True, "email_verified_at": now_utc()}},
                    return_document=ReturnDocument.AFTER,
                )
            except DuplicateKeyError as exc:
                raise HTTPException(status_code=409, detail="Google account is already linked") from exc
            if linked is None:
                raise HTTPException(status_code=409, detail="This account could not be linked safely")
            user = linked
        else:
            deleted = await users_col.find_one({
                "is_deleted": True,
                "$or": [
                    {"email_hash": hashlib.sha256(email.encode()).hexdigest()},
                    {"google_id_hash": hashlib.sha256(google_id.encode()).hexdigest()},
                ],
            })
            if deleted:
                raise HTTPException(status_code=403, detail=DELETED_MESSAGE)
            user = {
                "username": str(claims["name"]).strip(), "email": email,
                "role": body.role, "auth_provider": "google", "google_id": google_id,
                "profile_picture": str(claims.get("picture") or "").strip(),
                "registration_complete": False, "is_email_verified": True,
                "email_verified_at": now_utc(), "is_banned": False, "created_at": now_utc(),
            }
            try:
                result = await users_col.insert_one(user)
                user["_id"] = result.inserted_id
            except DuplicateKeyError:
                # A concurrent request may have created or linked the account.
                user = await users_col.find_one({"$or": [{"google_id": google_id}, {"email": email}]})
                if user is None or user.get("google_id") != google_id:
                    raise HTTPException(status_code=409, detail="An account already exists for this email")
            if body.role == "editor":
                try:
                    await editors_col.insert_one(_new_editor_document(user["_id"]))
                except DuplicateKeyError:
                    pass

    if user.get("role") != body.role:
        raise HTTPException(status_code=409, detail=f"This account is registered as {user['role']}. Continue with that role.")
    if user.get("is_banned"):
        raise HTTPException(status_code=403, detail="Account has been suspended")
    return await _issue_tokens(user, response, request)


@router.post("/complete-profile", response_model=dict)
async def complete_profile(body: CompleteProfileRequest, current_user: dict = Depends(get_current_user)):
    if not current_user.get("is_email_verified"):
        raise HTTPException(status_code=403, detail="Verify your email before completing your profile")
    if current_user["role"] == "editor":
        if body.nic != str(current_user.get("nic", "")).strip().upper():
            raise HTTPException(
                status_code=409,
                detail="The verified editor NIC number cannot be changed",
            )
        editor = await editors_col.find_one(
            {"user_id": current_user["_id"]},
            {"identity_verification_status": 1, "nic_ocr_verified": 1, "selfie_verified": 1},
        )
        if (
            not editor
            or editor.get("identity_verification_status") not in {"selfie_verified", "verified"}
            or not editor.get("nic_ocr_verified")
            or not editor.get("selfie_verified")
        ):
            raise HTTPException(
                status_code=409,
                detail="Complete NIC and live selfie verification before finishing editor registration",
            )

    if body.nic != current_user.get("nic"):
        existing = await users_col.find_one({"nic": body.nic, "_id": {"$ne": current_user["_id"]}})
        if existing:
            raise HTTPException(status_code=409, detail="NIC already in use")

    try:
        await users_col.update_one(
            {"_id": current_user["_id"]},
            {"$set": {
                "username": body.username,
                "nic": body.nic,
                "district": body.district,
                "gender": body.gender,
                "phone": body.phone,
                "registration_complete": True,
                "updated_at": now_utc(),
            }},
        )
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="NIC already in use") from exc
    redirect_to = "editors" if current_user["role"] == "user" else "editor-dashboard"
    return {"message": "Profile completed successfully", "redirect_to": redirect_to}


@router.get("/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    safe = serialize_doc(current_user)
    safe.pop("password_hash", None)
    safe.pop("google_id", None)
    safe["has_password"] = bool(current_user.get("password_hash"))
    safe["email_verified"] = bool(current_user.get("is_email_verified"))
    return safe


@router.get("/session")
async def get_session_presence(request: Request, response: Response):
    """Validate cookie-backed session presence without returning an expected 401."""
    access_token = request.cookies.get("ez_access_token")
    refresh_token = request.cookies.get("ez_refresh_token")

    if access_token:
        try:
            payload = decode_token(access_token)
            user_id = payload.get("sub", "")
            if payload.get("type") == "access" and ObjectId.is_valid(user_id):
                user = await users_col.find_one({"_id": ObjectId(user_id)})
                if user and not user.get("is_banned") and not user.get("is_deleted") and user.get("status") != "deleted" and user.get("is_email_verified"):
                    return {"authenticated": True}
        except HTTPException:
            pass

    if refresh_token:
        try:
            payload = decode_token(refresh_token)
            user_id = payload.get("sub", "")
            if payload.get("type") == "refresh" and ObjectId.is_valid(user_id):
                session = await auth_sessions_col.find_one({
                    "refresh_token_hash": _token_hash(refresh_token),
                    "revoked_at": None,
                })
                if session:
                    user = await users_col.find_one({"_id": session["user_id"]})
                    if user and not user.get("is_banned") and not user.get("is_deleted") and user.get("status") != "deleted" and user.get("is_email_verified"):
                        return {"authenticated": True}
        except HTTPException:
            pass

    response.delete_cookie("ez_access_token", path="/")
    response.delete_cookie("ez_refresh_token", path="/")
    return {"authenticated": False}


@router.post("/forgot-password")
async def forgot_password(body: ForgotPasswordRequest, request: Request):
    email = str(body.email).strip().lower()
    ip = _client_ip(request)
    await increment_counter(email, ip, "forgot_password", 60)
    counts = await get_scope_counts(email, ip, "forgot_password", 60)
    if counts["email"] > settings.FORGOT_PASSWORD_MAX_PER_HOUR or counts["ip"] > settings.PASSWORD_RESET_IP_MAX_PER_HOUR:
        await require_captcha(body.captcha_token, ip)
        raise HTTPException(status_code=429, detail="Password reset request limit reached. Try again later", headers={"Retry-After": "3600"})
    user = await users_col.find_one({"email": email})
    if not user:
        # Don't leak whether the email exists
        return {"message": "If that email exists, an OTP has been sent."}

    otp = _generate_otp()
    existing_otp = await get_otp(email, "reset_password")
    if existing_otp and not _otp_expired(existing_otp) and _otp_age_seconds(existing_otp) < settings.OTP_RESEND_SECONDS:
        raise HTTPException(status_code=429, detail="Wait 60 seconds before requesting another OTP", headers={"Retry-After": "60"})
    await store_otp(email, "reset_password", _hash_otp(email, "reset_password", otp))
    try:
        await send_otp_email(email, otp, "reset_password")
    except (OSError, smtplib.SMTPException, RuntimeError):
        await delete_otp(email, "reset_password")
        raise HTTPException(status_code=503, detail="Email delivery is temporarily unavailable")
    return {"message": "If that email exists, an OTP has been sent."}


@router.post("/reset-password")
async def reset_password(body: ResetPasswordRequest, request: Request):
    email = str(body.email).strip().lower()
    record = await get_otp(email, "reset_password")
    await _check_otp(record, body.otp, email, "reset_password", _client_ip(request), body.captcha_token)

    user = await users_col.find_one_and_update(
        {"email": email},
        {"$set": {"password_hash": hash_password(body.new_password), "password_changed_at": now_utc()}},
        return_document=ReturnDocument.AFTER,
    )
    if user:
        await auth_sessions_col.update_many(
            {"user_id": user["_id"], "revoked_at": None},
            {"$set": {"revoked_at": now_utc(), "revoke_reason": "password_reset"}},
        )
    await delete_otp(email, "reset_password")
    return {"message": "Password reset successfully"}


@router.post("/send-otp")
async def send_email_otp(body: ForgotPasswordRequest, request: Request):
    email = str(body.email).strip().lower()
    ip = _client_ip(request)
    account = await users_col.find_one({"email": {"$regex": f"^{re.escape(email)}$", "$options": "i"}})
    if not account or account.get("is_deleted") or account.get("status") == "deleted":
        raise HTTPException(status_code=404, detail="Unverified account not found")
    if await _migrate_legacy_email_verification(account):
        await delete_otp(email, "verify_email")
        raise HTTPException(status_code=409, detail="Email is already verified")
    existing_otp = await get_otp(email, "verify_email")
    if existing_otp and not _otp_expired(existing_otp):
        age = _otp_age_seconds(existing_otp)
        if age < settings.OTP_RESEND_SECONDS:
            retry_after = max(1, int(settings.OTP_RESEND_SECONDS - age))
            return {
                "message": "A valid OTP was already sent. Use the latest code you received.",
                "resend_after": retry_after,
            }
    await increment_counter(email, ip, "send_otp", 60)
    counts = await get_scope_counts(email, ip, "send_otp", 60)
    if counts["email"] > settings.OTP_EMAIL_MAX_PER_HOUR or counts["ip"] > settings.OTP_IP_MAX_PER_HOUR:
        await require_captcha(body.captcha_token, ip)
        raise HTTPException(status_code=429, detail="OTP request limit reached. Try again later", headers={"Retry-After": "3600"})
    otp = _generate_otp()
    await store_otp(email, "verify_email", _hash_otp(email, "verify_email", otp))
    try:
        await send_otp_email(email, otp, "verify_email")
    except (OSError, smtplib.SMTPException, RuntimeError):
        await delete_otp(email, "verify_email")
        raise HTTPException(status_code=503, detail="Email delivery is temporarily unavailable")
    return {"message": "OTP sent to your email", "resend_after": settings.OTP_RESEND_SECONDS}


@router.post("/verify-otp", response_model=TokenResponse)
async def verify_email_otp(body: VerifyOtpRequest, request: Request, response: Response):
    email = str(body.email).strip().lower()
    account = await users_col.find_one({"email": {"$regex": f"^{re.escape(email)}$", "$options": "i"}})
    if not account or account.get("is_deleted") or account.get("status") == "deleted":
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")
    if await _migrate_legacy_email_verification(account):
        await delete_otp(email, "verify_email")
        raise HTTPException(status_code=409, detail="Email is already verified")
    record = await get_otp(email, "verify_email")
    submitted_hash = await _check_otp(record, body.otp, email, "verify_email", _client_ip(request), body.captcha_token)
    if not await consume_otp(record, submitted_hash):
        raise HTTPException(status_code=400, detail="Invalid verification code.")
    verified_at = now_utc()
    user = await users_col.find_one_and_update(
        {"email": email, "is_email_verified": {"$ne": True}, "is_deleted": {"$ne": True}, "status": {"$ne": "deleted"}},
        {"$set": {"is_email_verified": True, "email_verified": True, "email_verified_at": verified_at}},
        return_document=ReturnDocument.AFTER,
    )
    if not user:
        raise HTTPException(status_code=400, detail="Invalid verification code.")
    return await _issue_tokens(user, response, request)


@router.post("/logout")
async def logout(request: Request, response: Response):
    refresh = request.cookies.get("ez_refresh_token")
    if refresh:
        await auth_sessions_col.update_one(
            {"refresh_token_hash": _token_hash(refresh), "revoked_at": None},
            {"$set": {"revoked_at": now_utc(), "revoke_reason": "logout"}},
        )
    response.delete_cookie("ez_access_token", path="/")
    response.delete_cookie("ez_refresh_token", path="/")
    return {"message": "Logged out successfully"}


@router.post("/refresh", response_model=TokenResponse)
async def refresh_session(request: Request, response: Response):
    raw_token = request.cookies.get("ez_refresh_token")
    if not raw_token:
        raise HTTPException(status_code=401, detail="Refresh session is missing")
    try:
        from app.core.security import decode_token
        payload = decode_token(raw_token)
    except HTTPException:
        response.delete_cookie("ez_access_token", path="/")
        response.delete_cookie("ez_refresh_token", path="/")
        raise
    if payload.get("type") != "refresh" or not ObjectId.is_valid(payload.get("sub", "")):
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    token_hash = _token_hash(raw_token)
    session = await auth_sessions_col.find_one({"refresh_token_hash": token_hash})
    if not session:
        raise HTTPException(status_code=401, detail="Refresh session is invalid")
    if session.get("revoked_at"):
        if session.get("revoke_reason") == "rotated":
            now = now_utc()
            await auth_sessions_col.update_many(
                {"token_family": session["token_family"], "revoked_at": None},
                {"$set": {"revoked_at": now, "revoke_reason": "refresh_token_reuse"}},
            )
            await auth_security_events_col.insert_one({
                "user_id": session["user_id"], "event": "refresh_token_reuse",
                "token_family": session["token_family"], "ip_address": _client_ip(request),
                "user_agent": (request.headers.get("user-agent") or "")[:500], "created_at": now,
            })
        response.delete_cookie("ez_access_token", path="/")
        response.delete_cookie("ez_refresh_token", path="/")
        raise HTTPException(status_code=401, detail="Refresh session has been revoked")

    now = now_utc()
    rotated = await auth_sessions_col.update_one(
        {"_id": session["_id"], "revoked_at": None},
        {"$set": {"revoked_at": now, "revoke_reason": "rotated", "last_used_at": now}},
    )
    if rotated.modified_count != 1:
        raise HTTPException(status_code=401, detail="Refresh session was already used")
    user = await users_col.find_one({"_id": session["user_id"]})
    if user and (user.get("is_deleted") or user.get("status") == "deleted"):
        raise HTTPException(status_code=403, detail=DELETED_MESSAGE)
    if not user or user.get("is_banned") or not user.get("is_email_verified"):
        raise HTTPException(status_code=401, detail="Account is unavailable")
    return await _issue_tokens(user, response, request, token_family=session["token_family"])
