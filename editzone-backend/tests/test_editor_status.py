from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bson import ObjectId
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.utils import now_utc
from pymongo.errors import DuplicateKeyError

from app.core.security import require_roles
from app.routers.upload_router import CHAT_IMAGE_TYPES, CHAT_VIDEO_MIME_TYPES, PURPOSES, _matches_magic, _media_status_payload
from app.routers.status_router import (
    StatusCreate, _active_filter, _people, _resolve_editor_user_id,
    _status_or_404, create_status, delete_status, like_status, record_view, unlike_status,
)


def test_caption_limit_is_enforced():
    with pytest.raises(ValidationError):
        StatusCreate(upload_id=str(ObjectId()), caption="x" * 301)


def test_active_filter_uses_server_expiration_and_active_flag():
    now = now_utc()
    query = _active_filter(now)
    assert query == {"is_active": True, "expires_at": {"$gt": now}}
    assert now + timedelta(hours=24) > query["expires_at"]["$gt"]


def test_status_upload_policy_accepts_only_supported_media_signatures():
    assert "editor_status" in PURPOSES
    assert {"video/mp4", "video/webm", "video/quicktime"} == CHAT_VIDEO_MIME_TYPES
    assert {"image/jpeg", "image/png", "image/webp"} == set(CHAT_IMAGE_TYPES.values())
    assert _matches_magic("image", b"not-an-image") is False
    assert _matches_magic("video", b"not-a-video") is False


def test_canonical_media_status_maps_legacy_scan_states_without_exposing_url():
    assert _media_status_payload("one", "safe", url="/private") ["status"] == "ready"
    pending = _media_status_payload("two", "pending", url="/must-not-leak")
    assert pending["status"] == "uploaded"
    assert pending["url"] is None
    failed = _media_status_payload("three", "scan_failed", error_code="connection_refused")
    assert failed["status"] == "failed"
    assert failed["retryable"] is True


@pytest.mark.asyncio
async def test_private_insights_reject_non_owner():
    owner_id, viewer_id, status_id = ObjectId(), ObjectId(), ObjectId()
    with patch("app.routers.status_router.editor_statuses_col.find_one", AsyncMock(return_value={"_id": status_id, "editor_id": owner_id})):
        with pytest.raises(HTTPException) as raised:
            await _people(str(status_id), {"_id": viewer_id, "role": "user"}, "likes")
    assert raised.value.status_code == 403


@pytest.mark.asyncio
async def test_private_insights_allow_owner_with_empty_result():
    owner_id, status_id = ObjectId(), ObjectId()
    relation_cursor = MagicMock()
    relation_cursor.sort.return_value.limit.return_value.to_list = AsyncMock(return_value=[])
    editor_cursor = MagicMock()
    editor_cursor.to_list = AsyncMock(return_value=[])
    with (
        patch("app.routers.status_router.editor_statuses_col.find_one", AsyncMock(return_value={"_id": status_id, "editor_id": owner_id})),
        patch("app.routers.status_router.status_views_col.find", return_value=relation_cursor),
        patch("app.routers.status_router.editors_col.find", return_value=editor_cursor),
    ):
        result = await _people(str(status_id), {"_id": owner_id, "role": "editor"}, "views")
    assert result == {"users": [], "count": 0}


@pytest.mark.asyncio
async def test_client_cannot_use_editor_creation_dependency():
    dependency = require_roles(["editor"])
    with pytest.raises(HTTPException) as raised:
        await dependency({"role": "user"})
    assert raised.value.status_code == 403


@pytest.mark.asyncio
async def test_editor_can_create_status_from_owned_safe_upload():
    editor_id, upload_id, status_id = ObjectId(), ObjectId(), ObjectId()
    upload_collection = MagicMock()
    upload_collection.find_one = AsyncMock(return_value={
        "_id": upload_id, "filename": "safe.mp4",
        "metadata": {"owner_id": editor_id, "purpose": "editor_status", "scan_status": "safe", "state": "safe", "category": "video"},
    })
    database = MagicMock()
    database.__getitem__.return_value = upload_collection
    inserted = MagicMock(inserted_id=status_id)
    with (
        patch("app.routers.status_router.db", database),
        patch("app.routers.status_router.gridfs_video_duration", AsyncMock(return_value=45.5)),
        patch("app.routers.status_router.editor_statuses_col.insert_one", AsyncMock(return_value=inserted)) as insert,
        patch("app.routers.status_router._serialize_statuses", AsyncMock(return_value=[{"id": str(status_id)}])),
    ):
        result = await create_status(StatusCreate(upload_id=str(upload_id), caption="  New edit  "), {"_id": editor_id, "role": "editor"})
    document = insert.await_args.args[0]
    assert result == {"id": str(status_id)}
    assert document["editor_id"] == editor_id
    assert document["media_type"] == "video"
    assert document["duration_seconds"] == 45.5
    assert document["caption"] == "New edit"
    assert document["expires_at"] - document["created_at"] == timedelta(hours=24)


