"""Unit tests for SQLite persistence layer."""

from __future__ import annotations

import unittest

from app.database import Database


class TestDatabase(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Database(":memory:")

    def tearDown(self) -> None:
        self.db.close()

    def test_scheduled_task_crud(self) -> None:
        task = self.db.create_task(
            task_uuid="uuid-1",
            title="Morning Report",
            cron="0 9 * * *",
            content="Summarize today's tasks",
            is_one_time=False,
            username="@alice:org",
            room_id="!room:org",
        )
        self.assertEqual(task.uuid, "uuid-1")
        self.assertFalse(task.is_one_time)
        self.assertEqual(task.username, "@alice:org")
        self.assertEqual(task.room_id, "!room:org")

        fetched = self.db.get_task("uuid-1")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.title, "Morning Report")
        self.assertEqual(fetched.username, "@alice:org")
        self.assertEqual(fetched.room_id, "!room:org")

        self.db.mark_task_executed("uuid-1")
        updated = self.db.get_task("uuid-1")
        self.assertIsNotNone(updated)
        self.assertEqual(updated.run_count, 1)
        self.assertIsNotNone(updated.last_run_at)

        all_tasks = self.db.list_tasks()
        self.assertEqual(len(all_tasks), 1)

        self.assertTrue(self.db.delete_task("uuid-1"))
        self.assertFalse(self.db.delete_task("uuid-1"))
        self.assertEqual(self.db.list_tasks(), [])

    def test_per_user_conversation_isolation(self) -> None:
        self.db.set_conversation_id("!room1:org", "@alice:org", "conv-a")
        self.db.set_conversation_id("!room1:org", "@bob:org", "conv-b")
        self.db.set_conversation_id("!room2:org", "@alice:org", "conv-c")

        self.assertEqual(
            self.db.get_conversation_id("!room1:org", "@alice:org"), "conv-a"
        )
        self.assertEqual(
            self.db.get_conversation_id("!room1:org", "@bob:org"), "conv-b"
        )
        self.assertEqual(
            self.db.get_conversation_id("!room2:org", "@alice:org"), "conv-c"
        )

        self.db.set_conversation_id("!room1:org", "@alice:org", "conv-a2")
        self.assertEqual(
            self.db.get_conversation_id("!room1:org", "@alice:org"), "conv-a2"
        )
        self.assertTrue(self.db.clear_conversation_id("!room1:org", "@alice:org"))
        self.assertIsNone(self.db.get_conversation_id("!room1:org", "@alice:org"))

    def test_room_message_context_ordering_and_pruning(self) -> None:
        for i in range(15):
            self.db.add_room_message(
                room_id="!group:org",
                event_id=f"$evt{i}",
                sender_id=f"@u{i % 3}:org",
                sender_name=f"User {i % 3}",
                body=f"message {i}",
                timestamp_ms=1000 + i,
                retain_limit=12,
            )

        # Exclude the latest event ($evt14) and fetch 10 most recent prior messages
        recent = self.db.get_recent_room_messages(
            room_id="!group:org",
            limit=10,
            exclude_event_id="$evt14",
        )
        self.assertEqual(len(recent), 10)
        self.assertEqual([m.body for m in recent], [f"message {i}" for i in range(4, 14)])

    def test_bot_session_persistence(self) -> None:
        self.assertIsNone(self.db.get_bot_session("https://matrix.org", "@bot:matrix.org"))
        self.db.save_bot_session(
            homeserver="https://matrix.org",
            user_id="@bot:matrix.org",
            device_id="DEV1",
            access_token="tok1",
        )
        saved = self.db.get_bot_session("https://matrix.org", "@bot:matrix.org")
        self.assertIsNotNone(saved)
        self.assertEqual(saved.device_id, "DEV1")
        self.assertEqual(saved.access_token, "tok1")

        self.db.save_bot_session(
            homeserver="https://matrix.org",
            user_id="@bot:matrix.org",
            device_id="DEV1",
            access_token="tok2",
        )
        updated = self.db.get_bot_session("https://matrix.org", "@bot:matrix.org")
        self.assertEqual(updated.access_token, "tok2")

        self.db.clear_bot_session("https://matrix.org", "@bot:matrix.org")
        self.assertIsNone(self.db.get_bot_session("https://matrix.org", "@bot:matrix.org"))


if __name__ == "__main__":
    unittest.main()
