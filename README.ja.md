<img src="docs/lilla-logo.svg" alt="Lilla" width="160" />

# lilla-core

Lilla のコアランタイム。AI エージェントを構築するための汎用基盤ライブラリです。

Discord ボットとして常駐し、メッセージを受け取って LLM（Ollama / OpenAI 互換）を
呼び出し、tool_call ループ・コマンド処理・返信を行う「エージェントとしての骨格」を
提供します。

キャラクター設定・特定ドメイン専用のツール（外部サービス連携など）・特定用途の
HTTP/ダッシュボードサーバーといった、利用者ごとに異なる要素はコアには含めません。
そうした要素は拡張ポイントと、起動時に読み込む `LILLA_EXTENSIONS_MODULE` 環境変数を
通じて外部から拡張できるようにしています。

lilla-core は拡張が一切登録されていない状態でも Discord bot として単体で起動できる
ことを設計上の前提にしています。各拡張ポイントは「未登録ならデフォルト動作に
フォールバックする」形で実装されており、コアが拡張の有無に依存しません。

## 制約

- 会話履歴・`runtime_state`（切り替え中の LLM プロバイダー、ツール無効化フラグ）・
  セッションメモリは、いずれもチャンネルやユーザーで分離されず、**プロセス全体で
  1 つ**を共有します。`repository/conversation_repository.py` はチャンネル/ユーザー
  で履歴を分離しないため、複数チャンネルの会話が 1 本の履歴に混ざります。
  lilla-core は単一オーナーが 1 つの bot プロセスと会話する構成を前提としており、
  複数ユーザー・マルチテナント向けの設計ではありません。

## 特徴

- Discord bot としてそのまま起動できる（拡張なしでも動作）
- Ollama（WakeOnLAN 対応）・OpenAI 互換 API の複数 LLM プロバイダを設定で切り替え可能
- tool_call ループ、会話履歴・ユーザーメモ・セッションメモリの管理
- `!` コマンドのプラグイン的な追加（`commands/` にファイルを置くだけ）
- APScheduler による定期タスク（`task_*.yaml`）
- 承認フロー（オーナー以外からのコマンド実行を Discord 上で承認/拒否）
- 7 種類の拡張ポイントによる、コアを変更しないカスタマイズ
  （追加リポジトリ、メッセージフック、起動タスク、結果配送、クライアント固有
  プロンプト、会話開始フック、ツール実行 context）
- Discord に見せる文言はロケールカタログ（`ja` / `en`）から取得

## 動作要件

- Python 3.12 以上
- MongoDB（会話履歴・ユーザーメモ等の永続化に使用）
- Discord Bot トークン

## セットアップ

```bash
pip install -e ".[dev]"

export CONFIG_ROOT=./config.example
export DISCORD_TOKEN=...
export MONGODB_URI=mongodb://xxx

python -m lilla_core.bot
```

`CONFIG_ROOT` が指すディレクトリには `lilla.yaml`（非秘匿の構造設定）と、必要に応じて
`logging.yaml` を配置します。サンプルは `config.example/` を参照してください。

> **プライバシーに関する注意:** `config.example/logging.yaml` は root ロガーを
> `DEBUG` に設定しています。この場合 `core/http_util.py` がリクエスト/レスポンス
> 本文を標準出力にログ出力し、LLM へのリクエスト本文（システムプロンプトや会話
> 履歴を含む）が出力されることがあります。標準出力を収集する環境では特に
> 注意してください。

秘匿情報（トークン・接続文字列など）は環境変数または `.env` から読み込みます
（`.env.example` を参照）。OS 環境変数が `.env` より優先されます。

開発時は、`.env` の値が起動時にプロセスの環境変数へも反映されます（既に export
済みの値が優先されます）。これは LLM プロバイダーの API キー（`GROK_API_KEY` など、
`lilla.yaml` の `api_key_env` で指定した名前）にも当てはまり、`AppConfig` 経由ではなく
`api/llm_client.py` が環境変数から直接読み取ります。

拡張を登録して起動する場合は、`LILLA_EXTENSIONS_MODULE` 環境変数に拡張モジュールの
import パスを指定してください（未指定の場合はコア単体で起動します）。

```bash
export LILLA_EXTENSIONS_MODULE=my_extension_package
python -m lilla_core.bot
```

### Discordボットのセットアップ

