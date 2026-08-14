from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bson import ObjectId
from fastapi import HTTPException

from app.routers.editor_router import add_portfolio_item, update_portfolio_item
from app.schemas.schemas import PortfolioItemBody, PortfolioItemUpdate


@pytest.mark.asyncio
async def test_editor_creates_owned_portfolio_media_record():
    editor_id, upload_id, item_id = ObjectId(), ObjectId(), ObjectId()
    uploads = MagicMock()
    uploads.find_one = AsyncMock(return_value={
        "_id": upload_id, "filename": "safe.webp", "length": 321,
        "metadata": {"owner_id": editor_id, "purpose": "editor_portfolio", "scan_status": "safe",
                     "state": "safe", "category": "image", "content_type": "image/webp", "size": 321},
    })
    database = MagicMock(); database.__getitem__.return_value = uploads
    portfolio = MagicMock(); portfolio.count_documents = AsyncMock(return_value=0)
    portfolio.insert_one = AsyncMock(return_value=MagicMock(inserted_id=item_id))
    editors = MagicMock(); editors.find_one = AsyncMock(return_value={"portfolio_links": []}); editors.update_one = AsyncMock()
    with patch("app.routers.editor_router.db", database), patch("app.routers.editor_router.editor_portfolio_items_col", portfolio), patch("app.routers.editor_router.editors_col", editors):
        result = await add_portfolio_item(PortfolioItemBody(upload_id=str(upload_id), title="Showreel", skills=["Premiere"]), {"_id": editor_id, "role": "editor"})
    assert result["id"] == str(item_id)
    assert result["editor_id"] == str(editor_id)
    assert result["media_type"] == "image"
    assert result["url"].endswith("safe.webp")


@pytest.mark.asyncio
async def test_editor_cannot_update_another_editors_portfolio():
    collection = MagicMock(); collection.find_one_and_update = AsyncMock(return_value=None)
    with patch("app.routers.editor_router.editor_portfolio_items_col", collection):
        with pytest.raises(HTTPException) as exc:
            await update_portfolio_item(str(ObjectId()), PortfolioItemUpdate(title="Changed"), {"_id": ObjectId(), "role": "editor"})
    assert exc.value.status_code == 404
    query = collection.find_one_and_update.await_args.args[0]
    assert query["editor_id"] is not None
