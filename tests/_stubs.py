"""Shared lightweight stubs for optional third-party libraries during unit tests."""

from __future__ import annotations

import importlib.util
import sys
import types
from dataclasses import dataclass


def _is_missing(module_name: str) -> bool:
    if module_name in sys.modules:
        return False
    return importlib.util.find_spec(module_name) is None


def ensure_httpx_stub() -> None:
    if not _is_missing("httpx"):
        return
    httpx_stub = types.ModuleType("httpx")
    httpx_stub.AsyncClient = type("AsyncClient", (), {})
    httpx_stub.Timeout = lambda *args, **kwargs: None
    sys.modules["httpx"] = httpx_stub


def ensure_markdown_stub() -> None:
    if not _is_missing("markdown"):
        return
    md_stub = types.ModuleType("markdown")
    md_stub.markdown = lambda text, **kwargs: f"<p>{text}</p>"
    sys.modules["markdown"] = md_stub


def ensure_nio_stub() -> None:
    if not _is_missing("nio"):
        return
    nio_stub = types.ModuleType("nio")

    class _DummyAsyncClient:
        def __init__(
            self, homeserver="", user="", device_id="", store_path=None, config=None
        ) -> None:
            self.homeserver = homeserver
            self.user_id = user
            self.device_id = device_id
            self.access_token = ""
            self.store_path = store_path
            self.config = config
            self.rooms = {}

        @property
        def logged_in(self) -> bool:
            return bool(self.user_id and self.device_id and self.access_token)

    class _DummyRoomPreset:
        private_chat = "private_chat"

    class _DummyRoomVisibility:
        private = "private"

    nio_stub.AsyncClient = _DummyAsyncClient
    nio_stub.AsyncClientConfig = lambda **kwargs: types.SimpleNamespace(**kwargs)
    nio_stub.RoomPreset = _DummyRoomPreset
    nio_stub.RoomVisibility = _DummyRoomVisibility
    for attr in [
        "InviteMemberEvent",
        "LoginResponse",
        "MatrixRoom",
        "MegolmEvent",
        "RoomMessageText",
        "RoomSendResponse",
    ]:
        setattr(nio_stub, attr, type(attr, (), {}))

    nio_exc = types.ModuleType("nio.exceptions")

    class OlmUnverifiedDeviceError(Exception):
        def __init__(self, msg="", device=None) -> None:
            super().__init__(msg)
            self.device = device

    nio_exc.OlmUnverifiedDeviceError = OlmUnverifiedDeviceError
    sys.modules["nio.exceptions"] = nio_exc
    sys.modules["nio"] = nio_stub


def ensure_apscheduler_stub() -> None:
    if not _is_missing("apscheduler"):
        return
    aps_mod = types.ModuleType("apscheduler")
    jobstores_mod = types.ModuleType("apscheduler.jobstores")
    base_mod = types.ModuleType("apscheduler.jobstores.base")

    class JobLookupError(KeyError):
        pass

    base_mod.JobLookupError = JobLookupError

    sched_pkg = types.ModuleType("apscheduler.schedulers")
    asyncio_mod = types.ModuleType("apscheduler.schedulers.asyncio")

    @dataclass
    class _DummyJob:
        id: str
        func: object
        trigger: object
        args: list
        next_run_time: object = None

    class AsyncIOScheduler:
        def __init__(self, timezone=None) -> None:
            self.timezone = timezone
            self.jobs: dict[str, _DummyJob] = {}
            self.running = False

        def start(self) -> None:
            self.running = True

        def shutdown(self, wait: bool = False) -> None:
            self.running = False

        def add_job(self, func, trigger=None, id=None, args=None, **kwargs) -> _DummyJob:
            job = _DummyJob(id=id, func=func, trigger=trigger, args=args or [])
            self.jobs[id] = job
            return job

        def get_job(self, job_id: str):
            return self.jobs.get(job_id)

        def remove_job(self, job_id: str) -> None:
            if job_id not in self.jobs:
                raise JobLookupError(job_id)
            del self.jobs[job_id]

    asyncio_mod.AsyncIOScheduler = AsyncIOScheduler

    triggers_pkg = types.ModuleType("apscheduler.triggers")
    cron_mod = types.ModuleType("apscheduler.triggers.cron")

    class CronTrigger:
        def __init__(self, expr: str) -> None:
            self.expr = expr

        @classmethod
        def from_crontab(cls, expr: str, timezone=None) -> "CronTrigger":
            parts = expr.split()
            if len(parts) != 5:
                raise ValueError(f"Invalid crontab: {expr}")
            return cls(expr)

    cron_mod.CronTrigger = CronTrigger

    sys.modules["apscheduler"] = aps_mod
    sys.modules["apscheduler.jobstores"] = jobstores_mod
    sys.modules["apscheduler.jobstores.base"] = base_mod
    sys.modules["apscheduler.schedulers"] = sched_pkg
    sys.modules["apscheduler.schedulers.asyncio"] = asyncio_mod
    sys.modules["apscheduler.triggers"] = triggers_pkg
    sys.modules["apscheduler.triggers.cron"] = cron_mod


