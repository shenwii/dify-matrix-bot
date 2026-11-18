# Dify Matrix Bot & Scheduled Task MCP

English | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

A lightweight, single-container service built with `matrix-nio`, `FastAPI`, `APScheduler`, and `FastMCP` (SSE) that connects a **Matrix AI Chatbot** (with E2EE support) and a **Dify Scheduled Task MCP Server** to your Dify application.

---

## Features

1. **Dify Scheduled Task MCP Server (SSE)**
   - Exposes an MCP SSE endpoint at `/sse` (and `/messages/`) on the FastAPI server for Dify to connect.
   - Provides 3 MCP tools:
     - `create_scheduled_task`: Create a scheduled task with auto-generated `uuid`, `title`, `cron`, `content`, `is_one_time`, plus optional `user_id` and `room_id`.
     - `list_scheduled_tasks`: List all scheduled tasks and their next run times.
     - `delete_scheduled_task`: Delete a scheduled task by `task_uuid`.
   - When a cron trigger fires, the scheduler invokes Dify `/chat-messages` with `DIFY_SCHEDULER_INPUTS` and routes the response based on `user_id` and `room_id`:
     - `room_id` empty & `user_id` empty → Executes silently (no Matrix message sent).
     - `room_id` empty & `user_id` non-empty → Sends a Direct Message (DM) to `user_id`.
     - `room_id` non-empty & `user_id` empty → Sends to the group room without `@mentioning` anyone.
     - `room_id` non-empty & `user_id` non-empty → Sends to the group room and `@mentions` `user_id`.
   - One-time tasks (`is_one_time=true`) are automatically removed after execution. All tasks are persisted in SQLite and restored across restarts.

2. **Matrix AI Bot (`matrix-nio`)**
   - **End-to-End Encryption (E2EE)**: Supports encrypted rooms and encrypted DMs out of the box via `matrix-nio[e2e]` (`vodozemac`), persisting cryptographic keys in `MATRIX_STORE_PATH` and login sessions in SQLite across container restarts.
   - **DM & Group Chat Support**: Auto-joins rooms on invite (`MATRIX_AUTO_JOIN=true`). Responds directly in DMs, and responds in groups when `@mentioned` or triggered with `MATRIX_COMMAND_PREFIX`.
   - **Per-User Conversation Isolation**: Maintains independent Dify `conversation_id`s per `(room_id, user_id)`. Users can send `/reset` or `/clear` to clear their own context.
   - **Typing Indicator Heartbeat**: Keeps the Matrix "typing..." indicator alive every `MATRIX_TYPING_INTERVAL_SECONDS` (default `15s`) while waiting for slow Dify responses.
   - **Template-Driven `DIFY_INPUTS` & `$history`**: Full control over Dify's `inputs` payload and whether/how recent group chat history (`$history`) is passed.

---

## Quick Start (Docker)

1. Copy and edit the environment file:
   ```bash
   cp .env.example .env
   ```
   Fill in your Matrix credentials, `DIFY_BASE_URL`, `DIFY_API_KEY`, and customize `DIFY_INPUTS` / `DIFY_SCHEDULER_INPUTS` as needed.

2. Start the container:
   ```bash
   docker compose up -d --build
   ```

3. Verify service health or open the built-in task dashboard:
   ```bash
   curl http://localhost:8000/health
   ```
   Open `http://localhost:8000/` in your browser to view, create, or delete scheduled tasks.

---

## Customizing Dify `inputs`

### 1. Chat Bot Inputs (`DIFY_INPUTS`)

Configure `DIFY_INPUTS` as a single-line JSON object using `$var` or `${var}` placeholders and static values. You can customize the JSON keys to match whatever input names your Dify app expects:

```env
DIFY_INPUTS={"user_id":"$user_id","user_name":"$user_name","room_id":"$room_id","source":"matrix","history":"$history"}
```

| Variable | Description |
| :--- | :--- |
| `$user_id` | Sender Matrix MXID (e.g. `@alice:example.org`) |
| `$user_name` | Sender display name in Matrix |
| `$user_localpart` | Matrix username localpart (e.g. `alice`) |
| `$room_id` | Current Matrix room ID |
| `$room_name` | Current Matrix room name |
| `$is_group` | `"true"` in group rooms (`member_count > 2`), `"false"` otherwise |
| `$source` | `"matrix"` |
| `$history` | Chronological transcript of the latest `MATRIX_GROUP_CONTEXT_LIMIT` (default `10`) messages from all participants in the group; `""` in DMs |

