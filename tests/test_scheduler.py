"""Unit tests for TaskScheduler CRUD, one-time cleanup, and Dify-to-Matrix execution."""

from __future__ import annotations

import asyncio
import unittest

from tests._stubs import ensure_apscheduler_stub, ensure_httpx_stub

ensure_apscheduler_stub()
ensure_httpx_stub()

from app.config import Settings
from app.database import Database
from app.dify_client import DifyChatResult
from app.scheduler import TaskScheduler


class _FakeDifyClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def chat(self, query: str, user: str, conversation_id=None, inputs=None):
        self.calls.append({"query": query, "user": user, "inputs": inputs or {}})
        return DifyChatResult(answer="Task executed OK", conversation_id=None, message_id="m1")


class _FakeMatrixSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    async def deliver_task_result(
        self, room_id: str, username: str, text: str
    ) -> None:
        self.sent.append((room_id, username, text))


class TestTaskScheduler(unittest.TestCase):
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
            dify_scheduler_inputs_template={
                "username": "$username",
                "room_id": "$room_id",
            },
        )
        self.dify = _FakeDifyClient()
        self.matrix = _FakeMatrixSender()
        self.scheduler = TaskScheduler(
            settings=self.settings,
            db=self.db,
            dify_client=self.dify,  # type: ignore[arg-type]
            matrix_sender=self.matrix,
        )

    def tearDown(self) -> None:
        self.scheduler.stop()
        self.db.close()

    def test_create_list_delete_and_invalid_cron(self) -> None:
        with self.assertRaises(ValueError):
            self.scheduler.create_task("Bad", "invalid cron", "do work", False)

        created = self.scheduler.create_task(
            title="Daily Digest",
            cron="0 9 * * *",
            content="Send daily summary",
            is_one_time=False,
        )
        self.assertTrue(created["uuid"])
        self.assertEqual(len(self.scheduler.list_tasks()), 1)
        self.assertTrue(self.scheduler.delete_task(created["uuid"]))
        self.assertEqual(len(self.scheduler.list_tasks()), 0)

    def test_one_time_task_executes_and_cleans_up(self) -> None:
        created = self.scheduler.create_task(
            title="One-off Reminder",
            cron="30 14 * * *",
            content="Remind team about release",
            is_one_time=True,
            username="@alice:example.org",
            room_id="!target:example.org",
        )
        asyncio.run(self.scheduler._execute_task(created["uuid"]))

        self.assertEqual(len(self.dify.calls), 1)
        self.assertIn("Remind team about release", self.dify.calls[0]["query"])
        self.assertEqual(
            self.dify.calls[0]["inputs"],
            {"username": "@alice:example.org", "room_id": "!target:example.org"},
        )
        self.assertEqual(len(self.matrix.sent), 1)
        self.assertEqual(self.matrix.sent[0][0], "!target:example.org")
        self.assertEqual(self.matrix.sent[0][1], "@alice:example.org")
        self.assertIn("One-off Reminder", self.matrix.sent[0][2])
        self.assertIn("Task executed OK", self.matrix.sent[0][2])
        self.assertEqual(self.scheduler.list_tasks(), [])

    def test_silent_task_when_username_and_room_id_empty(self) -> None:
        created = self.scheduler.create_task(
            title="Silent Job",
            cron="0 3 * * *",
            content="Run background maintenance",
            is_one_time=True,
        )
        asyncio.run(self.scheduler._execute_task(created["uuid"]))

        self.assertEqual(len(self.dify.calls), 1)
        self.assertEqual(
            self.dify.calls[0]["inputs"],
            {"username": "", "room_id": ""},
        )
        self.assertEqual(len(self.matrix.sent), 0)
        self.assertEqual(self.scheduler.list_tasks(), [])

    def test_placeholder_normalization_and_invalid_target_rejection(self) -> None:
        # LLM placeholder strings normalize cleanly to empty strings
        created = self.scheduler.create_task(
            title="Normalized",
            cron="0 8 * * *",
            content="Check status",
            username="$username",
            room_id="null",
        )
        self.assertEqual(created["username"], "")
        self.assertEqual(created["room_id"], "")

        # Hallucinated room_id format is rejected at creation time
        with self.assertRaises(ValueError):
            self.scheduler.create_task(
                title="Bad Room",
                cron="0 8 * * *",
                content="Check status",
                room_id="general_chat",
            )

        # Malformed MXID with whitespace is rejected at creation time
        with self.assertRaises(ValueError):
            self.scheduler.create_task(
                title="Bad User",
                cron="0 8 * * *",
                content="Check status",
                username="@alice user:example.org",
            )


if __name__ == "__main__":
    unittest.main()
