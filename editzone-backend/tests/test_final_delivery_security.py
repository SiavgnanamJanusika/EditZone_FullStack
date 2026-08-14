import unittest
from unittest.mock import AsyncMock, patch

from bson import ObjectId
from fastapi import HTTPException

from app.routers.upload_router import _authorize_final_delivery


class FinalDeliveryAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_cannot_access_pending_final_video(self):
        client_id = ObjectId()
        delivery = {
            "project_id": "project-1",
            "upload_id": "upload-1",
            "client_id": client_id,
            "editor_id": ObjectId(),
            "delivery_status": "PENDING_ADMIN_REVIEW",
        }
        with patch("app.routers.upload_router.deliveries_col.find_one", AsyncMock(return_value=delivery)):
            with self.assertRaises(HTTPException) as raised:
                await _authorize_final_delivery("upload-1", "project-1", {"_id": client_id, "role": "user"})
        self.assertEqual(raised.exception.status_code, 403)

    async def test_only_owner_client_can_access_released_final_video(self):
        client_id = ObjectId()
        delivery = {
            "project_id": "project-1",
            "upload_id": "upload-1",
            "client_id": client_id,
            "editor_id": ObjectId(),
            "delivery_status": "RELEASED",
        }
        finder = AsyncMock(return_value=delivery)
        with patch("app.routers.upload_router.deliveries_col.find_one", finder):
            result = await _authorize_final_delivery("upload-1", "project-1", {"_id": client_id, "role": "user"})
            self.assertEqual(result, delivery)
            with self.assertRaises(HTTPException) as raised:
                await _authorize_final_delivery("upload-1", "project-1", {"_id": ObjectId(), "role": "user"})
        self.assertEqual(raised.exception.status_code, 403)

    async def test_admin_can_review_pending_final_video(self):
        delivery = {
            "project_id": "project-1",
            "upload_id": "upload-1",
            "client_id": ObjectId(),
            "editor_id": ObjectId(),
            "delivery_status": "PENDING_ADMIN_REVIEW",
        }
        with patch("app.routers.upload_router.deliveries_col.find_one", AsyncMock(return_value=delivery)):
            result = await _authorize_final_delivery("upload-1", "project-1", {"_id": ObjectId(), "role": "admin"})
        self.assertEqual(result, delivery)


if __name__ == "__main__":
    unittest.main()
