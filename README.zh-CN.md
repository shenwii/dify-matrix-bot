# Dify Matrix Bot & Scheduled Task MCP

[English](README.md) | 简体中文 | [日本語](README.ja.md)

基于 `matrix-nio`、`FastAPI`、`APScheduler` 和 `FastMCP`（SSE）构建的轻量级单容器服务，将支持端到端加密（E2EE）的 **Matrix AI 聊天机器人** 与 **Dify 定时任务 MCP 服务** 集成到你的 Dify 应用中。

---

## 核心功能

1. **Dify 定时任务 MCP Server（SSE）**
   - 在 FastAPI 服务上暴露 `/sse`（及 `/messages/`）端点，供 Dify 通过 SSE MCP 接入。
   - 提供 3 个 MCP 工具：
     - `create_scheduled_task`：新增定时任务（自动生成 `uuid`，参数：`title`、`cron`、`content`、`is_one_time`，以及可选 `username`、`room_id`）。
     - `list_scheduled_tasks`：查询所有定时任务及其下次执行时间。
     - `delete_scheduled_task`：根据 `task_uuid` 删除定时任务。
   - 到达 Cron 时间后使用 `DIFY_SCHEDULER_INPUTS` 回调 Dify `/chat-messages` 执行任务，并根据任务保存的 `username` 与 `room_id` 决定如何发送结果：
     - `room_id` 为空且 `username` 为空 → 静默执行，不向 Matrix 发送任何反馈。
     - `room_id` 为空且 `username` 非空 → 私信发送给该用户。
     - `room_id` 非空且 `username` 为空 → 发送到群房间，不 `@` 任何人。
     - `room_id` 非空且 `username` 非空 → 发送到群房间，并 `@` 该用户。
   - 一次性任务（`is_one_time=true`）执行后自动删除；任务持久化在 SQLite 中，重启自动恢复。

2. **Matrix AI 聊天机器人（`matrix-nio`）**
   - **端到端加密（E2EE）支持**：内置 `matrix-nio[e2e]`（`vodozemac`），开箱支持加密群聊与加密私聊；密钥持久化保存在 `MATRIX_STORE_PATH`，登录会话保存在 SQLite 中，容器重启不丢密钥。
   - **私聊与群组支持**：支持收到邀请后自动进群（`MATRIX_AUTO_JOIN=true`）；私聊直接回复，群组中通过 `@提及` 或可选前缀 `MATRIX_COMMAND_PREFIX` 触发回复。
   - **按人隔离上下文**：以 `(room_id, user_id)` 维度独立维护 Dify `conversation_id`（发送 `/reset` 或 `/clear` 可重置个人会话上下文）。
   - **输入中状态保持**：AI 后台处理期间每 `MATRIX_TYPING_INTERVAL_SECONDS`（默认 `15s`）自动续期 Matrix `typing` 状态，直到回复完成。
   - **模板化 `DIFY_INPUTS` 与 `$history`**：完全由你在配置中控制传给 Dify `inputs` 的字段，以及是否传入、如何传入最近群聊记录（`$history`）。

---

## 快速部署（Docker）

1. 复制并修改环境变量配置：
   ```bash
   cp .env.example .env
   ```
   填写 Matrix 登录信息、`DIFY_BASE_URL`、`DIFY_API_KEY`，并按需配置 `DIFY_INPUTS` 与 `DIFY_SCHEDULER_INPUTS`。

2. 启动容器：
   ```bash
   docker compose up -d --build
   ```

3. 检查服务健康状态或打开内置任务管理页：
   ```bash
   curl http://localhost:8000/health
   ```
   在浏览器打开 `http://localhost:8000/` 即可查看、手动新增与删除定时任务。

---

## 自定义 Dify `inputs`

### 1. 聊天机器人 Inputs（`DIFY_INPUTS`）

在 `.env` 中通过单行 JSON 模板配置 `DIFY_INPUTS`，支持 `$var` / `${var}` 占位符与静态字符串混用：

```env
DIFY_INPUTS={"username":"$user_id","room_id":"$room_id","source":"matrix","history":"$history"}
```

| 变量名 | 说明 |
| :--- | :--- |
| `$username` / `$user_name` | Matrix 发送者显示昵称 |
| `$user_id` | Matrix 发送者 MXID（如 `@alice:example.org`） |
| `$user_localpart` | Matrix 用户名部分（如 `alice`） |
| `$room_id` | 当前房间 ID |
| `$room_name` | 当前房间名称 |
| `$is_group` | 群聊（`member_count > 2`）为 `"true"`，否则为 `"false"` |
| `$source` | `"matrix"` |
| `$history` | 群聊中所有成员最近的 `MATRIX_GROUP_CONTEXT_LIMIT`（默认 `10`）条消息记录（按时间顺序）；私聊时为空字符串 `""` |

