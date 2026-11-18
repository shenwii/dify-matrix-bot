"""Matrix bot built on matrix-nio with group support, typing heartbeat, and Dify integration."""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Iterable

import markdown as md_lib
from nio import (
    AsyncClient,
    AsyncClientConfig,
    InviteMemberEvent,
    LoginResponse,
    MatrixRoom,
    MegolmEvent,
    RoomMessageText,
    RoomPreset,
    RoomSendResponse,
    RoomVisibility,
)
from nio.exceptions import OlmUnverifiedDeviceError

from app.config import Settings
from app.database import Database, RoomMessageRecord
from app.dify_client import (
    MATRIX_MXID_RE,
    MATRIX_ROOM_ID_RE,
    DifyAPIError,
    DifyClient,
    extract_localpart,
    normalize_target_field,
    render_dify_inputs,
    validate_target_format,
)

logger = logging.getLogger(__name__)


def is_bot_mentioned(
    body: str,
    formatted_body: str | None,
    source: dict,
    bot_user_id: str,
    bot_display_name: str | None = None,
    command_prefix: str = "",
) -> bool:
    """Return True if a group message explicitly mentions the bot or uses the command prefix."""
    content = source.get("content") or {}
    mentions = content.get("m.mentions") or {}
    mentioned_ids = mentions.get("user_ids") or []
    if bot_user_id in mentioned_ids:
        return True

    if formatted_body and f"https://matrix.to/#/{bot_user_id}" in formatted_body:
        return True

    stripped_body = body.strip()
    if command_prefix and stripped_body.startswith(command_prefix):
        return True

    if bot_user_id and bot_user_id in body:
        return True

    localpart = extract_localpart(bot_user_id)
    if localpart:
        if re.search(rf"(?<!\S)@{re.escape(localpart)}(?=$|[\s:,，：])", body):
            return True

    if bot_display_name:
        clean_name = bot_display_name.strip()
        if clean_name and re.search(
            rf"(?<!\S)@{re.escape(clean_name)}(?=$|[\s:,，：])", body
        ):
            return True

    return False


def strip_bot_mention(
    body: str,
    bot_user_id: str,
    bot_display_name: str | None = None,
    command_prefix: str = "",
) -> str:
    """Remove bot mention tokens or command prefix from the message body."""
    text = body.strip()
    if command_prefix and text.startswith(command_prefix):
        text = text[len(command_prefix) :].strip()

    localpart = extract_localpart(bot_user_id)
    candidates = [bot_user_id]
    if localpart:
        candidates.append(f"@{localpart}")
    if bot_display_name and bot_display_name.strip():
        name = bot_display_name.strip()
        candidates.append(f"@{name}")
        candidates.append(name)

    for token in candidates:
        if not token:
            continue
        # Strip leading mention like "@bot: hello" or "bot, hello"
        pattern_leading = rf"^{re.escape(token)}[\s:,，：]*"
        new_text = re.sub(pattern_leading, "", text, count=1).strip()
        if new_text != text:
            text = new_text
            continue
        # Strip inline MXID / @mention occurrences
        text = re.sub(rf"\s*{re.escape(token)}\s*", " ", text).strip()

    return text


def format_room_history(recent_messages: list[RoomMessageRecord]) -> str:
    """Format recent room messages into a chronological transcript for $history."""
    if not recent_messages:
        return ""
    return "\n".join(
        f"{msg.sender_name} ({msg.sender_id}): {msg.body}"
        for msg in recent_messages
    )


def render_markdown_html(text: str) -> str:
    """Convert Markdown text into HTML suitable for Matrix formatted_body."""
    return md_lib.markdown(
        text,
        extensions=["fenced_code", "tables", "nl2br"],
    )


