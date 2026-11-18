"""APScheduler-based cron task scheduler that invokes Dify and pushes results to Matrix."""

from __future__ import annotations

import logging
import uuid
from typing import Protocol
from zoneinfo import ZoneInfo

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import Settings
from app.database import Database, ScheduledTaskRecord
from app.dify_client import DifyClient, render_dify_inputs, validate_target_format

logger = logging.getLogger(__name__)


class MatrixSender(Protocol):
    """Protocol for validating targets and delivering scheduled task results to Matrix."""

    def validate_task_target(
        self, room_id: str, username: str
    ) -> tuple[str, str]: ...

    async def deliver_task_result(
        self, room_id: str, username: str, text: str
    ) -> None: ...


def build_scheduled_prompt(task: ScheduledTaskRecord) -> str:
    """Build the prompt sent to Dify when a scheduled task triggers."""
    task_kind = "一次性任务" if task.is_one_time else "周期性任务"
    return (
        "[定时任务触发]\n"
        f"任务ID: {task.uuid}\n"
        f"任务标题: {task.title}\n"
        f"任务类型: {task_kind}\n"
        "任务内容:\n"
        f"{task.content}\n\n"
        "请直接执行上述定时任务并返回最终结果，无需再次调用创建定时任务工具。"
    )


def format_scheduled_result(task: ScheduledTaskRecord, answer: str) -> str:
    """Format Dify's response before pushing it to the target Matrix room."""
    body = answer.strip() or "（AI 未返回文本内容）"
    return f"**[定时任务] {task.title}**\n\n{body}"


class TaskScheduler:
    """Manages persistent cron jobs and bridges execution between Dify and Matrix."""

    def __init__(
        self,
        settings: Settings,
        db: Database,
        dify_client: DifyClient,
        matrix_sender: MatrixSender,
    ) -> None:
        self._settings = settings
        self._db = db
        self._dify = dify_client
        self._matrix_sender = matrix_sender
        self._tz = ZoneInfo(settings.timezone)
        self._scheduler = AsyncIOScheduler(timezone=self._tz)

    def start(self) -> None:
        """Load persisted tasks from SQLite and start the scheduler."""
        tasks = self._db.list_tasks()
        for task in tasks:
            try:
                self._register_job(task)
            except Exception:
                logger.exception(
                    "Failed to register persisted task %s (%s)", task.uuid, task.cron
                )
        self._scheduler.start()
        logger.info(
            "TaskScheduler started with %d persisted task(s) in timezone %s",
            len(tasks),
            self._settings.timezone,
        )

    def stop(self) -> None:
        """Shut down the scheduler gracefully."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("TaskScheduler stopped")

    def _build_trigger(self, cron_expr: str) -> CronTrigger:
        cleaned = " ".join(cron_expr.strip().split())
        if not cleaned:
            raise ValueError("Cron expression cannot be empty")
        return CronTrigger.from_crontab(cleaned, timezone=self._tz)

    def _register_job(self, task: ScheduledTaskRecord) -> None:
        trigger = self._build_trigger(task.cron)
        self._scheduler.add_job(
            self._execute_task,
            trigger=trigger,
            id=task.uuid,
            args=[task.uuid],
            replace_existing=True,
            misfire_grace_time=300,
            coalesce=True,
        )

    def _get_next_run_iso(self, task_uuid: str) -> str | None:
        job = self._scheduler.get_job(task_uuid)
        if job and job.next_run_time:
            return job.next_run_time.isoformat()
        return None

    def create_task(
        self,
        title: str,
        cron: str,
        content: str,
        is_one_time: bool = False,
        username: str = "",
        room_id: str = "",
    ) -> dict:
        """Validate cron, persist a new task, and schedule it."""
        clean_title = title.strip()
        clean_cron = " ".join(cron.strip().split())
        clean_content = content.strip()
        if not clean_title:
            raise ValueError("title cannot be empty")
        if not clean_content:
            raise ValueError("content cannot be empty")

        clean_room_id, clean_username = validate_target_format(room_id, username)
        validator = getattr(self._matrix_sender, "validate_task_target", None)
        if callable(validator):
            clean_room_id, clean_username = validator(clean_room_id, clean_username)

        # Validate cron before touching the database.
        self._build_trigger(clean_cron)

        task_uuid = str(uuid.uuid4())
        task = self._db.create_task(
            task_uuid=task_uuid,
            title=clean_title,
            cron=clean_cron,
            content=clean_content,
            is_one_time=bool(is_one_time),
            username=clean_username,
            room_id=clean_room_id,
        )
        self._register_job(task)

        result = task.to_dict()
        result["next_run_time"] = self._get_next_run_iso(task.uuid)
        logger.info(
            "Scheduled task created: uuid=%s title=%r cron=%r is_one_time=%s username=%r room_id=%r",
            task.uuid,
            task.title,
            task.cron,
            task.is_one_time,
            task.username,
            task.room_id,
        )
        return result

    def list_tasks(self) -> list[dict]:
        """List all scheduled tasks with their next scheduled execution time."""
        items: list[dict] = []
        for task in self._db.list_tasks():
            item = task.to_dict()
            item["next_run_time"] = self._get_next_run_iso(task.uuid)
            items.append(item)
        return items

    def delete_task(self, task_uuid: str) -> bool:
        """Unschedule and delete a task by UUID."""
        clean_uuid = task_uuid.strip()
        if not clean_uuid:
            return False
        try:
            self._scheduler.remove_job(clean_uuid)
        except JobLookupError:
            pass
        deleted = self._db.delete_task(clean_uuid)
        if deleted:
            logger.info("Scheduled task deleted: uuid=%s", clean_uuid)
        return deleted

    async def _execute_task(self, task_uuid: str) -> None:
        task = self._db.get_task(task_uuid)
        if task is None:
            logger.warning("Task %s no longer exists in DB; skipping", task_uuid)
            try:
                self._scheduler.remove_job(task_uuid)
            except JobLookupError:
                pass
            return

        logger.info("Executing scheduled task %s (%s)", task.uuid, task.title)
        should_notify = bool(task.room_id or task.username)

        try:
            prompt = build_scheduled_prompt(task)
            dify_inputs = render_dify_inputs(
                template=self._settings.dify_scheduler_inputs_template,
                context={
                    "username": task.username,
                    "room_id": task.room_id,
                },
            )
            dify_res = await self._dify.chat(
                query=prompt,
                user=task.username or self._settings.dify_scheduled_user,
                inputs=dify_inputs,
            )
            if should_notify:
                message = format_scheduled_result(task, dify_res.answer)
                await self._matrix_sender.deliver_task_result(
                    room_id=task.room_id,
                    username=task.username,
                    text=message,
                )
            else:
                logger.info(
                    "Task %s executed silently (both username and room_id are empty)",
                    task.uuid,
                )
        except Exception as exc:
            logger.exception("Scheduled task %s failed during execution", task.uuid)
            if should_notify:
                try:
                    await self._matrix_sender.deliver_task_result(
                        room_id=task.room_id,
                        username=task.username,
                        text=f"**[定时任务失败] {task.title}**\n\n执行出错: `{exc}`",
                    )
                except Exception:
                    logger.exception(
                        "Failed to push error notice for task %s to Matrix", task.uuid
                    )
        finally:
            if task.is_one_time:
                self.delete_task(task.uuid)
                logger.info("One-time task %s completed and removed", task.uuid)
            else:
                self._db.mark_task_executed(task.uuid)
