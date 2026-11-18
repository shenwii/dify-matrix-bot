"""Application configuration loaded from environment variables."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(value: str | None, default: int, minimum: int | None = None) -> int:
    if value is None or value.strip() == "":
        parsed = default
    else:
        parsed = int(value.strip())
    if minimum is not None and parsed < minimum:
        raise ValueError(f"Expected integer >= {minimum}, got {parsed}")
    return parsed


def _parse_float(value: str | None, default: float, minimum: float | None = None) -> float:
    if value is None or value.strip() == "":
        parsed = default
    else:
        parsed = float(value.strip())
    if minimum is not None and parsed < minimum:
        raise ValueError(f"Expected float >= {minimum}, got {parsed}")
    return parsed


def _parse_json_dict(value: str | None, env_name: str) -> dict[str, Any]:
    if value is None or value.strip() == "":
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{env_name} must be a valid JSON object: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{env_name} must be a JSON object, got {type(parsed).__name__}")
    return parsed


@dataclass(frozen=True)
class Settings:
    """Runtime settings for Matrix bot, Dify client, MCP server, and scheduler."""

    # Matrix settings
    matrix_homeserver: str
    matrix_user_id: str
    matrix_access_token: str
    matrix_password: str
    matrix_device_id: str
    matrix_auto_join: bool
    matrix_group_context_limit: int
    matrix_command_prefix: str
    matrix_typing_interval_seconds: int
    matrix_typing_timeout_ms: int

    # Dify settings
    dify_base_url: str
    dify_api_key: str
    dify_timeout_seconds: float
    dify_scheduled_user: str

    # Scheduler & storage
    timezone: str
    database_path: str

    # Server & MCP security
    host: str
    port: int
    log_level: str
    mcp_auth_token: str

    # Optional Dify inputs templates (JSON objects with $variable placeholders)
    dify_inputs_template: dict[str, Any] = field(default_factory=dict)
    dify_scheduler_inputs_template: dict[str, Any] = field(default_factory=dict)

    # Matrix E2EE settings
    matrix_e2ee_enabled: bool = True
    matrix_store_path: str = "./data/crypto_store"
    matrix_trust_all_devices: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings from process environment variables."""
        return cls(
            matrix_homeserver=os.getenv("MATRIX_HOMESERVER", "").strip().rstrip("/"),
            matrix_user_id=os.getenv("MATRIX_USER_ID", "").strip(),
            matrix_access_token=os.getenv("MATRIX_ACCESS_TOKEN", "").strip(),
            matrix_password=os.getenv("MATRIX_PASSWORD", "").strip(),
            matrix_device_id=os.getenv("MATRIX_DEVICE_ID", "DIFY_MATRIX_BOT").strip()
            or "DIFY_MATRIX_BOT",
            matrix_auto_join=_parse_bool(os.getenv("MATRIX_AUTO_JOIN"), True),
            matrix_group_context_limit=_parse_int(
                os.getenv("MATRIX_GROUP_CONTEXT_LIMIT"), 10, minimum=0
            ),
            matrix_command_prefix=os.getenv("MATRIX_COMMAND_PREFIX", "").strip(),
            matrix_typing_interval_seconds=_parse_int(
                os.getenv("MATRIX_TYPING_INTERVAL_SECONDS"), 15, minimum=1
            ),
            matrix_typing_timeout_ms=_parse_int(
                os.getenv("MATRIX_TYPING_TIMEOUT_MS"), 30000, minimum=1000
            ),
            matrix_e2ee_enabled=_parse_bool(os.getenv("MATRIX_E2EE_ENABLED"), True),
            matrix_store_path=(
                os.getenv("MATRIX_STORE_PATH", "./data/crypto_store").strip()
                or "./data/crypto_store"
            ),
            matrix_trust_all_devices=_parse_bool(
                os.getenv("MATRIX_TRUST_ALL_DEVICES"), True
            ),
            dify_base_url=(
                os.getenv("DIFY_BASE_URL", "https://api.dify.ai/v1").strip().rstrip("/")
            ),
            dify_api_key=os.getenv("DIFY_API_KEY", "").strip(),
            dify_timeout_seconds=_parse_float(
                os.getenv("DIFY_TIMEOUT_SECONDS"), 180.0, minimum=1.0
            ),
            dify_scheduled_user=(
                os.getenv("DIFY_SCHEDULED_USER", "dify-scheduler").strip()
                or "dify-scheduler"
            ),
            dify_inputs_template=_parse_json_dict(
                os.getenv("DIFY_INPUTS"), "DIFY_INPUTS"
            ),
            dify_scheduler_inputs_template=_parse_json_dict(
                os.getenv("DIFY_SCHEDULER_INPUTS"), "DIFY_SCHEDULER_INPUTS"
            ),
            timezone=os.getenv("TZ", "Asia/Shanghai").strip() or "Asia/Shanghai",
            database_path=os.getenv("DATABASE_PATH", "./data/bot.db").strip()
            or "./data/bot.db",
            host=os.getenv("HOST", "0.0.0.0").strip() or "0.0.0.0",
            port=_parse_int(os.getenv("PORT"), 8000, minimum=1),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
            mcp_auth_token=os.getenv("MCP_AUTH_TOKEN", "").strip(),
        )

    def validate_runtime(self) -> None:
        """Validate required configuration before starting background services."""
        missing: list[str] = []
        if not self.matrix_homeserver:
            missing.append("MATRIX_HOMESERVER")
        if not self.matrix_user_id:
            missing.append("MATRIX_USER_ID")
        if not self.matrix_access_token and not self.matrix_password:
            missing.append("MATRIX_ACCESS_TOKEN or MATRIX_PASSWORD")
        if not self.dify_api_key:
            missing.append("DIFY_API_KEY")
        if missing:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing)}"
            )