@pytest.mark.asyncio
async def test_status_video_over_ninety_seconds_is_rejected_server_side():
    editor_id, upload_id = ObjectId(), ObjectId()
    upload_collection = MagicMock()
    upload_collection.find_one = AsyncMock(return_value={
        "_id": upload_id, "filename": "long.mp4",
        "metadata": {"owner_id": editor_id, "purpose": "editor_status", "scan_status": "safe", "state": "safe", "category": "video"},
    })
    upload_collection.update_one = AsyncMock()
    database = MagicMock(); database.__getitem__.return_value = upload_collection
    with (
        patch("app.routers.status_router.db", database),
        patch("app.routers.status_router.gridfs_video_duration", AsyncMock(return_value=90.01)),
        pytest.raises(HTTPException) as raised,
    ):
        await create_status(StatusCreate(upload_id=str(upload_id)), {"_id": editor_id, "role": "editor"})
    assert raised.value.status_code == 422
    assert raised.value.detail["code"] == "STATUS_VIDEO_TOO_LONG"
    assert upload_collection.update_one.await_args.args[1]["$set"]["metadata.state"] == "rejected"


@pytest.mark.asyncio
async def test_status_video_exactly_ninety_seconds_is_allowed():
    editor_id, upload_id, status_id = ObjectId(), ObjectId(), ObjectId()
    upload_collection = MagicMock()
    upload_collection.find_one = AsyncMock(return_value={"_id": upload_id, "filename": "boundary.mp4", "metadata": {"owner_id": editor_id, "purpose": "editor_status", "scan_status": "safe", "state": "safe", "category": "video"}})
    database = MagicMock(); database.__getitem__.return_value = upload_collection
    inserted = MagicMock(inserted_id=status_id)
    with (
        patch("app.routers.status_router.db", database),
        patch("app.routers.status_router.gridfs_video_duration", AsyncMock(return_value=90.0)),
        patch("app.routers.status_router.editor_statuses_col.insert_one", AsyncMock(return_value=inserted)) as insert,
        patch("app.routers.status_router._serialize_statuses", AsyncMock(return_value=[{"id": str(status_id)}])),
    ):
        await create_status(StatusCreate(upload_id=str(upload_id)), {"_id": editor_id, "role": "editor"})
    assert insert.await_args.args[0]["duration_seconds"] == 90.0


@pytest.mark.asyncio
async def test_expired_status_is_not_accessible():
    with patch("app.routers.status_router.editor_statuses_col.find_one", AsyncMock(return_value=None)) as find:
        with pytest.raises(HTTPException) as raised:
            await _status_or_404(str(ObjectId()))
    assert raised.value.status_code == 404
    assert find.await_args.args[0]["expires_at"]["$gt"]


@pytest.mark.asyncio
async def test_editor_profile_id_resolves_to_owner_user_id():
    profile_id, user_id = ObjectId(), ObjectId()
    with patch("app.routers.status_router.editors_col.find_one", AsyncMock(return_value={"user_id": user_id})):
        assert await _resolve_editor_user_id(str(profile_id)) == user_id


@pytest.mark.asyncio
async def test_duplicate_like_keeps_relationship_count_authoritative():
    status_id, user_id = ObjectId(), ObjectId()
    status = {"_id": status_id, "editor_id": ObjectId(), "like_count": 99}
    with (
        patch("app.routers.status_router._status_or_404", AsyncMock(return_value=status)),
        patch("app.routers.status_router.status_likes_col.insert_one", AsyncMock(side_effect=DuplicateKeyError("duplicate"))),
        patch("app.routers.status_router.status_likes_col.count_documents", AsyncMock(return_value=1)),
        patch("app.routers.status_router.editor_statuses_col.update_one", AsyncMock()) as update,
    ):
        result = await like_status(str(status_id), {"_id": user_id})
    assert result["like_count"] == 1
    assert result["liked_by_me"] is True
    assert update.await_args.args[1]["$set"]["like_count"] == 1


