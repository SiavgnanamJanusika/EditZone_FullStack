from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bson import ObjectId
from fastapi import HTTPException, Response
from pymongo.errors import PyMongoError
from starlette.requests import Request

from app.core.security import get_current_user
from app.routers.user_router import delete_my_account
from app.routers.user_router import account_router
from app.schemas.schemas import AccountDeletionBody
from app.services.account_deletion_service import deletion_blockers, hard_delete_account


class AsyncListCursor:
    def __init__(self, documents):
        self.documents = documents

    async def to_list(self, _length):
        return self.documents


def collection_mock(*, deleted_count=1):
    collection = MagicMock()
    collection.delete_many = AsyncMock(return_value=SimpleNamespace(deleted_count=deleted_count))
    collection.delete_one = AsyncMock(return_value=SimpleNamespace(deleted_count=deleted_count))
    collection.update_many = AsyncMock()
    collection.update_one = AsyncMock()
    return collection


@contextmanager
def hard_delete_dependencies(*, user_deleted=1, pending=None):
    names = [
        "auth_sessions_col", "messages_col", "media_agreements_col", "reviews_col",
        "editors_col", "notifications_col", "otps_col",
        "identity_rate_limits_col", "identity_audit_logs_col", "auth_security_events_col",
        "auth_rate_limits_col", "account_deletion_audit_logs_col",
        "legacy_editor_profiles_col", "editor_statuses_col", "status_likes_col", "status_views_col",
    ]
    mocks = {name: collection_mock() for name in names}
    mocks["requests_col"] = collection_mock()
    mocks["requests_col"].find.return_value = AsyncListCursor(pending or [])
    mocks["editor_statuses_col"].find.return_value = AsyncListCursor([])
    mocks["users_col"] = collection_mock(deleted_count=user_deleted)
    mocks["account_deletion_audit_logs_col"].insert_one = AsyncMock()
    with ExitStack() as stack:
        for name, value in mocks.items():
            stack.enter_context(patch(f"app.services.account_deletion_service.{name}", value))
        stack.enter_context(patch(
            "app.services.account_deletion_service._remove_owned_uploads",
            AsyncMock(return_value={"gridfs_deleted": 0, "gridfs_preserved": 0, "s3_deleted": 0, "s3_preserved": 0}),
        ))
        yield mocks


def http_request(cookies=None):
    headers = []
    if cookies:
        headers.append((b"cookie", cookies.encode()))
    return Request({"type": "http", "method": "DELETE", "path": "/api/v1/users/me", "headers": headers, "client": ("127.0.0.1", 1234)})


def account(role="user", password=True):
    value = {"_id": ObjectId(), "email": "member@example.com", "role": role, "is_email_verified": True}
    if password:
        value["password_hash"] = "hash"
    else:
        value.update({"google_id": "google-123", "auth_provider": "google"})
    return value


def test_protected_account_endpoint_is_registered():
    routes = {(method, route.path) for route in account_router.routes for method in route.methods}
    assert ("DELETE", "/api/v1/account") in routes


@pytest.mark.parametrize("role", ["user", "editor"])
async def test_successful_account_deletion(role):
    user = account(role)
    with (
        patch("app.routers.user_router.get_scope_counts", AsyncMock(return_value={"email": 0, "ip": 0})),
        patch("app.routers.user_router.increment_counter", AsyncMock()),
        patch("app.routers.user_router.verify_password", return_value=True),
        patch("app.routers.user_router.deletion_blockers", AsyncMock(return_value=[])),
        patch("app.routers.user_router.hard_delete_account", AsyncMock(return_value={"deleted": True})) as remove,
        patch("app.routers.user_router.purge_otp_cache", AsyncMock()),
        patch("app.routers.user_router.editors_col.find_one", AsyncMock(return_value=None)),
        patch("app.routers.user_router.disconnect_user", AsyncMock()),
        patch("app.routers.user_router.send_account_deletion_email", AsyncMock()),
    ):
        response = Response()
        result = await delete_my_account(AccountDeletionBody(confirmation="DELETE", password="Password1"), http_request(), response, user)
    assert result["message"] == "Your account has been deleted successfully."
    remove.assert_awaited_once()
    assert "ez_access_token=" in response.headers.get("set-cookie", "")


