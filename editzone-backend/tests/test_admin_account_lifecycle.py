from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from bson import ObjectId
from fastapi import HTTPException

from app.services.admin_account_service import admin_delete_account, admin_restore_account


def result(modified=1):
    return SimpleNamespace(modified_count=modified)


@pytest.mark.parametrize("role", ["user", "editor"])
async def test_admin_deletes_user_or_editor_and_revokes_sessions(role):
    account = {"_id": ObjectId(), "role": role, "status": "active"}
    admin = {"_id": ObjectId(), "role": "admin"}
    with (
        patch("app.services.admin_account_service.users_col.update_one", AsyncMock(return_value=result())) as update_user,
        patch("app.services.admin_account_service.auth_sessions_col.update_many", AsyncMock()) as revoke,
        patch("app.services.admin_account_service.editors_col.update_one", AsyncMock()) as update_editor,
        patch("app.services.admin_account_service.account_deletion_audit_logs_col.insert_one", AsyncMock()) as audit,
    ):
        response = await admin_delete_account(account, admin, reason="Policy violation", ip_address="127.0.0.1")
    assert response["status"] == "deleted"
    fields = update_user.await_args.args[1]["$set"]
    assert fields["status"] == "deleted" and fields["is_active"] is False
    assert fields["deleted_by"] == admin["_id"] and fields["deletion_reason"] == "Policy violation"
    assert revoke.await_args.args[1]["$set"]["revoke_reason"] == "admin_account_deleted"
    assert update_editor.await_count == (1 if role == "editor" else 0)
    assert audit.await_args.args[0]["action"] == "admin_delete"


async def test_admin_restore_does_not_restore_revoked_sessions():
    account = {"_id": ObjectId(), "role": "user", "status": "deleted", "is_deleted": True}
    admin = {"_id": ObjectId(), "role": "admin"}
    with (
        patch("app.services.admin_account_service.users_col.update_one", AsyncMock(return_value=result())) as update_user,
        patch("app.services.admin_account_service.auth_sessions_col.update_many", AsyncMock()) as sessions,
        patch("app.services.admin_account_service.account_deletion_audit_logs_col.insert_one", AsyncMock()),
    ):
        response = await admin_restore_account(account, admin, reason="Appeal approved", ip_address="127.0.0.1")
    assert response["status"] == "active"
    assert update_user.await_args.args[1]["$set"]["is_active"] is True
    sessions.assert_not_awaited()


async def test_admin_accounts_cannot_be_deleted_by_account_lifecycle():
    with pytest.raises(HTTPException) as exc:
        await admin_delete_account(
            {"_id": ObjectId(), "role": "admin"},
            {"_id": ObjectId(), "role": "admin"},
            reason="Invalid attempt",
            ip_address="127.0.0.1",
        )
    assert exc.value.status_code == 403
