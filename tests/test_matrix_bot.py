"""Unit tests for Matrix mention detection, stripping, and group context formatting."""

from __future__ import annotations

import asyncio
import tempfile
import types
import unittest

from tests._stubs import ensure_httpx_stub, ensure_markdown_stub, ensure_nio_stub

ensure_httpx_stub()
ensure_markdown_stub()
ensure_nio_stub()

from app.config import Settings
from app.database import Database, RoomMessageRecord
from app.matrix_bot import (
    MatrixBot,
    build_matrix_message_content,
    format_room_history,
    is_bot_mentioned,
    strip_bot_mention,
)


class TestMatrixBotHelpers(unittest.TestCase):
    def test_mention_detection_variants(self) -> None:
        bot_id = "@difybot:example.org"

        # 1. Standard MSC3952 m.mentions
        self.assertTrue(
            is_bot_mentioned(
                body="hello",
                formatted_body=None,
                source={"content": {"m.mentions": {"user_ids": [bot_id]}}},
                bot_user_id=bot_id,
            )
        )
        # 2. matrix.to link in formatted_body
        self.assertTrue(
            is_bot_mentioned(
                body="DifyBot: summary please",
                formatted_body=f'<a href="https://matrix.to/#/{bot_id}">DifyBot</a>: summary please',
                source={},
                bot_user_id=bot_id,
                bot_display_name="DifyBot",
            )
        )
        # 3. Plain @localpart
        self.assertTrue(
            is_bot_mentioned(
                body="@difybot 帮我查一下",
                formatted_body=None,
                source={},
                bot_user_id=bot_id,
            )
        )
        # 4. Command prefix
        self.assertTrue(
            is_bot_mentioned(
                body="!ai 总结一下",
                formatted_body=None,
                source={},
                bot_user_id=bot_id,
                command_prefix="!ai",
            )
        )
        # 5. Unrelated message
        self.assertFalse(
            is_bot_mentioned(
                body="大家下午好",
                formatted_body=None,
                source={},
                bot_user_id=bot_id,
                bot_display_name="DifyBot",
                command_prefix="!ai",
            )
        )

    def test_strip_bot_mention(self) -> None:
        self.assertEqual(
            strip_bot_mention(
                body="@difybot: 总结群聊",
                bot_user_id="@difybot:example.org",
                bot_display_name="DifyBot",
            ),
            "总结群聊",
        )
        self.assertEqual(
            strip_bot_mention(
                body="!ai 明天天气如何",
                bot_user_id="@difybot:example.org",
                command_prefix="!ai",
            ),
            "明天天气如何",
        )

    def test_format_room_history(self) -> None:
        self.assertEqual(format_room_history([]), "")
        history = [
            RoomMessageRecord(
                room_id="!r:org",
                event_id="$1",
                sender_id="@alice:org",
                sender_name="Alice",
                body="下午三点开会",
                timestamp_ms=100,
            ),
            RoomMessageRecord(
                room_id="!r:org",
                event_id="$2",
                sender_id="@bob:org",
                sender_name="Bob",
                body="收到",
                timestamp_ms=200,
            ),
        ]
        formatted = format_room_history(history)
        self.assertEqual(
            formatted,
            "Alice (@alice:org): 下午三点开会\nBob (@bob:org): 收到",
        )

    def test_build_matrix_message_content_with_mention(self) -> None:
        payload = build_matrix_message_content(
            text="Task done",
            mention_user_id="@alice:example.org",
            mention_label="Alice",
        )
        self.assertEqual(payload["m.mentions"], {"user_ids": ["@alice:example.org"]})
        self.assertIn("https://matrix.to/#/@alice:example.org", payload["formatted_body"])
        self.assertTrue(payload["body"].startswith("@Alice"))

    def test_deliver_task_result_routing(self) -> None:
        db = Database(":memory:")
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(
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
                matrix_e2ee_enabled=True,
                matrix_store_path=tmpdir,
                matrix_trust_all_devices=True,
            )
            bot = MatrixBot(settings=settings, db=db, dify_client=object())  # type: ignore[arg-type]

            sent_calls: list[dict] = []
            created_dms: list[dict] = []

            async def _fake_send_markdown(
                room_id: str,
                text: str,
                mention_user_id: str | None = None,
                mention_label: str | None = None,
                mention_fallback_name: str | None = None,
            ):
                sent_calls.append(
                    {
                        "room_id": room_id,
                        "text": text,
                        "mention_user_id": mention_user_id,
                        "mention_label": mention_label,
                        "mention_fallback_name": mention_fallback_name,
                    }
                )
                return "$evt"

            async def _fake_room_create(**kwargs):
                created_dms.append(kwargs)
                return types.SimpleNamespace(room_id="!dm_new:example.org")

            bot.send_markdown = _fake_send_markdown  # type: ignore[method-assign]
            bot._client.room_create = _fake_room_create  # type: ignore[attr-defined]

            # Setup a known group room with Alice
            fake_group = types.SimpleNamespace(
                room_id="!group:example.org",
                member_count=5,
                users={"@alice:example.org": types.SimpleNamespace(display_name="Alice")},
                user_name=lambda uid: "Alice" if uid == "@alice:example.org" else uid,
            )
            bot._client.rooms = {"!group:example.org": fake_group}

            # 1. Both empty -> silent (no message sent)
            asyncio.run(bot.deliver_task_result("", "", "hello"))
            self.assertEqual(len(sent_calls), 0)

            # 2. Group room + empty user_id -> send to group without mention
            asyncio.run(bot.deliver_task_result("!group:example.org", "", "group notice"))
            self.assertEqual(sent_calls[-1]["room_id"], "!group:example.org")
            self.assertIsNone(sent_calls[-1]["mention_user_id"])
            self.assertIsNone(sent_calls[-1]["mention_fallback_name"])

            # 3. Group room + user_id (or display name resolved to MXID) -> send to group and @mention user
            asyncio.run(bot.deliver_task_result("!group:example.org", "Alice", "hi alice"))
            self.assertEqual(sent_calls[-1]["room_id"], "!group:example.org")
            self.assertEqual(sent_calls[-1]["mention_user_id"], "@alice:example.org")
            self.assertEqual(sent_calls[-1]["mention_label"], "Alice")

            # 4. Empty room_id + user_id -> create/find encrypted DM and send directly
            asyncio.run(bot.deliver_task_result("", "@bob:example.org", "dm bob"))
            self.assertEqual(len(created_dms), 1)
            self.assertEqual(created_dms[0]["invite"], ["@bob:example.org"])
            self.assertEqual(
                created_dms[0]["initial_state"][0]["type"], "m.room.encryption"
            )
            self.assertEqual(sent_calls[-1]["room_id"], "!dm_new:example.org")
            self.assertIsNone(sent_calls[-1]["mention_user_id"])

            # 5. Live validation against connected bot state catches hallucinations
            bot._connected = True
            self.assertEqual(
                bot.validate_task_target("!group:example.org", "Alice"),
                ("!group:example.org", "@alice:example.org"),
            )
            self.assertEqual(
                bot.validate_task_target("null", "$user_id"),
                ("", ""),
            )
            with self.assertRaises(ValueError):
                bot.validate_task_target("!not_joined:example.org", "")
            with self.assertRaises(ValueError):
                bot.validate_task_target("!group:example.org", "ghost_user")
            with self.assertRaises(ValueError):
                bot.validate_task_target("", "@ghost:example.org")

            # 6. Stale or invalid delivery targets do not raise at runtime
            before_count = len(sent_calls)
            asyncio.run(bot.deliver_task_result("not_a_room", "", "should skip"))
            asyncio.run(bot.deliver_task_result("!not_joined:example.org", "", "should skip"))
            self.assertEqual(len(sent_calls), before_count)

            # 7. Auto-trust devices verifies unverified devices in device_store
            fake_device = types.SimpleNamespace(verified=False)
            verified_calls: list[object] = []
            bot._client.device_store = types.SimpleNamespace(
                users=["@alice:example.org"],
                active_user_devices=lambda uid: [fake_device] if uid == "@alice:example.org" else [],
            )
            bot._client.verify_device = lambda dev: verified_calls.append(dev)
            bot._trust_devices(["@alice:example.org"])
            self.assertEqual(verified_calls, [fake_device])
        db.close()


if __name__ == "__main__":
    unittest.main()
