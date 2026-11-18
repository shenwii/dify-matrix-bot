"""FastMCP SSE server exposing scheduled task management tools to Dify."""

from __future__ import annotations

import inspect
from typing import Any, Callable, Sequence

from mcp.server.fastmcp import FastMCP

from app.scheduler import TaskScheduler


def _build_fastmcp_kwargs(
    host: str = "0.0.0.0",
    allowed_hosts: Sequence[str] = (),
) -> dict[str, Any]:
    """Build FastMCP kwargs compatible across mcp SDK versions without blocking LAN/Docker hosts."""
    kwargs: dict[str, Any] = {}
    params = inspect.signature(FastMCP.__init__).parameters
    if "host" in params:
        kwargs["host"] = host
    if "transport_security" in params:
        try:
            from mcp.server.transport_security import TransportSecuritySettings
        except ImportError:
            TransportSecuritySettings = None  # type: ignore[assignment]
        if TransportSecuritySettings is not None:
            clean_hosts = [h.strip() for h in allowed_hosts if h and h.strip()]
            if clean_hosts:
                kwargs["transport_security"] = TransportSecuritySettings(
                    enable_dns_rebinding_protection=True,
                    allowed_hosts=clean_hosts,
                )
            else:
                kwargs["transport_security"] = TransportSecuritySettings(
                    enable_dns_rebinding_protection=False,
                )
    return kwargs


def create_mcp_server(
    get_scheduler: Callable[[], TaskScheduler],
    host: str = "0.0.0.0",
    allowed_hosts: Sequence[str] = (),
) -> FastMCP:
    """Create and configure the FastMCP server with the 3 task management tools."""
    mcp = FastMCP(
        "dify-scheduler",
        **_build_fastmcp_kwargs(host=host, allowed_hosts=allowed_hosts),
    )

    @mcp.tool()
    def create_scheduled_task(
        title: str,
        cron: str,
        content: str,
        is_one_time: bool = False,
        user_id: str = "",
        room_id: str = "",
    ) -> dict:
        """新增一个定时任务（自动生成 UUID）。

        Args:
            title: 定时任务标题，例如 "每日早报"。
            cron: 标准 5 位 Cron 表达式（分 时 日 月 周），例如 "0 9 * * *" 表示每天 09:00。
            content: 定时任务触发时需要 AI 执行的具体指令或内容。
            is_one_time: 是否为一次性任务。为 True 时触发一次后自动删除；为 False 时按 Cron 周期执行。
            user_id: 可选，触发任务的目标用户 Matrix ID（如 "@alice:example.org"，通常由 Dify 上下文传入；与 room_id 同时为空时不发送任何通知；群聊有 room_id 时用于 @ 该用户；无 room_id 时私信该用户）。
            room_id: 可选，任务结果推送的 Matrix 房间 ID（为空且 user_id 非空时将私信给 user_id）。
        """
        scheduler = get_scheduler()
        task = scheduler.create_task(
            title=title,
            cron=cron,
            content=content,
            is_one_time=is_one_time,
            user_id=user_id,
            room_id=room_id,
        )
        return {
            "status": "created",
            "task": task,
        }

    @mcp.tool()
    def list_scheduled_tasks() -> dict:
        """查询当前所有的定时任务列表。"""
        scheduler = get_scheduler()
        tasks = scheduler.list_tasks()
        return {
            "count": len(tasks),
            "tasks": tasks,
        }

    @mcp.tool()
    def delete_scheduled_task(task_uuid: str) -> dict:
        """根据任务 UUID 删除指定的定时任务。

        Args:
            task_uuid: 定时任务的 UUID 字符串。
        """
        scheduler = get_scheduler()
        deleted = scheduler.delete_task(task_uuid)
        if not deleted:
            return {
                "status": "not_found",
                "uuid": task_uuid,
                "message": f"未找到 UUID 为 {task_uuid} 的定时任务。",
            }
        return {
            "status": "deleted",
            "uuid": task_uuid,
            "message": f"已删除定时任务 {task_uuid}。",
        }

    return mcp