async def test_wrong_password_is_rejected():
    user = account()
    with (
        patch("app.routers.user_router.get_scope_counts", AsyncMock(return_value={"email": 0, "ip": 0})),
        patch("app.routers.user_router.increment_counter", AsyncMock()),
        patch("app.routers.user_router.verify_password", return_value=False),
    ):
        with pytest.raises(HTTPException) as exc:
            await delete_my_account(AccountDeletionBody(confirmation="DELETE", password="wrong"), http_request(), Response(), user)
    assert exc.value.status_code == 401


async def test_google_reauthentication_failure():
    user = account(password=False)
    with (
        patch("app.routers.user_router.get_scope_counts", AsyncMock(return_value={"email": 0, "ip": 0})),
        patch("app.routers.user_router.increment_counter", AsyncMock()),
        patch("app.routers.user_router._verify_google_credential", return_value={"sub": "other", "email": user["email"]}),
    ):
        with pytest.raises(HTTPException) as exc:
            await delete_my_account(AccountDeletionBody(confirmation="DELETE", google_credential="fresh-token"), http_request(), Response(), user)
    assert exc.value.status_code == 401


async def test_active_project_prevents_deletion():
    user = account()
    with (
        patch("app.routers.user_router.get_scope_counts", AsyncMock(return_value={"email": 0, "ip": 0})),
        patch("app.routers.user_router.increment_counter", AsyncMock()),
        patch("app.routers.user_router.verify_password", return_value=True),
        patch("app.routers.user_router.deletion_blockers", AsyncMock(return_value=["Complete or cancel all active and pending projects"])),
    ):
        with pytest.raises(HTTPException) as exc:
            await delete_my_account(AccountDeletionBody(confirmation="DELETE", password="Password1"), http_request(), Response(), user)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "ACTIVE_PROJECT"


async def test_mongodb_failure_returns_safe_specific_error():
    user = account()
    with (
        patch("app.routers.user_router.get_scope_counts", AsyncMock(return_value={"email": 0, "ip": 0})),
        patch("app.routers.user_router.increment_counter", AsyncMock()),
        patch("app.routers.user_router.verify_password", return_value=True),
        patch("app.routers.user_router.deletion_blockers", AsyncMock(side_effect=PyMongoError("database unavailable"))),
    ):
        with pytest.raises(HTTPException) as exc:
            await delete_my_account(AccountDeletionBody(confirmation="DELETE", current_password="Password1"), http_request(), Response(), user)
    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == "DATABASE_ERROR"
    assert "database unavailable" not in exc.value.detail["message"]


async def test_socket_disconnect_failure_does_not_turn_completed_deletion_into_failure():
    user = account()
    with (
        patch("app.routers.user_router.get_scope_counts", AsyncMock(return_value={"email": 0, "ip": 0})),
        patch("app.routers.user_router.increment_counter", AsyncMock()),
        patch("app.routers.user_router.verify_password", return_value=True),
        patch("app.routers.user_router.deletion_blockers", AsyncMock(return_value=[])),
        patch("app.routers.user_router.hard_delete_account", AsyncMock(return_value={"deleted": True})),
        patch("app.routers.user_router.purge_otp_cache", AsyncMock()),
        patch("app.routers.user_router.editors_col.find_one", AsyncMock(return_value=None)),
        patch("app.routers.user_router.disconnect_user", AsyncMock(side_effect=RuntimeError("socket unavailable"))),
        patch("app.routers.user_router.send_account_deletion_email", AsyncMock()),
    ):
        result = await delete_my_account(AccountDeletionBody(confirmation="DELETE", current_password="Password1"), http_request(), Response(), user)
    assert result == {"success": True, "message": "Your account has been deleted successfully."}


