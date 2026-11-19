"""Simple built-in web UI and JSON API for viewing, adding, and deleting scheduled tasks."""

from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.scheduler import TaskScheduler


class CreateTaskRequest(BaseModel):
    """Validated payload for creating a scheduled task from the web UI."""

    title: str = Field(..., min_length=1, max_length=200)
    cron: str = Field(..., min_length=1, max_length=100)
    content: str = Field(..., min_length=1, max_length=10000)
    is_one_time: bool = False
    user_id: str = Field(default="", max_length=255)
    room_id: str = Field(default="", max_length=255)


_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Dify Matrix Bot - Scheduled Tasks</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #f6f7f9;
      --card: #ffffff;
      --text: #1f2937;
      --muted: #6b7280;
      --border: #e5e7eb;
      --primary: #2563eb;
      --danger: #dc2626;
      --ok: #16a34a;
      --warn: #d97706;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #111827;
        --card: #1f2937;
        --text: #f3f4f6;
        --muted: #9ca3af;
        --border: #374151;
      }
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      padding: 24px 16px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
    }
    .container { max-width: 1080px; margin: 0 auto; display: grid; gap: 20px; }
    .header {
      display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px;
    }
    h1 { margin: 0; font-size: 1.4rem; }
    h2 { margin: 0 0 12px; font-size: 1.1rem; }
    .status-bar { display: flex; gap: 10px; align-items: center; font-size: 0.875rem; color: var(--muted); }
    .badge {
      padding: 2px 10px; border-radius: 999px; font-weight: 600; font-size: 0.78rem;
      border: 1px solid var(--border); background: var(--card);
    }
    .badge.ok { color: var(--ok); border-color: var(--ok); }
    .badge.warn { color: var(--warn); border-color: var(--warn); }
    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 18px;
    }
    .grid-form {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
    }
    .full { grid-column: 1 / -1; }
    label { display: block; font-size: 0.85rem; font-weight: 600; margin-bottom: 4px; }
    input[type="text"], textarea {
      width: 100%;
      padding: 8px 10px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: transparent;
      color: var(--text);
      font: inherit;
    }
    textarea { min-height: 82px; resize: vertical; }
    .hint { font-size: 0.78rem; color: var(--muted); margin-top: 3px; }
    .checkbox-row { display: flex; align-items: center; gap: 8px; padding-top: 4px; }
    .actions { display: flex; gap: 10px; align-items: center; }
    button {
      cursor: pointer;
      border: 1px solid transparent;
      border-radius: 6px;
      padding: 8px 14px;
      font: inherit;
      font-weight: 600;
    }
    button.primary { background: var(--primary); color: #fff; }
    button.secondary { background: transparent; border-color: var(--border); color: var(--text); }
    button.danger { background: transparent; border-color: var(--danger); color: var(--danger); padding: 4px 10px; font-size: 0.82rem; }
    button:disabled { opacity: 0.6; cursor: not-allowed; }
    .msg {
      padding: 10px 12px; border-radius: 6px; font-size: 0.88rem; margin-bottom: 12px; display: none;
    }
    .msg.error { display: block; border: 1px solid var(--danger); color: var(--danger); }
    .msg.success { display: block; border: 1px solid var(--ok); color: var(--ok); }
    .table-wrap { overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
    th, td { text-align: left; padding: 10px 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
    th { color: var(--muted); font-weight: 600; white-space: nowrap; }
    code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.82rem; }
    .empty { text-align: center; color: var(--muted); padding: 24px 0; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.8rem; word-break: break-all; }
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>Dify Matrix Bot - Scheduled Tasks</h1>
      <div class="status-bar">
        <span id="matrixBadge" class="badge">Matrix: ...</span>
        <span id="tzBadge" class="badge">TZ: ...</span>
        <button id="refreshBtn" class="secondary" type="button">Refresh</button>
      </div>
    </div>

    <div class="card">
      <h2>Create Scheduled Task</h2>
      <div id="formMsg" class="msg"></div>
      <form id="createForm" class="grid-form" novalidate>
        <div>
          <label for="title">Title *</label>
          <input id="title" name="title" type="text" placeholder="e.g. Daily Digest" required />
        </div>
        <div>
          <label for="cron">Cron Expression (5 fields) *</label>
          <input id="cron" name="cron" type="text" placeholder="0 9 * * *" required />
          <div class="hint">Format: min hour day month weekday (e.g. <code>*/30 * * * *</code>)</div>
        </div>
        <div>
          <label for="room_id">Target Room ID (optional)</label>
          <input id="room_id" name="room_id" type="text" placeholder="!roomid:example.org" />
          <div class="hint">Leave empty with User ID set to send a DM</div>
        </div>
        <div>
          <label for="user_id">Target User ID (optional)</label>
          <input id="user_id" name="user_id" type="text" placeholder="@alice:example.org" />
          <div class="hint">Leave both Room ID and User ID empty to run silently</div>
        </div>
        <div class="full">
          <label for="content">Prompt / Content *</label>
          <textarea id="content" name="content" placeholder="Instructions sent to Dify when the schedule triggers..." required></textarea>
        </div>
        <div class="full actions" style="justify-content: space-between;">
          <label class="checkbox-row" style="margin: 0;">
            <input id="is_one_time" name="is_one_time" type="checkbox" />
            <span>One-time task (auto-delete after execution)</span>
          </label>
          <button id="submitBtn" class="primary" type="submit">Create Task</button>
        </div>
      </form>
    </div>

    <div class="card">
      <h2>Scheduled Tasks (<span id="taskCount">0</span>)</h2>
      <div id="listMsg" class="msg"></div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Title / Content</th>
              <th>Cron</th>
              <th>Type</th>
              <th>Target</th>
              <th>Next Run / Stats</th>
              <th>UUID</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody id="taskBody">
            <tr><td colspan="7" class="empty">Loading...</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <script>
    const taskBody = document.getElementById("taskBody");
    const taskCount = document.getElementById("taskCount");
    const formMsg = document.getElementById("formMsg");
    const listMsg = document.getElementById("listMsg");
    const createForm = document.getElementById("createForm");
    const submitBtn = document.getElementById("submitBtn");
    const refreshBtn = document.getElementById("refreshBtn");
    const matrixBadge = document.getElementById("matrixBadge");
    const tzBadge = document.getElementById("tzBadge");

    function esc(str) {
      return String(str ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }

    function showMsg(el, type, text) {
      el.className = "msg " + type;
      el.textContent = text;
    }

    function clearMsg(el) {
      el.className = "msg";
      el.textContent = "";
    }

    function formatTime(iso) {
      if (!iso) return "-";
      const d = new Date(iso);
      return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
    }

    function validateClient(payload) {
      if (!payload.title) return "Please enter a task title.";
      if (!payload.cron) return "Please enter a cron expression.";
      if (payload.cron.split(/\\s+/).length !== 5) {
        return "Cron expression must have 5 fields (min hour day month weekday), e.g. 0 9 * * *";
      }
      if (!payload.content) return "Please enter task content.";
      if (payload.room_id && !/^![^:\\s]+(?::[^\\s]+)?$/.test(payload.room_id)) {
        return "Invalid Room ID format; expected !roomid:server or !roomhash (or leave empty).";
      }
      if (payload.user_id && payload.user_id.startsWith("@") && payload.user_id.includes(":") && !/^@[^:\\s]+:[^\\s]+$/.test(payload.user_id)) {
        return "Invalid User ID (MXID) format; expected @user:server.";
      }
      return null;
    }

    async function loadHealth() {
      try {
        const res = await fetch("/health");
        if (!res.ok) return;
        const data = await res.json();
        matrixBadge.textContent = data.matrix_connected ? "Matrix: Connected" : "Matrix: Disconnected";
        matrixBadge.className = "badge " + (data.matrix_connected ? "ok" : "warn");
        if (data.timezone) tzBadge.textContent = "TZ: " + data.timezone;
      } catch (_) {}
    }

    async function loadTasks() {
      clearMsg(listMsg);
      try {
        const res = await fetch("/api/tasks");
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Failed to load tasks");
        const tasks = data.tasks || [];
        taskCount.textContent = String(tasks.length);
        if (tasks.length === 0) {
          taskBody.innerHTML = '<tr><td colspan="7" class="empty">No scheduled tasks</td></tr>';
          return;
        }
        taskBody.innerHTML = tasks.map((t) => {
          const target = (!t.room_id && !t.user_id)
            ? '<span class="hint">Silent</span>'
            : `${t.room_id ? "<div>Room: <code>" + esc(t.room_id) + "</code></div>" : ""}${t.user_id ? "<div>User ID: <code>" + esc(t.user_id) + "</code></div>" : ""}`;
          return `
            <tr>
              <td><strong>${esc(t.title)}</strong><div class="hint" style="white-space:pre-wrap;">${esc(t.content)}</div></td>
              <td><code>${esc(t.cron)}</code></td>
              <td>${t.is_one_time ? "One-time" : "Recurring"}</td>
              <td>${target}</td>
              <td>
                <div>Next: ${esc(formatTime(t.next_run_time))}</div>
                <div class="hint">Runs: ${esc(t.run_count)}${t.last_run_at ? " / Last: " + esc(formatTime(t.last_run_at)) : ""}</div>
              </td>
              <td class="mono">${esc(t.uuid)}</td>
              <td><button class="danger" type="button" data-delete="${esc(t.uuid)}">Delete</button></td>
            </tr>
          `;
        }).join("");
      } catch (err) {
        showMsg(listMsg, "error", "Failed to load tasks: " + err.message);
      }
    }

    createForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      clearMsg(formMsg);
      const payload = {
        title: document.getElementById("title").value.trim(),
        cron: document.getElementById("cron").value.trim().replace(/\\s+/g, " "),
        content: document.getElementById("content").value.trim(),
        is_one_time: document.getElementById("is_one_time").checked,
        room_id: document.getElementById("room_id").value.trim(),
        user_id: document.getElementById("user_id").value.trim(),
      };
      const clientErr = validateClient(payload);
      if (clientErr) {
        showMsg(formMsg, "error", clientErr);
        return;
      }

      submitBtn.disabled = true;
      try {
        const res = await fetch("/api/tasks", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await res.json();
        if (!res.ok) {
          const detail = Array.isArray(data.detail)
            ? data.detail.map((d) => d.msg).join("; ")
            : (data.detail || "Failed to create task");
          throw new Error(detail);
        }
        showMsg(formMsg, "success", "Task created: " + data.task.uuid);
        createForm.reset();
        await loadTasks();
      } catch (err) {
        showMsg(formMsg, "error", err.message);
      } finally {
        submitBtn.disabled = false;
      }
    });

    taskBody.addEventListener("click", async (e) => {
      const btn = e.target.closest("button[data-delete]");
      if (!btn) return;
      const uuid = btn.getAttribute("data-delete");
      if (!confirm("Delete scheduled task " + uuid + "?")) return;
      btn.disabled = true;
      clearMsg(listMsg);
      try {
        const res = await fetch("/api/tasks/" + encodeURIComponent(uuid), { method: "DELETE" });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Failed to delete task");
        await loadTasks();
      } catch (err) {
        showMsg(listMsg, "error", "Failed to delete task: " + err.message);
        btn.disabled = false;
      }
    });

    refreshBtn.addEventListener("click", () => {
      loadHealth();
      loadTasks();
    });

    loadHealth();
    loadTasks();
  </script>
</body>
</html>
"""


def create_web_router(get_scheduler: Callable[[], TaskScheduler]) -> APIRouter:
    """Create FastAPI router for the task management web UI and REST API."""
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index() -> HTMLResponse:
        return HTMLResponse(content=_DASHBOARD_HTML)

    @router.get("/api/tasks")
    async def api_list_tasks() -> dict:
        scheduler = get_scheduler()
        tasks = scheduler.list_tasks()
        return {"count": len(tasks), "tasks": tasks}

    @router.post("/api/tasks", status_code=201)
    async def api_create_task(payload: CreateTaskRequest) -> dict:
        scheduler = get_scheduler()
        try:
            task = scheduler.create_task(
                title=payload.title,
                cron=payload.cron,
                content=payload.content,
                is_one_time=payload.is_one_time,
                user_id=payload.user_id,
                room_id=payload.room_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"status": "created", "task": task}

    @router.delete("/api/tasks/{task_uuid}")
    async def api_delete_task(task_uuid: str) -> dict:
        scheduler = get_scheduler()
        deleted = scheduler.delete_task(task_uuid)
        if not deleted:
            raise HTTPException(
                status_code=404, detail=f"Task {task_uuid} not found"
            )
        return {"status": "deleted", "uuid": task_uuid}

    return router
