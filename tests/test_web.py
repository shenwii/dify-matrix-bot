"""Unit tests for the built-in scheduled task web UI and REST API endpoints."""

from __future__ import annotations

import asyncio
import unittest

from tests._stubs import (
    ensure_apscheduler_stub,
    ensure_fastapi_pydantic_stub,
    ensure_httpx_stub,
)

ensure_apscheduler_stub()
ensure_httpx_stub()
ensure_fastapi_pydantic_stub()

from fastapi import HTTPException

from app.config import Settings
from app.database import Database
from app.scheduler import TaskScheduler
from app.web import CreateTaskRequest, create_web_router


class _DummySender:
    def validate_task_target(self, room_id: str, username: str) -> tuple[str, str]:
        return room_id, username

    async def deliver_task_result(self, room_id: str, username: str, text: str) -> None:
        pass


def _find_route(router, method: str, path: str):
    if hasattr(router, "routes") and isinstance(router.routes, dict):
        return router.routes[(method, path)]
    for route in router.routes:
        if route.path == path and method in route.methods:
            return route.endpoint
    raise KeyError((method, path))


class TestWebRouter(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Database(":memory:")
        self.settings = Settings(
            matrix_homeserver="https://matrix.example.org",
            matrix_user_id="@bot:example.org",
            matrix_access_token="tok",
            matrix_password="",
            matrix_device_id="DEV",
            matrix_auto_join=True,
            matrix_group_context_limit=10,
            matrix_command_prefix="",
            matrix_typing_interval_seconds=15,
            matrix_typing_timeout_ms=30000,
            dify_base_url="https://api.dify.ai/v1",
            dify_api_key="key",
            dify_timeout_seconds=60.0,
            dify_scheduled_user="scheduler",
            timezone="Asia/Shanghai",
            database_path=":memory:",
            host="0.0.0.0",
            port=8000,
            log_level="INFO",
            mcp_auth_token="",
        )
        self.scheduler = TaskScheduler(
            settings=self.settings,
            db=self.db,
            dify_client=object(),  # type: ignore[arg-type]
            matrix_sender=_DummySender(),
        )
        self.router = create_web_router(lambda: self.scheduler)

    def tearDown(self) -> None:
        self.scheduler.stop()
        self.db.close()

    def test_index_html_and_crud_endpoints(self) -> None:
        index_fn = _find_route(self.router, "GET", "/")
        html_resp = asyncio.run(index_fn())
        body = getattr(html_resp, "content", None) or getattr(html_resp, "body", b"").decode()
        self.assertIn("Dify Matrix Bot - Scheduled Tasks", body)

        list_fn = _find_route(self.router, "GET", "/api/tasks")
        create_fn = _find_route(self.router, "POST", "/api/tasks")
        delete_fn = _find_route(self.router, "DELETE", "/api/tasks/{task_uuid}")

        self.assertEqual(asyncio.run(list_fn())["count"], 0)

        created = asyncio.run(
            create_fn(
                CreateTaskRequest(
                    title="Standup",
                    cron="0 10 * * *",
                    content="Post standup reminder",
                    is_one_time=False,
                    username="@alice:example.org",
                    room_id="!room:example.org",
                )
            )
        )
        task_uuid = created["task"]["uuid"]
        self.assertEqual(asyncio.run(list_fn())["count"], 1)

        with self.assertRaises(HTTPException) as bad_cron:
            asyncio.run(
                create_fn(
                    CreateTaskRequest(
                        title="Bad",
                        cron="bad cron",
                        content="x",
                        is_one_time=False,
                        username="",
                        room_id="",
                    )
                )
            )
        self.assertEqual(bad_cron.exception.status_code, 400)

        with self.assertRaises(HTTPException) as bad_room:
            asyncio.run(
                create_fn(
                    CreateTaskRequest(
                        title="Bad Room",
                        cron="0 10 * * *",
                        content="x",
                        is_one_time=False,
                        username="",
                        room_id="not_a_room",
                    )
                )
            )
        self.assertEqual(bad_room.exception.status_code, 400)

        deleted = asyncio.run(delete_fn(task_uuid))
        self.assertEqual(deleted["status"], "deleted")

        with self.assertRaises(HTTPException) as missing:
            asyncio.run(delete_fn("nonexistent"))
        self.assertEqual(missing.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