### 2. 定时任务触发时的 Inputs（`DIFY_SCHEDULER_INPUTS`）

定时任务触发时可用上下文比实时聊天少，因此使用独立的 `DIFY_SCHEDULER_INPUTS`，仅保留由 Dify 通过 MCP 传入的 `$username` 和 `$room_id`：

```env
DIFY_SCHEDULER_INPUTS={"username":"$username","room_id":"$room_id"}
```

如果未配置 `DIFY_INPUTS` 或 `DIFY_SCHEDULER_INPUTS`，则默认传 `{}`。

> [!TIP]
> 当 `room_id` 为空需要私信发送时，建议在 `DIFY_INPUTS` 中将 `$user_id`（如 `@alice:example.org`）作为 `username` 传给 Dify，这样 Dify 调用 `create_scheduled_task` 时可精准定位用户。

---

## 在 Dify 中接入 MCP（SSE）

在 Dify 的 MCP 配置中添加 SSE Server：
- **传输方式**：`SSE`
- **URL**：`http://<你的服务地址>:8000/sse`
- **认证 Header（可选）**：若 `.env` 中设置了 `MCP_AUTH_TOKEN`，请添加：
  ```
  Authorization: Bearer <你的MCP_AUTH_TOKEN>
  ```

---

## 环境变量参考

| 变量 | 必填 | 默认值 | 说明 |
| :--- | :---: | :--- | :--- |
| `MATRIX_HOMESERVER` | 是 | - | Matrix Homeserver 地址 |
| `MATRIX_USER_ID` | 是 | - | 机器人 MXID（如 `@bot:example.org`） |
| `MATRIX_ACCESS_TOKEN` | 条件 | - | Access Token（与 `MATRIX_PASSWORD` 二选一） |
| `MATRIX_PASSWORD` | 条件 | - | 登录密码（未填 Token 时使用） |
| `MATRIX_DEVICE_ID` | 否 | `DIFY_MATRIX_BOT` | Matrix 设备 ID |
| `MATRIX_AUTO_JOIN` | 否 | `true` | 是否自动接受房间邀请 |
| `MATRIX_E2EE_ENABLED` | 否 | `true` | 是否启用 Matrix 端到端加密（`vodozemac`） |
| `MATRIX_STORE_PATH` | 否 | `./data/crypto_store` | E2EE 密钥持久化目录（Docker 中建议 `/app/data/crypto_store`） |
| `MATRIX_TRUST_ALL_DEVICES` | 否 | `true` | 是否自动信任群成员设备，避免未验证设备拦截加密发送 |
| `MATRIX_GROUP_CONTEXT_LIMIT` | 否 | `10` | `$history` 保留并提取的最近群聊消息条数 |
| `MATRIX_COMMAND_PREFIX` | 否 | - | 群聊中可选的触发命令前缀 |
| `MATRIX_TYPING_INTERVAL_SECONDS` | 否 | `15` | `typing` 状态续期心跳间隔（秒） |
| `MATRIX_TYPING_TIMEOUT_MS` | 否 | `30000` | 发送给 Matrix 服务器的 typing 超时时间（毫秒） |
| `DIFY_BASE_URL` | 否 | `https://api.dify.ai/v1` | Dify API 基础地址 |
| `DIFY_API_KEY` | 是 | - | Dify 应用 API Key |
| `DIFY_TIMEOUT_SECONDS` | 否 | `180` | Dify 流式请求超时时间（秒） |
| `DIFY_SCHEDULED_USER` | 否 | `dify-scheduler` | 任务未传 `username` 时调用 Dify 使用的默认 `user` 标识 |
| `DIFY_INPUTS` | 否 | `{}` | Matrix 聊天时传给 Dify `inputs` 的 JSON 模板 |
| `DIFY_SCHEDULER_INPUTS` | 否 | `{}` | 定时任务触发时传给 Dify `inputs` 的 JSON 模板 |
| `TZ` | 否 | `Asia/Shanghai` | 系统及 Cron 调度器标准时区 |
| `DATABASE_PATH` | 否 | `./data/bot.db` | SQLite 路径（Docker 中建议 `/app/data/bot.db`） |
| `PORT` | 否 | `8000` | FastAPI 与 MCP 服务端口 |
| `MCP_AUTH_TOKEN` | 否 | - | 保护 `/sse` 与 `/messages/` 的可选 Bearer Token |
