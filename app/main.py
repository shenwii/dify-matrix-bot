"""FastAPI application entrypoint wiring MatrixBot, TaskScheduler, and MCP SSE server."""

from __future__ import annotations

import hmac
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import Settings
from app.database import Database
from app.dify_client import DifyClient
from app.matrix_bot import MatrixBot
from app.mcp_server import create_mcp_server
from app.scheduler import TaskScheduler
from app.web import create_web_router


@dataclass
class AppContainer:
    """Holds runtime service instances managed by FastAPI lifespan."""

    settings: Settings
    db: Database | None = None
    dify: DifyClient | None = None
    matrix_bot: MatrixBot | None = None
    scheduler: TaskScheduler | None = None


def _configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI + MCP SSE application."""
    cfg = settings or Settings.from_env()
    _configure_logging(cfg.log_level)
    logger = logging.getLogger(__name__)

    container = AppContainer(settings=cfg)

    def _get_scheduler() -> TaskScheduler:
        if container.scheduler is None:
            raise RuntimeError("TaskScheduler has not been initialized yet")
        return container.scheduler

    mcp = create_mcp_server(_get_scheduler)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        cfg.validate_runtime()

        db = Database(cfg.database_path)
        dify = DifyClient(
            base_url=cfg.dify_base_url,
            api_key=cfg.dify_api_key,
            timeout_seconds=cfg.dify_timeout_seconds,
        )
        matrix_bot = MatrixBot(settings=cfg, db=db, dify_client=dify)
        scheduler = TaskScheduler(
            settings=cfg,
            db=db,
            dify_client=dify,
            matrix_sender=matrix_bot,
        )

        container.db = db
        container.dify = dify
        container.matrix_bot = matrix_bot
        container.scheduler = scheduler

        await matrix_bot.start()
        scheduler.start()
        logger.info("Application startup complete (MCP SSE mounted at /sse)")
        try:
            yield
        finally:
            scheduler.stop()
            await matrix_bot.stop()
            await dify.close()
            db.close()
            logger.info("Application shutdown complete")

    app = FastAPI(
        title="Dify Matrix Bot & Scheduled Task MCP",
        version="0.1.0",
        lifespan=lifespan,
    )

    if cfg.mcp_auth_token:

        @app.middleware("http")
        async def mcp_bearer_auth(request: Request, call_next):
            path = request.url.path
            if path.startswith("/sse") or path.startswith("/messages"):
                auth_header = request.headers.get("Authorization", "")
                expected = f"Bearer {cfg.mcp_auth_token}"
                if not hmac.compare_digest(auth_header, expected):
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Invalid or missing MCP Bearer token"},
                    )
            return await call_next(request)

    @app.get("/health")
    async def health() -> dict:
        task_count = len(container.scheduler.list_tasks()) if container.scheduler else 0
        matrix_connected = (
            container.matrix_bot.is_connected if container.matrix_bot else False
        )
        return {
            "status": "ok",
            "matrix_connected": matrix_connected,
            "scheduled_tasks": task_count,
            "timezone": cfg.timezone,
        }

    app.include_router(create_web_router(_get_scheduler))

    # Mount MCP SSE server at root so /sse and /messages/ work seamlessly.
    app.mount("/", mcp.sse_app())
    return app


app = create_app()