def ensure_fastapi_pydantic_stub() -> None:
    if _is_missing("pydantic"):
        pyd_mod = types.ModuleType("pydantic")

        class BaseModel:
            def __init__(self, **kwargs) -> None:
                for k, v in kwargs.items():
                    setattr(self, k, v)

        pyd_mod.BaseModel = BaseModel
        pyd_mod.Field = lambda default=..., **kwargs: default
        sys.modules["pydantic"] = pyd_mod

    if _is_missing("fastapi"):
        fa_mod = types.ModuleType("fastapi")
        fa_resp = types.ModuleType("fastapi.responses")

        class HTTPException(Exception):
            def __init__(self, status_code: int, detail: str) -> None:
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        class APIRouter:
            def __init__(self) -> None:
                self.routes: dict[tuple[str, str], object] = {}

            def get(self, path: str, **kwargs):
                def _dec(fn):
                    self.routes[("GET", path)] = fn
                    return fn
                return _dec

            def post(self, path: str, **kwargs):
                def _dec(fn):
                    self.routes[("POST", path)] = fn
                    return fn
                return _dec

            def delete(self, path: str, **kwargs):
                def _dec(fn):
                    self.routes[("DELETE", path)] = fn
                    return fn
                return _dec

        class HTMLResponse:
            def __init__(self, content: str) -> None:
                self.content = content

        fa_mod.APIRouter = APIRouter
        fa_mod.HTTPException = HTTPException
        fa_resp.HTMLResponse = HTMLResponse
        sys.modules["fastapi"] = fa_mod
        sys.modules["fastapi.responses"] = fa_resp


def ensure_mcp_stub() -> None:
    if not _is_missing("mcp"):
        return
    mcp_pkg = types.ModuleType("mcp")
    mcp_server_pkg = types.ModuleType("mcp.server")
    fastmcp_mod = types.ModuleType("mcp.server.fastmcp")
    sec_mod = types.ModuleType("mcp.server.transport_security")

    @dataclass
    class TransportSecuritySettings:
        enable_dns_rebinding_protection: bool = True
        allowed_hosts: list[str] | None = None
        allowed_origins: list[str] | None = None

    class FastMCP:
        def __init__(
            self,
            name: str,
            host: str = "127.0.0.1",
            transport_security: TransportSecuritySettings | None = None,
        ) -> None:
            self.name = name
            self.host = host
            self.transport_security = transport_security
            self.tools: dict[str, object] = {}

        def tool(self):
            def _dec(fn):
                self.tools[fn.__name__] = fn
                return fn
            return _dec

    sec_mod.TransportSecuritySettings = TransportSecuritySettings
    fastmcp_mod.FastMCP = FastMCP

    sys.modules["mcp"] = mcp_pkg
    sys.modules["mcp.server"] = mcp_server_pkg
    sys.modules["mcp.server.fastmcp"] = fastmcp_mod
    sys.modules["mcp.server.transport_security"] = sec_mod