@pytest.mark.asyncio
async def test_duplicate_view_does_not_inflate_unique_count():
    status_id, viewer_id = ObjectId(), ObjectId()
    with (
        patch("app.routers.status_router._status_or_404", AsyncMock(return_value={"_id": status_id})),
        patch("app.routers.status_router.status_views_col.insert_one", AsyncMock(side_effect=DuplicateKeyError("duplicate"))),
        patch("app.routers.status_router.status_views_col.count_documents", AsyncMock(return_value=1)),
        patch("app.routers.status_router.editor_statuses_col.update_one", AsyncMock()),
    ):
        result = await record_view(str(status_id), {"_id": viewer_id})
    assert result == {"view_count": 1, "is_viewed_by_me": True}


@pytest.mark.asyncio
async def test_unlike_never_uses_negative_cached_count():
    status_id, user_id = ObjectId(), ObjectId()
    with (
        patch("app.routers.status_router._status_or_404", AsyncMock(return_value={"_id": status_id, "like_count": 12})),
        patch("app.routers.status_router.status_likes_col.delete_one", AsyncMock(return_value=MagicMock(deleted_count=1))),
        patch("app.routers.status_router.status_likes_col.count_documents", AsyncMock(return_value=0)),
        patch("app.routers.status_router.editor_statuses_col.update_one", AsyncMock()),
    ):
        result = await unlike_status(str(status_id), {"_id": user_id})
    assert result["like_count"] == 0
    assert result["liked_by_me"] is False


@pytest.mark.asyncio
async def test_other_editor_cannot_delete_status():
    owner_id, other_id, status_id = ObjectId(), ObjectId(), ObjectId()
    with patch("app.routers.status_router.editor_statuses_col.find_one", AsyncMock(return_value={"_id": status_id, "editor_id": owner_id})):
        with pytest.raises(HTTPException) as raised:
            await delete_status(str(status_id), {"_id": other_id, "role": "editor"})
    assert raised.value.status_code == 403


@pytest.mark.asyncio
async def test_client_cannot_delete_status():
    owner_id, client_id, status_id = ObjectId(), ObjectId(), ObjectId()
    with patch("app.routers.status_router.editor_statuses_col.find_one", AsyncMock(return_value={"_id": status_id, "editor_id": owner_id})):
        with pytest.raises(HTTPException) as raised:
            await delete_status(str(status_id), {"_id": client_id, "role": "user"})
    assert raised.value.status_code == 403


@pytest.mark.asyncio
async def test_editor_cannot_like_own_status():
    owner_id, status_id = ObjectId(), ObjectId()
    with patch("app.routers.status_router._status_or_404", AsyncMock(return_value={"_id": status_id, "editor_id": owner_id})):
        with pytest.raises(HTTPException) as raised:
            await like_status(str(status_id), {"_id": owner_id, "role": "editor"})
    assert raised.value.status_code == 403


@pytest.mark.asyncio
async def test_owner_can_delete_own_status_and_related_records():
    owner_id, status_id, upload_id = ObjectId(), ObjectId(), ObjectId()
    with (
        patch("app.routers.status_router.editor_statuses_col.find_one", AsyncMock(side_effect=[{"_id": status_id, "editor_id": owner_id, "upload_id": upload_id}, None])),
        patch("app.routers.status_router.editor_statuses_col.delete_one", AsyncMock(return_value=MagicMock(deleted_count=1))),
        patch("app.routers.status_router.status_likes_col.delete_many", AsyncMock()) as likes,
        patch("app.routers.status_router.status_views_col.delete_many", AsyncMock()) as views,
        patch("app.routers.status_router.editor_portfolio_items_col.find_one", AsyncMock(return_value=None)),
        patch("app.routers.status_router.uploads_bucket.delete", AsyncMock()) as media_delete,
    ):
        result = await delete_status(str(status_id), {"_id": owner_id, "role": "editor"})
    assert result["message"] == "Status deleted successfully."
    likes.assert_awaited_once_with({"status_id": status_id})
    views.assert_awaited_once_with({"status_id": status_id})
    media_delete.assert_awaited_once_with(upload_id)
