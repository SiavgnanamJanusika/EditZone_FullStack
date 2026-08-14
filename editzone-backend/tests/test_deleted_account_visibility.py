from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bson import ObjectId
from fastapi import HTTPException

from app.routers.admin_router import archived_accounts, list_users
from app.routers.editor_router import get_editor_profile, list_editors
from app.routers.request_router import create_request
from app.schemas.schemas import CreateRequestBody


class Cursor:
    def __init__(self, docs): self.docs = docs
    def sort(self, *_args): return self
    def limit(self, *_args): return self
    async def to_list(self, _length): return self.docs


def request_body(editor_id):
    return CreateRequestBody(
        editor_id=str(editor_id), project_title="A valid project title",
        project_description="A sufficiently detailed project description for testing visibility.",
        content_type="YouTube", requested_revision_limit=2,
    )


async def test_deleted_editor_excluded_from_client_listing_and_search():
    deleted_user_id = ObjectId()
    editor = {"_id": ObjectId(), "user_id": deleted_user_id, "bio": "video"}
    editors = MagicMock(); editors.find.return_value = Cursor([editor])
    users = MagicMock(); users.find_one = AsyncMock(return_value=None); users.find.return_value = Cursor([])
    with patch("app.routers.editor_router.editors_col", editors), patch("app.routers.editor_router.users_col", users):
        result = await list_editors(category=None, search="video")
    assert result == {"editors": [], "count": 0}
    assert users.find.call_args.args[0]["status"] == {"$ne": "deleted"}


async def test_direct_deleted_profile_access_is_blocked_with_safe_contract():
    editor_id = ObjectId()
    editors = MagicMock(); editors.find_one = AsyncMock(return_value=None)
    with patch("app.routers.editor_router.editors_col", editors):
        with pytest.raises(HTTPException) as exc:
            await get_editor_profile(str(editor_id))
    assert exc.value.status_code == 404
    assert exc.value.detail["code"] == "ACCOUNT_NOT_AVAILABLE"


async def test_deleted_editor_cannot_receive_a_new_request():
    editor_id = ObjectId()
    editors = MagicMock(); editors.find_one = AsyncMock(return_value=None)
    current = {"_id": ObjectId(), "role": "user", "username": "Client"}
    with (
        patch("app.routers.request_router.editors_col", editors),
    ):
        with pytest.raises(HTTPException) as exc:
            await create_request(request_body(editor_id), current)
    assert exc.value.detail["code"] == "ACCOUNT_NOT_AVAILABLE"


async def test_admin_active_list_excludes_deleted_and_archive_is_separate():
    users = MagicMock(); users.find.return_value = Cursor([])
    audits = MagicMock(); audits.find.return_value = Cursor([{
        "_id": ObjectId(), "account_id": ObjectId(), "role": "editor",
    }])
    with patch("app.routers.admin_router.users_col", users):
        assert await list_users({"role": "admin"}) == {"users": []}
    query = users.find.call_args.args[0]
    assert query["role"] == "user" and query["status"] == {"$ne": "deleted"}
    with patch("app.routers.admin_router.account_deletion_audit_logs_col", audits):
        result = await archived_accounts({"role": "admin"})
    assert result["accounts"][0]["role"] == "editor"
