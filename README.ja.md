# Dify Matrix Bot & Scheduled Task MCP

[English](README.md) | [简体中文](README.zh-CN.md) | 日本語

`matrix-nio`、`FastAPI`、`APScheduler`、`FastMCP`（SSE）で構築された軽量な単一コンテナサービスです。E2EE（エンドツーエンド暗号化）対応の **Matrix AI チャットボット** と **Dify スケジュールタスク MCP サーバー** を Dify アプリケーションに統合します。

---

## 主な機能

1. **Dify スケジュールタスク MCP サーバー（SSE）**
   - FastAPI サーバー上で `/sse`（および `/messages/`）を公開し、Dify から SSE MCP として接続できます。
   - 3 つの MCP ツールを提供：
     - `create_scheduled_task`：定期/単発タスクを作成（`uuid` は自動生成。`title`、`cron`、`content`、`is_one_time`、および任意の `username`・`room_id` を指定）。
     - `list_scheduled_tasks`：登録済みタスク一覧と次回実行時刻を取得。
     - `delete_scheduled_task`：`task_uuid` を指定してタスクを削除。
   - Cron 実行時刻になると `DIFY_SCHEDULER_INPUTS` を用いて Dify `/chat-messages` を呼び出し、`username` と `room_id` に応じて通知先を制御します：
     - `room_id` が空かつ `username` が空 → サイレント実行（Matrix へ通知しません）。
     - `room_id` が空かつ `username` が非空 → 該当ユーザーへ DM（ダイレクトメッセージ）送信。
     - `room_id` が非空かつ `username` が空 → グループルームへメンションなしで送信。
     - `room_id` が非空かつ `username` が非空 → グループルームへ `username` を `@メンション` して送信。
   - 単発タスク（`is_one_time=true`）は実行後に自動削除されます。タスクは SQLite に永続化され、再起動後も自動復元されます。

2. **Matrix AI ボット（`matrix-nio`）**
   - **E2EE（エンドツーエンド暗号化）対応**：`matrix-nio[e2e]`（`vodozemac`）により暗号化ルーム・暗号化 DM に標準対応。暗号鍵は `MATRIX_STORE_PATH` に、ログインセッションは SQLite に永続化され、再起動後も維持されます。
   - **DM・グループ対応**：招待時に自動参加（`MATRIX_AUTO_JOIN=true`）。DM では直接応答し、グループでは `@メンション` または `MATRIX_COMMAND_PREFIX` で応答します。
   - **ユーザーごとの会話分離**：`(room_id, user_id)` 単位で Dify の `conversation_id` を独立管理します（`/reset` または `/clear` で自分の会話履歴をリセット可能）。
   - **入力中（Typing）ステータス維持**：AI が処理中の間、`MATRIX_TYPING_INTERVAL_SECONDS`（デフォルト `15秒`）ごとに Matrix の `typing` 通知を自動延長します。
   - **テンプレートベースの `DIFY_INPUTS` と `$history`**：Dify の `inputs` に渡すフィールドや、直近のグループ会話履歴（`$history`）を渡すかどうか・形式を自由に制御できます。

---

## クイックスタート（Docker）

1. 環境変数ファイルをコピーして編集します：
   ```bash
   cp .env.example .env
   ```
   Matrix 認証情報、`DIFY_BASE_URL`、`DIFY_API_KEY` を設定し、必要に応じて `DIFY_INPUTS` と `DIFY_SCHEDULER_INPUTS` を調整してください。

2. コンテナを起動します：
   ```bash
   docker compose up -d --build
   ```

3. ヘルスチェックを実行するか、内蔵タスク管理ページを開きます：
   ```bash
   curl http://localhost:8000/health
   ```
   ブラウザで `http://localhost:8000/` を開くと、タスク一覧の確認・手動追加・削除ができます。

---

## Dify `inputs` のカスタマイズ

### 1. チャットボット用 Inputs（`DIFY_INPUTS`）

`.env` の `DIFY_INPUTS` に 1 行の JSON テンプレートを設定します。`$var` / `${var}` プレースホルダーと固定値を混在させることができます：

```env
DIFY_INPUTS={"username":"$user_id","room_id":"$room_id","source":"matrix","history":"$history"}
```

| 変数名 | 説明 |
| :--- | :--- |
| `$username` / `$user_name` | Matrix 送信者の表示名 |
| `$user_id` | 送信者の Matrix MXID（例：`@alice:example.org`） |
| `$user_localpart` | Matrix ユーザー名のローカルパート（例：`alice`） |
| `$room_id` | 現在のルーム ID |
| `$room_name` | 現在のルーム名 |
| `$is_group` | グループルーム（`member_count > 2`）の場合は `"true"`、それ以外は `"false"` |
| `$source` | `"matrix"` |
| `$history` | グループ内全員の直近 `MATRIX_GROUP_CONTEXT_LIMIT`（デフォルト `10`）件のメッセージ履歴（時系列）。DM 時は空文字 `""` |

