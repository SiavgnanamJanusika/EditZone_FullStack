import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from bson import ObjectId
from fastapi import HTTPException

from app.core.security import require_roles
from app.main import app, socket_app
from app.sockets.socket_manager import _can_access_request


class RouteContractTests(unittest.TestCase):
    def test_default_uvicorn_entrypoint_includes_socketio(self):
        self.assertIs(app, socket_app)

    def test_critical_workflow_routes_are_registered(self):
        # FastAPI 0.141 keeps included routers as deferred route groups. OpenAPI is
        # the public, flattened route contract and is stable across that change.
        routes = {
            (path, method.upper())
            for path, operations in app.openapi()["paths"].items()
            for method in operations
        }
        expected = {
            ("/api/v1/health", "GET"),
            ("/api/v1/auth/login", "POST"),
            ("/api/v1/auth/verify-otp", "POST"),
            ("/api/v1/chat/{request_id}/messages", "GET"),
            ("/api/v1/uploads", "POST"),
            ("/api/v1/media/{media_id}/status", "GET"),
            ("/api/v1/uploads/multipart/initiate", "POST"),
            ("/api/v1/payments/payhere/initiate", "POST"),
            ("/api/v1/payments/{request_id}/approve", "POST"),
            ("/api/v1/requests/{request_id}/dispute", "POST"),
            ("/api/v1/statuses", "POST"),
            ("/api/v1/statuses", "GET"),
            ("/api/v1/statuses/{status_id}/like", "POST"),
            ("/api/v1/statuses/{status_id}/view", "POST"),
            ("/api/v1/statuses/{status_id}/likes", "GET"),
            ("/api/v1/statuses/{status_id}/viewers", "GET"),
        }
        self.assertTrue(expected <= routes, expected - routes)

    def test_editor_discovery_requires_authenticated_api_access(self):
        operations = app.openapi()["paths"]
        self.assertTrue(operations["/api/v1/editors"]["get"].get("security"))
        self.assertTrue(operations["/api/v1/editors/{editor_id}"]["get"].get("security"))


class RoleAndChatAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_role_dependency_rejects_wrong_role(self):
        dependency = require_roles(["admin"])
        with self.assertRaises(HTTPException) as raised:
            await dependency({"role": "editor"})
        self.assertEqual(raised.exception.status_code, 403)

    async def test_chat_membership_rejects_non_member(self):
        member = ObjectId()
        outsider = ObjectId()
        with (
            patch("app.sockets.socket_manager.sio.get_session", AsyncMock(return_value={"user_id": str(outsider)})),
            patch("app.sockets.socket_manager.requests_col.find_one", AsyncMock(return_value={"user_id": member, "editor_user_id": ObjectId()})),
        ):
            self.assertFalse(await _can_access_request("socket-id", str(ObjectId())))


@unittest.skipUnless(os.getenv("RUN_E2E") == "1", "Set RUN_E2E=1 with the Docker stack running")
class LiveEndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_health_and_anonymous_session(self):
        base_url = os.getenv("E2E_API_URL", "http://127.0.0.1:8000")
        async with httpx.AsyncClient(base_url=base_url) as client:
            health = await client.get("/api/v1/health/live")
            session = await client.get("/api/v1/auth/session")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(session.status_code, 200)
        self.assertFalse(session.json()["authenticated"])