async def test_pending_escrow_prevents_deletion():
    user = account()
    counts = [1, 0]
    with (
        patch("app.services.account_deletion_service.requests_col.count_documents", AsyncMock(return_value=0)),
        patch("app.services.account_deletion_service.payments_col.count_documents", AsyncMock(side_effect=lambda *_args, **_kwargs: counts.pop(0))),
        patch("app.services.account_deletion_service.disputes_col.count_documents", AsyncMock(return_value=0)),
    ):
        blockers = await deletion_blockers(user)
    assert "Resolve pending or protected escrow payments" in blockers


async def test_hard_delete_revokes_tokens_and_removes_personal_collections():
    user = account()
    with hard_delete_dependencies() as mocks:
        await hard_delete_account(user, method="password", reason=None, ip_address="127.0.0.1")
    assert mocks["auth_sessions_col"].update_many.await_args.args[1]["$set"]["revoke_reason"] == "account_deleted"
    mocks["auth_sessions_col"].delete_many.assert_awaited_once_with({"user_id": user["_id"]})
    mocks["otps_col"].delete_many.assert_awaited_once_with({"email": user["email"]})
    mocks["notifications_col"].delete_many.assert_awaited_once_with({"user_id": user["_id"]})
    mocks["editors_col"].delete_many.assert_awaited_once_with({"user_id": user["_id"]})
    mocks["users_col"].delete_one.assert_awaited_once_with({
        "_id": user["_id"], "role": {"$in": ["user", "editor"]},
    })


@pytest.mark.parametrize("registration_complete", [False, True])
async def test_incomplete_and_completed_profiles_are_permanently_deleted(registration_complete):
    user = {**account(), "registration_complete": registration_complete, "nic": "200012345678", "phone": "0712345678"}
    with hard_delete_dependencies() as mocks:
        await hard_delete_account(user, method="password", reason=None, ip_address="127.0.0.1")
    assert mocks["users_col"].delete_one.await_count == 1
    message_update = mocks["messages_col"].update_many.await_args_list[-1].args[1]
    assert message_update["$set"]["sender_display_name"] == "Deleted User"


def test_wrong_confirmation_is_rejected_by_schema():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        AccountDeletionBody(confirmation="delete", current_password="Password1")


async def test_repeated_deletion_request_is_gone():
    user = account()
    with hard_delete_dependencies(user_deleted=0):
        with pytest.raises(HTTPException) as exc:
            await hard_delete_account(user, method="password", reason=None, ip_address="127.0.0.1")
    assert exc.value.status_code == 410


async def test_admin_account_is_never_deleted_or_anonymized():
    admin = account("admin")
    with patch("app.services.account_deletion_service.users_col", collection_mock()) as users:
        with pytest.raises(HTTPException) as exc:
            await hard_delete_account(admin, method="password", reason=None, ip_address="127.0.0.1")
    assert exc.value.status_code == 403
    users.delete_one.assert_not_awaited()


async def test_unauthorized_deletion_request():
    with pytest.raises(HTTPException) as exc:
        await get_current_user(http_request(), None)
    assert exc.value.status_code == 401


async def test_deleted_account_cannot_authenticate():
    deleted = {**account(), "is_deleted": True}
    token = "token"
    with (
        patch("app.core.security.decode_token", return_value={"type": "access", "sub": str(deleted["_id"])}),
        patch("app.core.security.users_col.find_one", AsyncMock(return_value=deleted)),
    ):
        with pytest.raises(HTTPException) as exc:
            await get_current_user(http_request(), token)
    assert exc.value.status_code == 403
    assert exc.value.detail == "This account has been deleted by the administrator. Please contact support."
