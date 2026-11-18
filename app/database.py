"""SQLite persistence for scheduled tasks, per-user Dify conversations, and room history."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class ScheduledTaskRecord:
    """Persisted scheduled task entity."""

    uuid: str
    title: str
    cron: str
    content: str
    is_one_time: bool
    created_at: str
    user_id: str = ""
    room_id: str = ""
    last_run_at: str | None = None
    run_count: int = 0

    def to_dict(self) -> dict:
        return {
            "uuid": self.uuid,
            "title": self.title,
            "cron": self.cron,
            "content": self.content,
            "is_one_time": self.is_one_time,
            "user_id": self.user_id,
            "room_id": self.room_id,
            "created_at": self.created_at,
            "last_run_at": self.last_run_at,
            "run_count": self.run_count,
        }


@dataclass(frozen=True)
class RoomMessageRecord:
    """Persisted Matrix room message for group context injection."""

    room_id: str
    event_id: str
    sender_id: str
    sender_name: str
    body: str
    timestamp_ms: int


@dataclass(frozen=True)
class BotSessionRecord:
    """Persisted Matrix login session so device_id and Olm keys stay consistent across restarts."""

    homeserver: str
    user_id: str
    device_id: str
    access_token: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Thread-safe SQLite repository for tasks, conversations, and room messages."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    uuid TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    cron TEXT NOT NULL,
                    content TEXT NOT NULL,
                    is_one_time INTEGER NOT NULL DEFAULT 0,
                    user_id TEXT NOT NULL DEFAULT '',
                    room_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    last_run_at TEXT,
                    run_count INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            existing_cols = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(scheduled_tasks);").fetchall()
            }
            if "user_id" not in existing_cols:
                if "username" in existing_cols:
                    self._conn.execute(
                        "ALTER TABLE scheduled_tasks RENAME COLUMN username TO user_id;"
                    )
                else:
                    self._conn.execute(
                        "ALTER TABLE scheduled_tasks ADD COLUMN user_id TEXT NOT NULL DEFAULT '';"
                    )
            if "room_id" not in existing_cols:
                self._conn.execute(
                    "ALTER TABLE scheduled_tasks ADD COLUMN room_id TEXT NOT NULL DEFAULT '';"
                )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_conversations (
                    room_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (room_id, user_id)
                );
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS room_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    sender_id TEXT NOT NULL,
                    sender_name TEXT NOT NULL,
                    body TEXT NOT NULL,
                    timestamp_ms INTEGER NOT NULL,
                    UNIQUE (room_id, event_id)
                );
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_room_messages_room_ts
                ON room_messages (room_id, timestamp_ms DESC, id DESC);
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_sessions (
                    homeserver TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    access_token TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (homeserver, user_id)
                );
                """
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # Scheduled Tasks
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> ScheduledTaskRecord:
        return ScheduledTaskRecord(
            uuid=row["uuid"],
            title=row["title"],
            cron=row["cron"],
            content=row["content"],
            is_one_time=bool(row["is_one_time"]),
            user_id=row["user_id"] or "",
            room_id=row["room_id"] or "",
            created_at=row["created_at"],
            last_run_at=row["last_run_at"],
            run_count=int(row["run_count"]),
        )

    def create_task(
        self,
        task_uuid: str,
        title: str,
        cron: str,
        content: str,
        is_one_time: bool,
        user_id: str = "",
        room_id: str = "",
    ) -> ScheduledTaskRecord:
        created_at = _utc_now_iso()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO scheduled_tasks (
                    uuid, title, cron, content, is_one_time, user_id, room_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    task_uuid,
                    title,
                    cron,
                    content,
                    int(is_one_time),
                    user_id,
                    room_id,
                    created_at,
                ),
            )
        return ScheduledTaskRecord(
            uuid=task_uuid,
            title=title,
            cron=cron,
            content=content,
            is_one_time=is_one_time,
            user_id=user_id,
            room_id=room_id,
            created_at=created_at,
            last_run_at=None,
            run_count=0,
        )

    def get_task(self, task_uuid: str) -> ScheduledTaskRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM scheduled_tasks WHERE uuid = ?;",
                (task_uuid,),
            ).fetchone()
        return self._row_to_task(row) if row else None

    def list_tasks(self) -> list[ScheduledTaskRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM scheduled_tasks ORDER BY created_at ASC;"
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def delete_task(self, task_uuid: str) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM scheduled_tasks WHERE uuid = ?;",
                (task_uuid,),
            )
            return cursor.rowcount > 0

    def mark_task_executed(self, task_uuid: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE scheduled_tasks
                SET last_run_at = ?, run_count = run_count + 1
                WHERE uuid = ?;
                """,
                (_utc_now_iso(), task_uuid),
            )

    # ------------------------------------------------------------------
    # Per-User Conversations (room_id + user_id -> conversation_id)
    # ------------------------------------------------------------------

    def get_conversation_id(self, room_id: str, user_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT conversation_id
                FROM user_conversations
                WHERE room_id = ? AND user_id = ?;
                """,
                (room_id, user_id),
            ).fetchone()
        return str(row["conversation_id"]) if row else None

    def set_conversation_id(
        self, room_id: str, user_id: str, conversation_id: str
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO user_conversations (room_id, user_id, conversation_id, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (room_id, user_id)
                DO UPDATE SET
                    conversation_id = excluded.conversation_id,
                    updated_at = excluded.updated_at;
                """,
                (room_id, user_id, conversation_id, _utc_now_iso()),
            )

    def clear_conversation_id(self, room_id: str, user_id: str) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM user_conversations WHERE room_id = ? AND user_id = ?;",
                (room_id, user_id),
            )
            return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Group Room Message Context
    # ------------------------------------------------------------------

    def add_room_message(
        self,
        room_id: str,
        event_id: str,
        sender_id: str,
        sender_name: str,
        body: str,
        timestamp_ms: int,
        retain_limit: int = 100,
    ) -> None:
        """Store a message for a room and prune older records beyond retain_limit."""
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO room_messages (
                    room_id, event_id, sender_id, sender_name, body, timestamp_ms
                ) VALUES (?, ?, ?, ?, ?, ?);
                """,
                (room_id, event_id, sender_id, sender_name, body, timestamp_ms),
            )
            if retain_limit > 0:
                self._conn.execute(
                    """
                    DELETE FROM room_messages
                    WHERE room_id = ?
                      AND id NOT IN (
                          SELECT id FROM room_messages
                          WHERE room_id = ?
                          ORDER BY timestamp_ms DESC, id DESC
                          LIMIT ?
                      );
                    """,
                    (room_id, room_id, retain_limit),
                )

    def get_recent_room_messages(
        self,
        room_id: str,
        limit: int,
        exclude_event_id: str | None = None,
    ) -> list[RoomMessageRecord]:
        """Fetch up to `limit` recent messages in chronological order (oldest first)."""
        if limit <= 0:
            return []
        with self._lock:
            if exclude_event_id:
                rows = self._conn.execute(
                    """
                    SELECT room_id, event_id, sender_id, sender_name, body, timestamp_ms
                    FROM room_messages
                    WHERE room_id = ? AND event_id != ?
                    ORDER BY timestamp_ms DESC, id DESC
                    LIMIT ?;
                    """,
                    (room_id, exclude_event_id, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT room_id, event_id, sender_id, sender_name, body, timestamp_ms
                    FROM room_messages
                    WHERE room_id = ?
                    ORDER BY timestamp_ms DESC, id DESC
                    LIMIT ?;
                    """,
                    (room_id, limit),
                ).fetchall()

        records = [
            RoomMessageRecord(
                room_id=row["room_id"],
                event_id=row["event_id"],
                sender_id=row["sender_id"],
                sender_name=row["sender_name"],
                body=row["body"],
                timestamp_ms=int(row["timestamp_ms"]),
            )
            for row in rows
        ]
        records.reverse()
        return records

    # ------------------------------------------------------------------
    # Persisted Bot Login Session (for E2EE device_id continuity)
    # ------------------------------------------------------------------

    def get_bot_session(self, homeserver: str, user_id: str) -> BotSessionRecord | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT homeserver, user_id, device_id, access_token
                FROM bot_sessions
                WHERE homeserver = ? AND user_id = ?;
                """,
                (homeserver, user_id),
            ).fetchone()
        if not row:
            return None
        return BotSessionRecord(
            homeserver=row["homeserver"],
            user_id=row["user_id"],
            device_id=row["device_id"],
            access_token=row["access_token"],
        )

    def save_bot_session(
        self,
        homeserver: str,
        user_id: str,
        device_id: str,
        access_token: str,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO bot_sessions (
                    homeserver, user_id, device_id, access_token, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (homeserver, user_id)
                DO UPDATE SET
                    device_id = excluded.device_id,
                    access_token = excluded.access_token,
                    updated_at = excluded.updated_at;
                """,
                (homeserver, user_id, device_id, access_token, _utc_now_iso()),
            )

    def clear_bot_session(self, homeserver: str, user_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM bot_sessions WHERE homeserver = ? AND user_id = ?;",
                (homeserver, user_id),
            )