[Discord Developer Portal](https://discord.com/developers/applications) のアプリケーション
の **Bot** ページで、招待前に以下を設定してください。

**Privileged Gateway Intents（特権インテント）**

- **Message Content Intent** — ON にしてください。`bot_client.py` が
  `intents.message_content = True` を要求しています。これを ON にしないと Discord は
  メッセージ本文を空にして配送するため、Bot はメッセージ内容を読めず、何も応答できなく
  なります。

それ以外の特権インテント（Server Members / Presence）は不要です。コアはメンバー情報や
在席状態を利用していません。

**Bot招待URL生成時に付与する Permissions**

招待URL（OAuth2 URL Generator、`bot` スコープ）を生成する際は、最低限以下にチェックを
入れてください。

- **View Channels** — 読み書きが必要なチャンネルを閲覧するため
- **Send Messages** — 返信・コマンド応答・承認依頼の投稿
  （`commands/` / `handlers/` 各所の `message.reply()` / `channel.send()`）
- **Read Message History** — `cleardirty` が `channel.fetch_message()` で過去メッセージを
  参照するため
- **Attach Files** — `selftest` と承認フローが `discord.File` でファイルを添付送信する
  ため

Message Content Intent を有効にし忘れると、Bot はメッセージを受信しているように見えても
`message.content` が空になり、何も反応しなくなります（本文が空になる症状）。上記の
Permissions が不足している場合は、送信・返信エラーや、必要なチャンネルが見えないといった
症状になります。

## テスト

```bash
pytest tests/
```

## ディレクトリ構成（概要）

```
src/lilla_core/
├── bot.py                # Discord bot エントリポイント
├── bot_client.py         # commands.Bot インスタンスの共有モジュール
├── core/                 # 設定管理・拡張ポイント・共通ユーティリティ
├── commands/             # `!コマンド名` の実装（1 コマンド 1 ファイル）
├── handlers/             # Discord イベント/コマンドのディスパッチ、定期タスク管理
├── services/             # tool_call ループ・会話履歴・システムプロンプト構築など
├── ui/ locales/          # Discord に見せる文言のカタログ（ja / en）
├── utils/                # 日付・パス・ハッシュ等の共通ユーティリティ
├── loaders/               # ツール（llm_*.yaml / task_*.yaml）の動的ロード
├── api/                  # LLM クライアント（Ollama / OpenAI 互換）
├── repository/           # MongoDB へのデータ永続化
└── tool_support/         # ツール実装向けの opt-in ヘルパー群
tests/                    # pytest による単体テスト
config.example/           # lilla.yaml / logging.yaml のサンプル
```

詳細な設計・各ファイルの役割は [`CLAUDE.md`](./CLAUDE.md) にまとめています。

## 拡張ポイント

コアの外から機能を差し込むための拡張ポイントを 7 種類提供しています。いずれも
「未登録ならコア単体でも安全に動くデフォルト」を持ちます。

| 登録関数 | 用途 |
|----------|------|
| `register_startup_repo` | `on_ready` で初期化する追加リポジトリファクトリ |
| `register_message_hook` | `on_message` の冒頭で必ず呼ばれるハンドラ |
| `register_startup_task` | `bot.start()` の前に await される起動タスク（HTTP サーバー等） |
| `register_result_delivery` | `!toolresult` の配送先を `client_type` ごとに差し替え |
| `register_client_prompt_provider` | `client_type` ごとのシステムプロンプト追記 |
| `register_conversation_start_hook` | 会話処理開始前のクライアント固有の前処理 |
| `register_tool_context_provider` | ツール実行 context への値の注入 |

拡張は `AppConfig` のサブクラス定義と `set_config()` による差し替え、
`core/extension_points.py` の登録関数の呼び出しによって、コアのコードを変更せずに
機能を追加できます。

## ツール契約

ツールは `${CONFIG_ROOT}/tools/` の YAML 設定ファイルをもとに、`${TOOL_ROOT}/**/*.py`
から動的にロードされます。各 YAML のファイル名（stem）がツール名になり、`type`
フィールドから対応する `.py` ファイルを探します（詳細は
`loaders/llm_tool_loader.py` / `loaders/task_tool_loader.py` の実装を参照）。

**LLM ツール**（`${CONFIG_ROOT}/tools/llm_*.yaml`、実装は `${TOOL_ROOT}/**/<type>.py`）:

- `.py` ファイルはモジュールレベルの `SCHEMA`（OpenAI の tools 形式の function
  schema dict）を定義するか、YAML の設定から動的にスキーマを組み立てたい場合は
  `build_schema(config: dict) -> dict` 関数を定義します（`SCHEMA` より先に確認
  されます）。
- `async def execute(tool_input: dict, context: dict) -> dict` も定義する必要があり、
  標準の結果 dict（`success` / `tool_name` / `data` / `error` など。ヘルパーは
  `tool_support/tool_result.py` を参照）を返します。
- YAML の `type` が `self` の場合、ローダーは `TOOL_ROOT` 配下の検索を行わず、
  YAML と同じディレクトリ・同名の `.py` をそのままロードします。
- YAML では `description`（スキーマの description を上書き）、
  `supported_client_type`（既定 `"all"`）、`cache` ブロック、その他ツール固有の
  キーを設定できます。ツール固有のキーは、下記の実行時コンテキストキーと衝突
  してはいけません（起動時に検証され、衝突時は fail-fast します）。

**task ツール**（`${CONFIG_ROOT}/tools/task_*.yaml`、実装は
`${TOOL_ROOT}/**/<type>.py`）:

- `.py` ファイルはクラスを定義し、`cls(config, name)` の形でインスタンス化されます
  （`config` は `_yaml_path` を加えた YAML の dict、`name` は YAML の stem）。
- インスタンスは `description` / `category` / `schedule`（cron 式の文字列）属性と、
  `async def execute(context: dict) -> None` を持つことが期待されます。
- `schedule` は省略可能です。未設定のツールはスケジューラには登録されませんが、
  `!runtask` による手動実行は可能です。

**`execute` に渡される `context`** は呼び出し元によって内容が異なります。LLM
ツールでは常に `client_type` と、入れ子呼び出し用のヘルパー
`call_tool(tool_name, tool_input)` に加え、そのツールの YAML 固有のキーと、
`register_tool_context_provider` で登録されたキーが入ります。task ツールでは、
スケジュール実行時は `discord_client` / `now` / `llm_tools` が、`!runtask` による
手動実行時はさらに `params` が渡されます。

## コントリビュート

Issue / Pull Request を歓迎します。セットアップ・テスト・ブランチ / PR の進め方・
コミット規約は [`CONTRIBUTING.md`](./CONTRIBUTING.md) を参照してください。
設計の詳細や開発ルールは [`CLAUDE.md`](./CLAUDE.md) にまとめています。

## 変更履歴

変更履歴は [`CHANGELOG.md`](./CHANGELOG.md) を参照してください。

## ライセンス

MIT
