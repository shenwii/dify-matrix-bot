"""Async client for Dify Chat API (/v1/chat-messages) with SSE streaming."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from string import Template
from typing import Any, Iterable

import httpx

logger = logging.getLogger(__name__)


class DifyAPIError(RuntimeError):
    """Raised when Dify returns an HTTP or SSE error."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code

    @property
    def is_conversation_not_found(self) -> bool:
        msg = str(self).lower()
        return (
            self.status_code == 404
            or (self.code or "").lower() == "conversation_not_found"
            or "conversation not exists" in msg
        )


@dataclass(frozen=True)
class DifyChatResult:
    """Result returned by Dify chat-messages endpoint."""

    answer: str
    conversation_id: str | None
    message_id: str | None


@dataclass
class _SseAccumulator:
    """Incremental parser and accumulator for Dify SSE event lines."""

    conversation_id: str | None = None
    message_id: str | None = None
    answer_parts: list[str] = field(default_factory=list)

    def feed_line(self, raw_line: str) -> None:
        line = raw_line.strip()
        if not line or not line.startswith("data:"):
            return
        payload_str = line[5:].strip()
        if not payload_str or payload_str == "[DONE]":
            return
        try:
            payload = json.loads(payload_str)
        except json.JSONDecodeError:
            logger.debug("Ignoring non-JSON SSE payload: %s", payload_str)
            return

        if payload.get("conversation_id"):
            self.conversation_id = str(payload["conversation_id"])
        if payload.get("message_id"):
            self.message_id = str(payload["message_id"])

        event_name = payload.get("event")
        if event_name in {"message", "agent_message"}:
            chunk = payload.get("answer")
            if isinstance(chunk, str):
                self.answer_parts.append(chunk)
        elif event_name == "message_replace":
            replaced = payload.get("answer")
            if isinstance(replaced, str):
                self.answer_parts = [replaced]
        elif event_name == "error":
            raise DifyAPIError(
                message=str(payload.get("message") or "Unknown Dify streaming error"),
                status_code=payload.get("status"),
                code=payload.get("code"),
            )

    def build(self) -> DifyChatResult:
        return DifyChatResult(
            answer="".join(self.answer_parts).strip(),
            conversation_id=self.conversation_id,
            message_id=self.message_id,
        )


def parse_sse_lines(
    lines: Iterable[str],
    initial_conversation_id: str | None = None,
) -> DifyChatResult:
    """Parse raw Dify SSE lines into a consolidated DifyChatResult."""
    acc = _SseAccumulator(conversation_id=initial_conversation_id)
    for raw_line in lines:
        acc.feed_line(raw_line)
    return acc.build()


def extract_localpart(user_id: str) -> str:
    """Extract the localpart from a Matrix MXID like '@bot:example.org' -> 'bot'."""
    if user_id.startswith("@") and ":" in user_id:
        return user_id[1:].split(":", 1)[0]
    return user_id.lstrip("@")


MATRIX_ROOM_ID_RE = re.compile(r"^![^:\s]+:[^\s]+$")
MATRIX_MXID_RE = re.compile(r"^@[^:\s]+:[^\s]+$")

_TARGET_PLACEHOLDERS = {
    "",
    "none",
    "null",
    "nil",
    "undefined",
    "n/a",
    "na",
    "empty",
    "unknown",
    "无",
    "空",
    "$username",
    "$user_id",
    "$room_id",
    "{username}",
    "{user_id}",
    "{room_id}",
}


def normalize_target_field(value: str | None) -> str:
    """Normalize optional username/room_id, treating LLM placeholders as empty."""
    if value is None:
        return ""
    cleaned = str(value).strip()
    if cleaned.lower() in _TARGET_PLACEHOLDERS:
        return ""
    return cleaned


def validate_target_format(room_id: str, username: str) -> tuple[str, str]:
    """Normalize and validate basic Matrix room_id and username formats."""
    clean_room = normalize_target_field(room_id)
    clean_user = normalize_target_field(username)

    if clean_room and not MATRIX_ROOM_ID_RE.match(clean_room):
        raise ValueError(
            f"Invalid room_id {clean_room!r}: expected Matrix room ID like '!room:server' or empty string."
        )

    if clean_user:
        if clean_user.startswith("@") and ":" in clean_user:
            if not MATRIX_MXID_RE.match(clean_user):
                raise ValueError(
                    f"Invalid username MXID {clean_user!r}: expected format '@user:server'."
                )
        elif clean_user.startswith("@") and any(ch.isspace() for ch in clean_user):
            raise ValueError(f"Invalid username {clean_user!r}: contains whitespace.")

    return clean_room, clean_user


_EXACT_PLACEHOLDER_RE = re.compile(r"^\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))$")


def _render_template_value(
    value: Any,
    raw_context: dict[str, Any],
    str_context: dict[str, str],
) -> Any:
    if isinstance(value, str):
        match = _EXACT_PLACEHOLDER_RE.match(value)
        if match:
            key = match.group(1) or match.group(2)
            if key in raw_context:
                resolved = raw_context[key]
                return "" if resolved is None else resolved
        return Template(value).safe_substitute(str_context)
    if isinstance(value, dict):
        return {
            str(k): _render_template_value(v, raw_context, str_context)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_render_template_value(item, raw_context, str_context) for item in value]
    return value


def render_dify_inputs(
    template: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Render a user-configured DIFY_INPUTS template with $var / ${var} placeholders."""
    if not template:
        return {}
    str_context = {k: "" if v is None else str(v) for k, v in context.items()}
    return {
        str(key): _render_template_value(val, context, str_context)
        for key, val in template.items()
    }


class DifyClient:
    """Async Dify Chat API client."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 180.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=15.0)
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def chat(
        self,
        query: str,
        user: str,
        conversation_id: str | None = None,
        inputs: dict[str, Any] | None = None,
    ) -> DifyChatResult:
        """Send a chat query to Dify using SSE streaming and return the full answer."""
        url = f"{self._base_url}/chat-messages"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        body: dict[str, Any] = {
            "inputs": inputs or {},
            "query": query,
            "response_mode": "streaming",
            "user": user,
        }
        if conversation_id:
            body["conversation_id"] = conversation_id

        acc = _SseAccumulator(conversation_id=conversation_id)

        async with self._client.stream("POST", url, headers=headers, json=body) as resp:
            if resp.status_code >= 400:
                raw_error = (await resp.aread()).decode("utf-8", errors="replace")
                error_code: str | None = None
                error_message = raw_error
                try:
                    err_json = json.loads(raw_error)
                    error_code = err_json.get("code")
                    error_message = err_json.get("message") or raw_error
                except json.JSONDecodeError:
                    pass
                raise DifyAPIError(
                    message=f"Dify API error ({resp.status_code}): {error_message}",
                    status_code=resp.status_code,
                    code=error_code,
                )

            async for raw_line in resp.aiter_lines():
                acc.feed_line(raw_line)

        return acc.build()