def build_matrix_message_content(
    text: str,
    mention_user_id: str | None = None,
    mention_label: str | None = None,
    mention_fallback_name: str | None = None,
) -> dict:
    """Build Matrix m.room.message content payload with optional @mention."""
    if mention_user_id:
        label = mention_label or extract_localpart(mention_user_id)
        body = f"@{label}\n\n{text}"
        html_body = (
            f'<a href="https://matrix.to/#/{html.escape(mention_user_id)}">'
            f"{html.escape(label)}</a><br/><br/>{render_markdown_html(text)}"
        )
        return {
            "msgtype": "m.text",
            "body": body,
            "format": "org.matrix.custom.html",
            "formatted_body": html_body,
            "m.mentions": {"user_ids": [mention_user_id]},
        }
    if mention_fallback_name:
        body = f"@{mention_fallback_name}\n\n{text}"
        return {
            "msgtype": "m.text",
            "body": body,
            "format": "org.matrix.custom.html",
            "formatted_body": render_markdown_html(body),
        }
    return {
        "msgtype": "m.text",
        "body": text,
        "format": "org.matrix.custom.html",
        "formatted_body": render_markdown_html(text),
    }


class MatrixBot:
    """Async Matrix client wrapping group/DM chat, typing heartbeat, and message sending."""

    def __init__(
        self,
        settings: Settings,
        db: Database,
        dify_client: DifyClient,
    ) -> None:
        self._settings = settings
        self._db = db
        self._dify = dify_client

        store_path: str | None = None
        if settings.matrix_e2ee_enabled:
            Path(settings.matrix_store_path).mkdir(parents=True, exist_ok=True)
            store_path = settings.matrix_store_path

        client_config = AsyncClientConfig(
            encryption_enabled=settings.matrix_e2ee_enabled,
            store_sync_tokens=settings.matrix_e2ee_enabled,
        )
        self._client = AsyncClient(
            homeserver=settings.matrix_homeserver,
            user=settings.matrix_user_id,
            device_id=settings.matrix_device_id,
            store_path=store_path,
            config=client_config,
        )
        self._start_ts_ms: int = 0
        self._sync_task: asyncio.Task[None] | None = None
        self._inflight_tasks: set[asyncio.Task[None]] = set()
        self._user_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._connected: bool = False
        self._stop_event = asyncio.Event()

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _get_user_lock(self, room_id: str, user_id: str) -> asyncio.Lock:
        key = (room_id, user_id)
        lock = self._user_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._user_locks[key] = lock
        return lock

    async def start(self) -> None:
        """Authenticate, perform initial sync, register callbacks, and start sync loop."""
        await self._authenticate()
        self._start_ts_ms = int(time.time() * 1000)

        # Initial sync populates joined rooms/members without triggering message handlers.
        sync_resp = await self._client.sync(timeout=3000, full_state=True)
        logger.info(
            "Initial Matrix sync complete (next_batch=%s, rooms=%d, e2ee=%s)",
            getattr(sync_resp, "next_batch", None),
            len(self._client.rooms),
            self._settings.matrix_e2ee_enabled,
        )

        if self._settings.matrix_e2ee_enabled:
            await self._sync_e2ee_keys()
            self._client.add_event_callback(self._on_megolm_event, MegolmEvent)

        self._client.add_event_callback(self._on_invite, InviteMemberEvent)
        self._client.add_event_callback(self._on_message, RoomMessageText)

        self._connected = True
        self._stop_event.clear()
        self._sync_task = asyncio.create_task(self._sync_loop(), name="matrix-sync")

    async def stop(self) -> None:
        """Stop the sync loop, wait for active tasks, and close the Matrix HTTP session."""
        self._connected = False
        self._stop_event.set()

        if self._sync_task:
            self._sync_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sync_task
            self._sync_task = None

        for task in list(self._inflight_tasks):
            task.cancel()
        if self._inflight_tasks:
            await asyncio.gather(*self._inflight_tasks, return_exceptions=True)

        await self._client.close()
        logger.info("MatrixBot stopped")

    def _load_crypto_store_if_enabled(self) -> None:
        if not self._settings.matrix_e2ee_enabled:
            return
        load_fn = getattr(self._client, "load_store", None)
        if callable(load_fn):
            load_fn()

    def _trust_devices(self, user_ids: Iterable[str] | None = None) -> None:
        if (
            not self._settings.matrix_e2ee_enabled
            or not self._settings.matrix_trust_all_devices
        ):
            return
        device_store = getattr(self._client, "device_store", None)
        if device_store is None:
            return
        targets = (
            list(user_ids)
            if user_ids is not None
            else list(getattr(device_store, "users", []))
        )
        for uid in targets:
            for device in device_store.active_user_devices(uid):
                if not getattr(device, "verified", False):
                    self._client.verify_device(device)

    async def _sync_e2ee_keys(self) -> None:
        if not self._settings.matrix_e2ee_enabled:
            return
        if getattr(self._client, "should_upload_keys", False):
            await self._client.keys_upload()
        if getattr(self._client, "should_query_keys", False):
            await self._client.keys_query()
        self._trust_devices()

    async def _authenticate(self) -> None:
        if self._settings.matrix_access_token:
            self._client.access_token = self._settings.matrix_access_token
            self._client.user_id = self._settings.matrix_user_id
            self._client.device_id = self._settings.matrix_device_id
            self._client.logged_in = True
            whoami = await self._client.whoami()
            if getattr(whoami, "user_id", None):
                self._client.user_id = whoami.user_id
                if getattr(whoami, "device_id", None):
                    self._client.device_id = whoami.device_id
                self._load_crypto_store_if_enabled()
                logger.info(
                    "Authenticated Matrix bot via access token as %s (device_id=%s)",
                    self._client.user_id,
                    self._client.device_id,
                )
                return
            self._load_crypto_store_if_enabled()
            logger.warning("Matrix whoami check did not return user_id: %s", whoami)
            return

        saved = self._db.get_bot_session(
            self._settings.matrix_homeserver, self._settings.matrix_user_id
        )
        if saved is not None:
            self._client.access_token = saved.access_token
            self._client.user_id = saved.user_id
            self._client.device_id = saved.device_id
            self._client.logged_in = True
            whoami = await self._client.whoami()
            if getattr(whoami, "user_id", None):
                self._load_crypto_store_if_enabled()
                logger.info(
                    "Restored Matrix session for %s (device_id=%s)",
                    saved.user_id,
                    saved.device_id,
                )
                return
            logger.warning("Saved Matrix session expired; logging in again")
            self._db.clear_bot_session(
                self._settings.matrix_homeserver, self._settings.matrix_user_id
            )
            self._client.access_token = ""
            self._client.logged_in = False

        resp = await self._client.login(
            password=self._settings.matrix_password,
            device_name=self._settings.matrix_device_id,
        )
        if not isinstance(resp, LoginResponse):
            raise RuntimeError(f"Matrix login failed: {resp}")
        self._db.save_bot_session(
            homeserver=self._settings.matrix_homeserver,
            user_id=resp.user_id,
            device_id=resp.device_id,
            access_token=resp.access_token,
        )
        logger.info(
            "Logged in to Matrix as %s (device_id=%s)", resp.user_id, resp.device_id
        )

    async def _sync_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._client.sync_forever(
                    timeout=30000,
                    full_state=False,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                self._connected = False
                logger.exception("Matrix sync loop error; retrying in 5s")
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    self._connected = True

    async def _on_megolm_event(self, room: MatrixRoom, event: MegolmEvent) -> None:
        if event.server_timestamp and event.server_timestamp < self._start_ts_ms:
            return
        logger.warning(
            "Unable to decrypt MegolmEvent %s in %s from %s (session_id=%s); requesting key",
            event.event_id,
            room.room_id,
            event.sender,
            getattr(event, "session_id", None),
        )
        try:
            await self._client.room_key_request(event)
        except Exception:
            logger.debug("room_key_request failed for %s", event.event_id, exc_info=True)

    async def _on_invite(self, room: MatrixRoom, event: InviteMemberEvent) -> None:
        if not self._settings.matrix_auto_join:
            return
        if event.state_key != self._client.user_id or event.membership != "invite":
            return
        logger.info("Invited to room %s by %s; joining...", room.room_id, event.sender)
        resp = await self._client.join(room.room_id)
        logger.info("Join response for %s: %s", room.room_id, resp)

    async def _on_message(self, room: MatrixRoom, event: RoomMessageText) -> None:
        if event.sender == self._client.user_id:
            return
        if event.server_timestamp and event.server_timestamp < self._start_ts_ms:
            return

        body = (event.body or "").strip()
        if not body:
            return

        is_group = room.member_count > 2
        sender_name = room.user_name(event.sender) or event.sender

        if is_group:
            retain_limit = max(self._settings.matrix_group_context_limit * 5, 100)
            self._db.add_room_message(
                room_id=room.room_id,
                event_id=event.event_id,
                sender_id=event.sender,
                sender_name=sender_name,
                body=body,
                timestamp_ms=int(event.server_timestamp or time.time() * 1000),
                retain_limit=retain_limit,
            )

            bot_display_name = room.user_name(self._client.user_id)
            if not is_bot_mentioned(
                body=body,
                formatted_body=getattr(event, "formatted_body", None),
                source=event.source,
                bot_user_id=self._client.user_id,
                bot_display_name=bot_display_name,
                command_prefix=self._settings.matrix_command_prefix,
            ):
                return

            user_query = strip_bot_mention(
                body=body,
                bot_user_id=self._client.user_id,
                bot_display_name=bot_display_name,
                command_prefix=self._settings.matrix_command_prefix,
            )
            if not user_query:
                user_query = "你好"
        else:
            user_query = body

        room_name = getattr(room, "display_name", None) or getattr(room, "name", None) or room.room_id
        task = asyncio.create_task(
            self._handle_chat_message(
                room_id=room.room_id,
                room_name=room_name,
                event_id=event.event_id,
                sender_id=event.sender,
                sender_name=sender_name,
                user_query=user_query,
                is_group=is_group,
                bot_display_name=room.user_name(self._client.user_id) or "AI助手",
            )
        )
        self._inflight_tasks.add(task)
        task.add_done_callback(self._inflight_tasks.discard)

    @asynccontextmanager
    async def _typing_heartbeat(self, room_id: str) -> AsyncIterator[None]:
        """Keep the Matrix 'typing' indicator active until AI response finishes."""
        stop_typing = asyncio.Event()

        async def _beat() -> None:
            while not stop_typing.is_set():
                try:
                    await self._client.room_typing(
                        room_id=room_id,
                        typing_state=True,
                        timeout=self._settings.matrix_typing_timeout_ms,
                    )
                except Exception:
                    logger.debug("Failed to send typing indicator for %s", room_id, exc_info=True)
                try:
                    await asyncio.wait_for(
                        stop_typing.wait(),
                        timeout=self._settings.matrix_typing_interval_seconds,
                    )
                except asyncio.TimeoutError:
                    continue

        beat_task = asyncio.create_task(_beat())
        try:
            yield
        finally:
            stop_typing.set()
            beat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beat_task
            try:
                await self._client.room_typing(room_id=room_id, typing_state=False)
            except Exception:
                logger.debug("Failed to clear typing indicator for %s", room_id, exc_info=True)

    async def _handle_chat_message(
        self,
        room_id: str,
        room_name: str,
        event_id: str,
        sender_id: str,
        sender_name: str,
        user_query: str,
        is_group: bool,
        bot_display_name: str,
    ) -> None:
        lock = self._get_user_lock(room_id, sender_id)
        async with lock:
            if user_query.strip().lower() in {"/reset", "/clear"}:
                self._db.clear_conversation_id(room_id, sender_id)
                await self.send_markdown(room_id, "已重置你当前会话的上下文。")
                return

            history_text = ""
            if is_group and self._settings.matrix_group_context_limit > 0:
                recent = self._db.get_recent_room_messages(
                    room_id=room_id,
                    limit=self._settings.matrix_group_context_limit,
                    exclude_event_id=event_id,
                )
                history_text = format_room_history(recent)

            dify_query = user_query
            dify_user = f"{room_id}:{sender_id}"
            conversation_id = self._db.get_conversation_id(room_id, sender_id)
            dify_inputs = render_dify_inputs(
                template=self._settings.dify_inputs_template,
                context={
                    "username": sender_name,
                    "user_name": sender_name,
                    "user_id": sender_id,
                    "user_localpart": extract_localpart(sender_id),
                    "room_id": room_id,
                    "room_name": room_name,
                    "is_group": "true" if is_group else "false",
                    "source": "matrix",
                    "history": history_text,
                    "task_uuid": "",
                    "task_title": "",
                },
            )

            try:
                async with self._typing_heartbeat(room_id):
                    try:
                        result = await self._dify.chat(
                            query=dify_query,
                            user=dify_user,
                            conversation_id=conversation_id,
                            inputs=dify_inputs,
                        )
                    except DifyAPIError as exc:
                        if conversation_id and exc.is_conversation_not_found:
                            logger.warning(
                                "Stale conversation_id %s for (%s, %s); retrying fresh",
                                conversation_id,
                                room_id,
                                sender_id,
                            )
                            self._db.clear_conversation_id(room_id, sender_id)
                            result = await self._dify.chat(
                                query=dify_query,
                                user=dify_user,
                                conversation_id=None,
                                inputs=dify_inputs,
                            )
                        else:
                            raise

                if result.conversation_id:
                    self._db.set_conversation_id(
                        room_id, sender_id, result.conversation_id
                    )

                reply_text = result.answer or "（AI 未返回内容）"
                sent_event_id = await self.send_markdown(room_id, reply_text)

                if is_group and sent_event_id:
                    retain_limit = max(self._settings.matrix_group_context_limit * 5, 100)
                    self._db.add_room_message(
                        room_id=room_id,
                        event_id=sent_event_id,
                        sender_id=self._client.user_id,
                        sender_name=bot_display_name,
                        body=reply_text,
                        timestamp_ms=int(time.time() * 1000),
                        retain_limit=retain_limit,
                    )
            except Exception as exc:
                logger.exception(
                    "Error handling Matrix message from %s in %s", sender_id, room_id
                )
                await self.send_markdown(
                    room_id,
                    f"处理请求时发生错误：`{exc}`",
                )

    async def send_markdown(
        self,
        room_id: str,
        text: str,
        mention_user_id: str | None = None,
        mention_label: str | None = None,
        mention_fallback_name: str | None = None,
    ) -> str | None:
        """Send a Markdown-formatted message to a Matrix room and return its event_id."""
        content = build_matrix_message_content(
            text=text,
            mention_user_id=mention_user_id,
            mention_label=mention_label,
            mention_fallback_name=mention_fallback_name,
        )
        if self._settings.matrix_e2ee_enabled:
            if getattr(self._client, "should_query_keys", False):
                await self._client.keys_query()
            room = self._client.rooms.get(room_id)
            member_ids = (
                list((getattr(room, "users", None) or {}).keys()) if room else None
            )
            self._trust_devices(member_ids)

        ignore_unverified = self._settings.matrix_trust_all_devices
        try:
            resp = await self._client.room_send(
                room_id=room_id,
                message_type="m.room.message",
                content=content,
                ignore_unverified_devices=ignore_unverified,
            )
        except OlmUnverifiedDeviceError as exc:
            if not self._settings.matrix_trust_all_devices:
                logger.error(
                    "Blocked sending encrypted message to %s due to unverified device: %s",
                    room_id,
                    exc,
                )
                return None
            if getattr(exc, "device", None) is not None:
                self._client.verify_device(exc.device)
            self._trust_devices()
            resp = await self._client.room_send(
                room_id=room_id,
                message_type="m.room.message",
                content=content,
                ignore_unverified_devices=True,
            )
        if isinstance(resp, RoomSendResponse):
            return resp.event_id
        logger.error("Failed to send Matrix message to %s: %s", room_id, resp)
        return None

    def _resolve_user_in_room(
        self, room: MatrixRoom | None, username: str
    ) -> tuple[str | None, str | None]:
        clean = username.strip()
        if not clean:
            return None, None
        if clean.startswith("@") and ":" in clean:
            label = (room.user_name(clean) if room else None) or extract_localpart(clean)
            return clean, label
        if room is not None:
            users = getattr(room, "users", None) or {}
            target_local = clean.lstrip("@")
            for mxid, member in users.items():
                display = getattr(member, "display_name", None)
                if display == clean or extract_localpart(mxid) == target_local:
                    return mxid, display or extract_localpart(mxid)
        return None, None

    def _find_user_across_rooms(self, username: str) -> str | None:
        clean = username.strip()
        if not clean:
            return None
        for room in self._client.rooms.values():
            users = getattr(room, "users", None) or {}
            if MATRIX_MXID_RE.match(clean):
                if clean in users:
                    return clean
                continue
            mxid, _ = self._resolve_user_in_room(room, clean)
            if mxid:
                return mxid
        return None

    def validate_task_target(
        self, room_id: str, username: str
    ) -> tuple[str, str]:
        """Validate and canonicalize task target (room_id, username) against known Matrix state."""
        clean_room, clean_user = validate_target_format(room_id, username)
        if not self._connected:
            return clean_room, clean_user

        if clean_room:
            if clean_room not in self._client.rooms:
                raise ValueError(
                    f"Bot is not in room_id {clean_room!r}. Pass a joined room_id or leave empty."
                )
            if clean_user:
                room = self._client.rooms[clean_room]
                users = getattr(room, "users", None) or {}
                if MATRIX_MXID_RE.match(clean_user):
                    if users and clean_user not in users:
                        raise ValueError(
                            f"User {clean_user!r} is not a member of room {clean_room!r}."
                        )
                else:
                    mxid, _ = self._resolve_user_in_room(room, clean_user)
                    if not mxid:
                        raise ValueError(
                            f"User {clean_user!r} was not found in room {clean_room!r}."
                        )
                    clean_user = mxid
        elif clean_user:
            resolved = self._find_user_across_rooms(clean_user)
            if resolved:
                clean_user = resolved
            elif not MATRIX_MXID_RE.match(clean_user) or self._client.rooms:
                raise ValueError(
                    f"Unknown username {clean_user!r}: user not found in any room the bot has joined."
                )

        return clean_room, clean_user

    def _resolve_dm_target_mxid(self, username: str) -> str | None:
        clean = normalize_target_field(username)
        if not clean:
            return None
        if MATRIX_MXID_RE.match(clean):
            return clean
        found = self._find_user_across_rooms(clean)
        if found:
            return found
        if self._client.user_id and ":" in self._client.user_id:
            domain = self._client.user_id.split(":", 1)[1]
            candidate = f"@{clean.lstrip('@')}:{domain}"
            if MATRIX_MXID_RE.match(candidate):
                return candidate
        return None

    async def _get_or_create_dm_room(self, target_mxid: str) -> str | None:
        for room in self._client.rooms.values():
            if room.member_count == 2 and target_mxid in (getattr(room, "users", None) or {}):
                return room.room_id

        create_kwargs: dict = {
            "visibility": RoomVisibility.private,
            "preset": RoomPreset.private_chat,
            "is_direct": True,
            "invite": [target_mxid],
        }
        if self._settings.matrix_e2ee_enabled:
            create_kwargs["initial_state"] = [
                {
                    "type": "m.room.encryption",
                    "state_key": "",
                    "content": {"algorithm": "m.megolm.v1.aes-sha2"},
                }
            ]

        try:
            resp = await self._client.room_create(**create_kwargs)
        except Exception:
            logger.exception("Error creating DM room with %s", target_mxid)
            return None
        room_id = getattr(resp, "room_id", None)
        if room_id:
            return room_id
        logger.error("Failed to create DM room with %s: %s", target_mxid, resp)
        return None

    async def deliver_task_result(
        self, room_id: str, username: str, text: str
    ) -> None:
        """Route scheduled task output to a group (with optional @mention) or DM."""
        clean_room = normalize_target_field(room_id)
        clean_user = normalize_target_field(username)

        if not clean_room and not clean_user:
            return

        if clean_room:
            if not MATRIX_ROOM_ID_RE.match(clean_room):
                logger.warning("Invalid room_id %r; skipping task delivery", clean_room)
                return
            if self._connected and clean_room not in self._client.rooms:
                logger.warning(
                    "Bot is no longer in room %r; skipping task delivery", clean_room
                )
                return
            if clean_user:
                room = self._client.rooms.get(clean_room)
                mxid, label = self._resolve_user_in_room(room, clean_user)
                await self.send_markdown(
                    clean_room,
                    text,
                    mention_user_id=mxid,
                    mention_label=label,
                    mention_fallback_name=None if mxid else clean_user,
                )
            else:
                await self.send_markdown(clean_room, text)
            return

        target_mxid = self._resolve_dm_target_mxid(clean_user)
        if not target_mxid or not MATRIX_MXID_RE.match(target_mxid):
            logger.warning(
                "Could not resolve valid Matrix user ID for DM delivery (username=%r)",
                clean_user,
            )
            return
        dm_room_id = await self._get_or_create_dm_room(target_mxid)
        if dm_room_id:
            await self.send_markdown(dm_room_id, text)
