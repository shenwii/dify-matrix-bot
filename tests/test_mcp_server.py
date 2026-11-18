"""Unit tests for MCP SSE server transport security and tool registration."""

from __future__ import annotations

import unittest

from tests._stubs import ensure_apscheduler_stub, ensure_httpx_stub, ensure_mcp_stub

ensure_apscheduler_stub()
ensure_httpx_stub()
ensure_mcp_stub()

from app.mcp_server import create_mcp_server


class TestMcpServer(unittest.TestCase):
    def test_default_disables_dns_rebinding_protection_for_lan_and_docker(self) -> None:
        mcp = create_mcp_server(lambda: None)  # type: ignore[arg-type,return-value]
        self.assertEqual(mcp.host, "0.0.0.0")
        self.assertIsNotNone(mcp.transport_security)
        self.assertFalse(mcp.transport_security.enable_dns_rebinding_protection)
        self.assertIn("create_scheduled_task", mcp.tools)
        self.assertIn("list_scheduled_tasks", mcp.tools)
        self.assertIn("delete_scheduled_task", mcp.tools)

    def test_explicit_allowed_hosts_enables_protection(self) -> None:
        mcp = create_mcp_server(
            lambda: None,  # type: ignore[arg-type,return-value]
            allowed_hosts=("192.168.1.10:8000", "dify-matrix-bot:8000"),
        )
        self.assertIsNotNone(mcp.transport_security)
        self.assertTrue(mcp.transport_security.enable_dns_rebinding_protection)
        self.assertEqual(
            mcp.transport_security.allowed_hosts,
            ["192.168.1.10:8000", "dify-matrix-bot:8000"],
        )


if __name__ == "__main__":
    unittest.main()
