"""Unit tests for Dify SSE response parsing and error classification."""

from __future__ import annotations

import unittest

from tests._stubs import ensure_httpx_stub

ensure_httpx_stub()

from app.dify_client import (
    DifyAPIError,
    extract_localpart,
    parse_sse_lines,
    render_dify_inputs,
)


class TestDifyClientParsing(unittest.TestCase):
    def test_parse_streaming_chunks(self) -> None:
        lines = [
            'data: {"event": "agent_message", "conversation_id": "conv-1", "message_id": "msg-1", "answer": "Hello "}',
            "",
            'data: {"event": "message", "conversation_id": "conv-1", "message_id": "msg-1", "answer": "world!"}',
            'data: {"event": "message_end", "conversation_id": "conv-1", "message_id": "msg-1"}',
            "data: [DONE]",
        ]
        res = parse_sse_lines(lines)
        self.assertEqual(res.answer, "Hello world!")
        self.assertEqual(res.conversation_id, "conv-1")
        self.assertEqual(res.message_id, "msg-1")

    def test_parse_sse_error_event(self) -> None:
        lines = [
            'data: {"event": "error", "status": 404, "code": "conversation_not_found", "message": "Conversation Not Exists"}'
        ]
        with self.assertRaises(DifyAPIError) as ctx:
            parse_sse_lines(lines)
        self.assertTrue(ctx.exception.is_conversation_not_found)

    def test_render_dify_inputs_template(self) -> None:
        ctx = {
            "user_name": "Alice",
            "user_id": "@alice:example.org",
            "user_localpart": extract_localpart("@alice:example.org"),
            "room_id": "!room:example.org",
            "source": "matrix",
            "history": "Bob (@bob:example.org): hi",
        }
        template = {
            "user_name": "$user_name",
            "sender": "${user_name} ($user_id)",
            "source": "matrix",
            "history": "$history",
            "static_num": 42,
            "unknown_keep": "$not_a_var",
        }
        rendered = render_dify_inputs(template, ctx)
        self.assertEqual(
            rendered,
            {
                "user_name": "Alice",
                "sender": "Alice (@alice:example.org)",
                "source": "matrix",
                "history": "Bob (@bob:example.org): hi",
                "static_num": 42,
                "unknown_keep": "$not_a_var",
            },
        )
        self.assertEqual(render_dify_inputs({}, ctx), {})


if __name__ == "__main__":
    unittest.main()