### 2. Scheduler Inputs (`DIFY_SCHEDULER_INPUTS`)

When a scheduled task triggers, less context is available than in live chat, so a dedicated template `DIFY_SCHEDULER_INPUTS` is used with `$user_id`, `$user_localpart`, `$room_id`, `$task_uuid`, and `$task_title`:

```env
DIFY_SCHEDULER_INPUTS={"user_id":"$user_id","room_id":"$room_id"}
```

If either `DIFY_INPUTS` or `DIFY_SCHEDULER_INPUTS` is omitted or empty, `{}` is sent as `inputs`.

> [!TIP]
> Across this project, `user_id` always refers to the Matrix MXID (e.g. `@alice:example.org`) and `user_name` refers to the display name. Passing `$user_id` to Dify ensures Dify passes an unambiguous Matrix ID to `create_scheduled_task` for reliable DM and `@mention` delivery.

---

## Connecting Dify to MCP (SSE)

Add an MCP Server in Dify with:
- **Transport**: `SSE`
- **URL**: `http://<your-host>:8000/sse`
- **Authorization Header (optional)**: If `MCP_AUTH_TOKEN` is set in `.env`, add:
  ```
  Authorization: Bearer <your-MCP_AUTH_TOKEN>
  ```

---

## Environment Variables

| Variable | Required | Default | Description |
| :--- | :---: | :--- | :--- |
| `MATRIX_HOMESERVER` | Yes | - | Matrix homeserver URL |
| `MATRIX_USER_ID` | Yes | - | Bot MXID (e.g. `@bot:example.org`) |
| `MATRIX_ACCESS_TOKEN` | Cond. | - | Access token (or provide `MATRIX_PASSWORD`) |
| `MATRIX_PASSWORD` | Cond. | - | Password used when `MATRIX_ACCESS_TOKEN` is empty |
| `MATRIX_DEVICE_ID` | No | `DIFY_MATRIX_BOT` | Matrix device ID |
| `MATRIX_AUTO_JOIN` | No | `true` | Automatically accept room invitations |
| `MATRIX_E2EE_ENABLED` | No | `true` | Enable Matrix End-to-End Encryption (`vodozemac`) |
| `MATRIX_STORE_PATH` | No | `./data/crypto_store` | Persistent E2EE key store directory (`/app/data/crypto_store` in Docker) |
| `MATRIX_TRUST_ALL_DEVICES` | No | `true` | Automatically trust room members' devices for seamless E2EE replies |
| `MATRIX_GROUP_CONTEXT_LIMIT` | No | `10` | Max recent group messages included in `$history` |
| `MATRIX_COMMAND_PREFIX` | No | - | Optional command prefix to trigger the bot in groups |
| `MATRIX_TYPING_INTERVAL_SECONDS` | No | `15` | Typing heartbeat refresh interval |
| `MATRIX_TYPING_TIMEOUT_MS` | No | `30000` | Typing event timeout sent to Matrix server |
| `DIFY_BASE_URL` | No | `https://api.dify.ai/v1` | Dify API base URL |
| `DIFY_API_KEY` | Yes | - | Dify App API key |
| `DIFY_TIMEOUT_SECONDS` | No | `180` | Dify HTTP streaming timeout |
| `DIFY_SCHEDULED_USER` | No | `dify-scheduler` | Fallback `user` field passed to Dify when task `user_id` is empty |
| `DIFY_INPUTS` | No | `{}` | JSON template for Dify `inputs` during Matrix chat |
| `DIFY_SCHEDULER_INPUTS` | No | `{}` | JSON template for Dify `inputs` during scheduled task runs |
| `TZ` | No | `Asia/Shanghai` | Standard system & cron scheduler timezone |
| `DATABASE_PATH` | No | `./data/bot.db` | SQLite database file path (`/app/data/bot.db` in Docker) |
| `PORT` | No | `8000` | FastAPI & MCP server port |
| `MCP_AUTH_TOKEN` | No | - | Optional Bearer token protecting `/sse` and `/messages/` |