### 2. スケジューラー用 Inputs（`DIFY_SCHEDULER_INPUTS`）

スケジュール実行時は通常チャットより取得できるコンテキストが少ないため、専用の `DIFY_SCHEDULER_INPUTS` を使用し、MCP 経由で Dify から渡された `$username` と `$room_id` のみを参照します：

```env
DIFY_SCHEDULER_INPUTS={"username":"$username","room_id":"$room_id"}
```

`DIFY_INPUTS` や `DIFY_SCHEDULER_INPUTS` が未設定の場合は `{}` が送信されます。

> [!TIP]
> `room_id` が空のときに DM 送信を確実に行うため、`DIFY_INPUTS` では `$user_id`（例：`@alice:example.org`）を `username` として Dify へ渡すことを推奨します。

---

## Dify への MCP（SSE）接続手順

Dify の MCP 設定で SSE サーバーを追加します：
- **方式**：`SSE`
- **URL**：`http://<サーバーアドレス>:8000/sse`
- **認証ヘッダー（任意）**：`.env` に `MCP_AUTH_TOKEN` を設定した場合は以下を追加：
  ```
  Authorization: Bearer <設定したMCP_AUTH_TOKEN>
  ```

---

## 環境変数一覧

| 変数名 | 必須 | デフォルト | 説明 |
| :--- | :---: | :--- | :--- |
| `MATRIX_HOMESERVER` | はい | - | Matrix ホームサーバー URL |
| `MATRIX_USER_ID` | はい | - | ボットの MXID（例：`@bot:example.org`） |
| `MATRIX_ACCESS_TOKEN` | 条件 | - | アクセストークン（または `MATRIX_PASSWORD` を指定） |
| `MATRIX_PASSWORD` | 条件 | - | `MATRIX_ACCESS_TOKEN` 未設定時のログインパスワード |
| `MATRIX_DEVICE_ID` | いいえ | `DIFY_MATRIX_BOT` | Matrix デバイス ID |
| `MATRIX_AUTO_JOIN` | いいえ | `true` | ルーム招待を自動承諾するか |
| `MATRIX_E2EE_ENABLED` | いいえ | `true` | Matrix E2EE（`vodozemac`）を有効にするか |
| `MATRIX_STORE_PATH` | いいえ | `./data/crypto_store` | E2EE 鍵ストア保存先ディレクトリ（Docker では `/app/data/crypto_store`） |
| `MATRIX_TRUST_ALL_DEVICES` | いいえ | `true` | 暗号化送信時にルームメンバーのデバイスを自動信頼するか |
| `MATRIX_GROUP_CONTEXT_LIMIT` | いいえ | `10` | `$history` に含めるグループ直近メッセージ件数 |
| `MATRIX_COMMAND_PREFIX` | いいえ | - | グループで応答トリガーに使う任意のコマンドプレフィックス |
| `MATRIX_TYPING_INTERVAL_SECONDS` | いいえ | `15` | Typing 通知の更新間隔（秒） |
| `MATRIX_TYPING_TIMEOUT_MS` | いいえ | `30000` | Matrix サーバーへ送る Typing タイムアウト（ミリ秒） |
| `DIFY_BASE_URL` | いいえ | `https://api.dify.ai/v1` | Dify API ベース URL |
| `DIFY_API_KEY` | はい | - | Dify アプリの API キー |
| `DIFY_TIMEOUT_SECONDS` | いいえ | `180` | Dify ストリーミング API タイムアウト（秒） |
| `DIFY_SCHEDULED_USER` | いいえ | `dify-scheduler` | タスクの `username` が空の場合に使うフォールバック `user` 識別子 |
| `DIFY_INPUTS` | いいえ | `{}` | Matrix チャット時の Dify `inputs` 用 JSON テンプレート |
| `DIFY_SCHEDULER_INPUTS` | いいえ | `{}` | スケジュール実行時の Dify `inputs` 用 JSON テンプレート |
| `TZ` | いいえ | `Asia/Shanghai` | システムおよび Cron スケジューラーのタイムゾーン |
| `DATABASE_PATH` | いいえ | `./data/bot.db` | SQLite DB パス（Docker では `/app/data/bot.db`） |
| `PORT` | いいえ | `8000` | FastAPI および MCP サーバーのポート |
| `MCP_AUTH_TOKEN` | いいえ | - | `/sse` と `/messages/` を保護する任意の Bearer トークン |
