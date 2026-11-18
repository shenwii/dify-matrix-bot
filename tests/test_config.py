"""Unit tests for Settings loading and validation."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.config import Settings


class TestSettings(unittest.TestCase):
    def test_defaults_and_parsing(self) -> None:
        env = {
            "MATRIX_HOMESERVER": "https://matrix.example.org/",
            "MATRIX_USER_ID": "@bot:example.org",
            "MATRIX_ACCESS_TOKEN": "tok_123",
            "MATRIX_GROUP_CONTEXT_LIMIT": "15",
            "MATRIX_AUTO_JOIN": "false",
            "DIFY_API_KEY": "app-key",
            "DIFY_BASE_URL": "https://dify.example.org/v1/",
            "TZ": "Asia/Tokyo",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()
            settings.validate_runtime()

        self.assertEqual(settings.matrix_homeserver, "https://matrix.example.org")
        self.assertEqual(settings.dify_base_url, "https://dify.example.org/v1")
        self.assertEqual(settings.matrix_group_context_limit, 15)
        self.assertFalse(settings.matrix_auto_join)
        self.assertEqual(settings.timezone, "Asia/Tokyo")
        self.assertEqual(settings.dify_inputs_template, {})
        self.assertEqual(settings.dify_scheduler_inputs_template, {})
        self.assertTrue(settings.matrix_e2ee_enabled)
        self.assertEqual(settings.matrix_store_path, "./data/crypto_store")
        self.assertTrue(settings.matrix_trust_all_devices)

    def test_e2ee_settings_override(self) -> None:
        env = {
            "MATRIX_E2EE_ENABLED": "false",
            "MATRIX_STORE_PATH": "/tmp/custom_store",
            "MATRIX_TRUST_ALL_DEVICES": "false",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()
        self.assertFalse(settings.matrix_e2ee_enabled)
        self.assertEqual(settings.matrix_store_path, "/tmp/custom_store")
        self.assertFalse(settings.matrix_trust_all_devices)

    def test_custom_dify_inputs_json(self) -> None:
        env = {
            "DIFY_INPUTS": '{"username": "$username", "source": "matrix", "history": "$history"}',
            "DIFY_SCHEDULER_INPUTS": '{"username": "$username", "room_id": "$room_id"}',
        }
        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()

        self.assertEqual(
            settings.dify_inputs_template,
            {"username": "$username", "source": "matrix", "history": "$history"},
        )
        self.assertEqual(
            settings.dify_scheduler_inputs_template,
            {"username": "$username", "room_id": "$room_id"},
        )

        with patch.dict(os.environ, {"DIFY_INPUTS": "not-json"}, clear=True):
            with self.assertRaises(ValueError):
                Settings.from_env()

    def test_validate_runtime_missing_required(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()
            with self.assertRaises(ValueError) as ctx:
                settings.validate_runtime()
        self.assertIn("MATRIX_HOMESERVER", str(ctx.exception))
        self.assertIn("DIFY_API_KEY", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
