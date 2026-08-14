from datetime import datetime, timedelta, timezone
from typing import Optional, List
import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
import jwt
from passlib.context import CryptContext
from bson import ObjectId

from app.config import settings
from app.core.utils import now_utc
from app.db.mongodb import auth_sessions_col, users_col
from app.services.admin_account_service import DELETED_MESSAGE

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return pwd_context.verify(plain, hashed)
    except (TypeError, ValueError):
        return False


def create_access_token(data: dict, expires_minutes: Optional[int] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    now = datetime.now(timezone.utc)
    to_encode.update({"exp": expire, "iat": now, "nbf": now, "iss": settings.JWT_ISSUER,
                      "aud": settings.JWT_AUDIENCE, "jti": secrets.token_urlsafe(24), "type": "access"})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    now = datetime.now(timezone.utc)
    to_encode.update({"exp": expire, "iat": now, "nbf": now, "iss": settings.JWT_ISSUER,
                      "aud": settings.JWT_AUDIENCE, "jti": secrets.token_urlsafe(24), "type": "refresh"})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_socket_token(data: dict, expires_seconds: int = 120) -> str:
    """Create a short-lived credential usable only for a Socket.IO handshake."""
    now = datetime.now(timezone.utc)
    payload = data.copy()
    payload.update({
        "exp": now + timedelta(seconds=expires_seconds), "iat": now, "nbf": now,
        "iss": settings.JWT_ISSUER, "aud": settings.JWT_AUDIENCE,
        "jti": secrets.token_urlsafe(24), "type": "socket",
    })
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM],
            issuer=settings.JWT_ISSUER, audience=settings.JWT_AUDIENCE,
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
) -> dict:
    token = token or request.cookies.get("ez_access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    payload = decode_token(token)
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")
    user_id = payload.get("sub")
    if not user_id or not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=401, detail="Invalid token payload")
    user = await users_col.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    # Keep this document-level check as defense in depth for mocked/custom
    # collection implementations that do not enforce the Mongo predicate.
    if user.get("is_deleted") is True or user.get("status") == "deleted":
        raise HTTPException(status_code=403, detail=DELETED_MESSAGE)
    session_id = payload.get("sid")
    if not session_id or not await auth_sessions_col.find_one(
        {"session_id": session_id, "user_id": ObjectId(user_id), "revoked_at": None},
        {"_id": 1},
    ):
        raise HTTPException(status_code=401, detail="Session was revoked or expired")
    valid_after = user.get("token_valid_after")
    issued_at = payload.get("iat")
    issued_at_dt = datetime.fromtimestamp(issued_at, tz=timezone.utc) if isinstance(issued_at, (int, float)) else issued_at
    if valid_after and issued_at_dt and issued_at_dt < valid_after:
        raise HTTPException(status_code=401, detail="Session was revoked")
    if user.get("is_banned"):
        raise HTTPException(status_code=403, detail="Account has been suspended")
    verified = user.get("is_email_verified") is True
    if not verified and "is_email_verified" not in user:
        verified = bool(
            user.get("email_verified") is True
            or user.get("email_verified_at")
            or user.get("google_id")
            or user.get("auth_provider") == "google"
            or user.get("registration_complete") is True
        )
        if verified:
            verified_at = user.get("email_verified_at") or user.get("updated_at") or user.get("created_at") or now_utc()
            await users_col.update_one(
                {"_id": user["_id"], "is_email_verified": {"$exists": False}},
                {"$set": {"is_email_verified": True, "email_verified": True, "email_verified_at": verified_at}},
            )
            user["is_email_verified"] = True
            user["email_verified"] = True
            user["email_verified_at"] = verified_at
    if not verified:
        raise HTTPException(status_code=403, detail="Email verification is required")
    return user


def require_roles(allowed_roles: List[str]):
    async def role_checker(current_user: dict = Depends(get_current_user)) -> dict:
        if current_user.get("role") not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to access this resource",
            )
        return current_user

    return role_checker


require_user = require_roles(["user"])
require_editor = require_roles(["editor"])
require_admin = require_roles(["admin"])
