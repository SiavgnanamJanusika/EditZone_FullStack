from types import SimpleNamespace
import re

import pytest
from bson import ObjectId
from fastapi import HTTPException, Response
from starlette.requests import Request

from app.routers import auth_router
from app.schemas.auth_schema import GoogleLoginRequest


def claims(**overrides):
    value = {
        "sub": "google-123", "email": "person@example.com", "name": "Person",
        "picture": "https://example.com/photo.jpg", "email_verified": True,
        "aud": "client-id", "iss": "https://accounts.google.com",
    }
    value.update(overrides)
    return value


class FakeUsers:
    def __init__(self, documents=()):
        self.documents = [dict(item) for item in documents]
        self.inserts = 0

    async def find_one(self, query):
        alternatives = query.get("$or", [query])
        for document in self.documents:
            for condition in alternatives:
                if "google_id" in condition and document.get("google_id") == condition["google_id"]:
                    return document
                if "email" in condition:
                    email_condition = condition["email"]
                    matches = (
                        document.get("email", "").lower() == email_condition.lower()
                        if isinstance(email_condition, str)
                        else re.match(email_condition["$regex"], document.get("email", ""), re.IGNORECASE)
                    )
                    if matches:
                        return document
        return None

    async def insert_one(self, document):
        self.inserts += 1
        document = dict(document)
        document["_id"] = ObjectId()
        self.documents.append(document)
        return SimpleNamespace(inserted_id=document["_id"])

    async def find_one_and_update(self, query, update, **_kwargs):
        document = next((item for item in self.documents if item["_id"] == query["_id"]), None)
        if document is None:
            return None
        requested = update["$set"]["google_id"]
        if document.get("google_id") not in (None, requested):
            return None
        document.update(update["$set"])
        return document


class FakeEditors:
    async def insert_one(self, _document):
        return SimpleNamespace(inserted_id=ObjectId())


@pytest.fixture
def google_setup(monkeypatch):
    users = FakeUsers()
    monkeypatch.setattr(auth_router, "users_col", users)
    monkeypatch.setattr(auth_router, "editors_col", FakeEditors())
    monkeypatch.setattr(auth_router, "_verify_google_credential", lambda _token: claims())

    async def issue(user, _response, _request):
        return {"user": user, "role": user["role"], "registration_complete": user.get("registration_complete", False)}

    monkeypatch.setattr(auth_router, "_issue_tokens", issue)
    return users


def request():
    return Request({"type": "http", "method": "POST", "path": "/api/v1/auth/google", "headers": [], "client": ("127.0.0.1", 1)})


async def login(role="user"):
    return await auth_router.google_login(GoogleLoginRequest(credential="token", role=role), Response(), request())


async def test_valid_new_google_user(google_setup):
    result = await login()
    assert result["user"]["auth_provider"] == "google"
    assert "password_hash" not in result["user"]


async def test_existing_google_user_is_reused(google_setup):
    google_setup.documents.append({"_id": ObjectId(), "google_id": "google-123", "email": "person@example.com", "role": "user", "is_email_verified": True})
    await login()
    assert google_setup.inserts == 0


async def test_existing_email_account_is_linked(google_setup):
    account = {"_id": ObjectId(), "email": "person@example.com", "role": "user", "is_email_verified": True, "username": "Existing"}
    google_setup.documents.append(account)
    result = await login()
    assert result["user"]["google_id"] == "google-123"
    assert result["user"]["username"] == "Existing"


@pytest.mark.parametrize("error", [ValueError("invalid"), ValueError("wrong audience"), ValueError("expired")], ids=["invalid-token", "wrong-audience", "expired-token"])
def test_google_verification_failures_are_rejected(monkeypatch, error):
    monkeypatch.setattr(auth_router.settings, "GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setattr(auth_router.google_id_token, "verify_oauth2_token", lambda *_args: (_ for _ in ()).throw(error))
    with pytest.raises(HTTPException) as exc:
        auth_router._verify_google_credential("bad-token")
    assert exc.value.status_code == 401


def test_unverified_google_email_is_rejected(monkeypatch):
    monkeypatch.setattr(auth_router.settings, "GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setattr(auth_router.google_id_token, "verify_oauth2_token", lambda *_args: claims(email_verified=False))
    with pytest.raises(HTTPException) as exc:
        auth_router._verify_google_credential("token")
    assert exc.value.status_code == 403


async def test_role_conflict_does_not_change_existing_role(google_setup):
    google_setup.documents.append({"_id": ObjectId(), "google_id": "google-123", "email": "person@example.com", "role": "editor", "is_email_verified": True})
    with pytest.raises(HTTPException) as exc:
        await login("user")
    assert exc.value.status_code == 409


async def test_duplicate_prevention_reuses_google_id(google_setup):
    existing = {"_id": ObjectId(), "google_id": "google-123", "email": "person@example.com", "role": "user", "is_email_verified": True}
    google_setup.documents.extend([existing])
    first = await login()
    second = await login()
    assert first["user"]["_id"] == second["user"]["_id"]
    assert google_setup.inserts == 0
