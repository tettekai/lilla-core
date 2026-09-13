<img src="docs/lilla-logo.svg" alt="Lilla" width="160" />

# lilla-core

Lilla のコアランタイム。AI エージェントを構築するための汎用基盤ライブラリです。

Discord ボットとして常駐し、メッセージを受け取って LLM（Ollama / OpenAI 互換）を
呼び出し、tool_call ループ・コマンド処理・返信を行う「エージェントとしての骨格」を
提供します。

キャラクター設定・特定ドメイン専用のツール（外部サービス連携など）・特定用途の
HTTP/ダッシュボードサーバーといった、利用者ごとに異なる要素はコアには含めません。
そうした要素は `Extension`（`core/extension.py`）のサブクラスと、起動時に読み込む
`LILLA_EXTENSIONS` 環境変数を通じて外部から拡張できるようにしています。

lilla-core は拡張を 1 つも読み込まない状態でも Discord bot として単体で起動できる
ことを設計上の前提にしています。`Extension` の各メソッドは「何も貢献しない」
デフォルトを持ち、コアが拡張の有無に依存しません。

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
- `Extension` アダプタクラスによる、コアを変更しないカスタマイズ。1 プロセスで
  複数の拡張を読み込める（追加リポジトリ、メッセージフック、起動処理、結果配送、
  クライアント固有プロンプト、会話開始フック、ツール実行 context、追加ツール
  ルート、追加コマンドパッケージ）
- Discord に見せる文言はロケールカタログ（`ja` / `en`）から取得
- 定期実行・「今日」・LLM に見せる現在時刻のタイムゾーンを `ui.timezone` で統一

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

`lilla.yaml` には `llm.providers` に少なくとも 1 つの provider を定義し、
`llm.default` がそのいずれかの provider 名と一致している必要があります。
一致しない場合は起動時に `ValidationError` で失敗します。

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

拡張を読み込んで起動する場合は、`LILLA_EXTENSIONS` 環境変数にモジュールの import
パスをカンマ区切りで指定してください（未指定・空ならコア単体で起動します）。
モジュールは指定した順に読み込まれます。

```bash
export LILLA_EXTENSIONS=my_extension_package,another_pack
python -m lilla_core.bot
```

### タイムゾーン

`lilla.yaml` の `ui.timezone` が、ボットにとっての「人間側の今日 / いま」を決めます。
定期タスクの crontab、システムプロンプトに埋め込む現在時刻、会話履歴の対象期間、
`today` などの相対日付は、すべてこの設定に従います。

- 未指定（または YAML の `null`）: プロセスの OS のローカルタイムゾーン
- `Asia/Tokyo` のような IANA 名: そのタイムゾーンのみを使い、OS のタイムゾーンは見ない
- 空文字、および `zoneinfo` が受け付けない名前: 起動時に失敗する。フォールバックは
  しないため、書き間違いによって日付だけが静かに 1 日ずれることはない

`utils/datetime_utils.py` の `local_timezone()` / `local_now()` / `to_jst_date()` /
`jst_day_end_utc()` は、いずれも呼び出しのたびに同じタイムゾーンへ解決します。
時計とカレンダー日付がずれることはありません。例外は `JST` 定数だけで、これは
`ui.timezone` の値にかかわらず UTC+9 のままです（日本時間を明示したいコード向け）。

> **注意:** コンテナイメージは OS のタイムゾーンが UTC のまま動くのが普通です。
> その環境で `ui.timezone` を未指定にすると、cron のスケジュールも「今日」も UTC に
> なります。日付が重要な場合は必ず明示してください。

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
├── core/                 # 設定管理・Extension 基底クラス・共通ユーティリティ
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

## 拡張

拡張は `lilla_core.core.extension.Extension` を継承し、必要なメソッドだけを
オーバーライドして、モジュールから `extension` 属性として 1 つだけ export します。
各メソッドは「何も貢献しない」デフォルトを持つため、コアは単体でも動き続けます。

```python
from lilla_core.core.extension import Extension


class MyExtension(Extension):
    name = "my-extension"

    def tool_context_providers(self):
        return {"my_client": get_my_client}

    async def setup(self, ctx):
        await start_my_http_server(ctx.llm_tools, ctx.tools, ctx.bot)


extension = MyExtension()
```

| メソッド | 用途 |
|----------|------|
| `startup_repos` | `on_ready` で初期化する追加リポジトリファクトリ |
| `on_message` | `on_message` の冒頭で呼ばれる。`True` を返すと以降の処理を止める |
| `setup` | `bot.start()` の前に await される起動処理（HTTP サーバー等）。引数は `SetupContext` 1 つ（`tools` / `llm_tools` / `bot` / `config`）で、フィールドの追加は既存の拡張を壊さない |
| `result_deliveries` | `!toolresult` の配送先を `client_type` ごとに指定 |
| `client_prompt_providers` | `client_type` ごとのシステムプロンプト追記。形は `{client_type: [プロバイダ, ...]}` の加算式で、複数の拡張が同じ `client_type` に足せる。コアがロード順に空行区切りで連結する |
| `conversation_start_hooks` | 会話処理開始前に await される非同期フック。形は `{client_type: [フック, ...]}` でプロンプトと同じく加算式。各フックは `ConversationContext`（`client_type` / `client_state` / `discord_channel_id` / `llm_name`）1 つを受け取る |
| `tool_context_providers` | ツール実行 context への値の注入 |
| `tool_roots` | ツールの `.py` を探す追加ディレクトリ |
| `command_packages` | `@register_command` を探す追加パッケージ |
| `config_models` | この拡張が `AppConfig` に足す YAML セクション |
| `env_fields` | この拡張が `cfg.env` に足す秘匿フィールド |
| `required_config_sections` | 自分では提供しないが読む YAML セクション |
| `required_env_fields` | 自分では提供しないが読む `cfg.env` のフィールド |
| `required_tool_context_keys` | 自分では提供しないが、自分のツールが読むツール context のキー |
| `requires`（クラス属性） | 依存する拡張の名前。ロード済みで、かつ `LILLA_EXTENSIONS` で自分より前に並んでいる必要がある |
| `api_version`（クラス属性） | この拡張が書かれた `Extension` 契約のバージョン。既定はコアの現在の `EXTENSION_API_VERSION` で、受け付けない値はロード時に失敗する |

貢献キーは **拡張どうし** で衝突してはいけません。`Extension.name`・YAML セクション名・
env フィールド名・ツール context のキー・結果配送の `client_type`・コマンド名・複数ルートに
またがる同名ツールファイルのいずれも、静かに勝者を決めず起動時に fail-fast します。
クライアント固有プロンプトと会話開始フックだけは設計上の例外で、`client_type` ごとの
リストをロード順に連結するため、複数の拡張が同じクライアントへ足せます。
`client_type="discord"` だけは扱いが 2 点異なります。システムプロンプトはコアが内蔵
デフォルトを持ち、拡張が 1 つも出していないときだけそれを使います。`!toolresult` の
配送はコアが持つため拡張は登録できません。

`run_conversation()` を自分で呼ぶクライアント拡張は、任意のオブジェクトを
`client_state` として渡せます（接続中ソケットの集合など）。コアは中身を解釈せず、
フックには `ConversationContext.client_state` として、LLM ツールには context の
`client_state` キーとしてそのまま渡します。

### 設定の合成

拡張は自分が足す設定を申告し、コアが起動時にすべての申告から 1 つの Pydantic
モデルを組みます。`AppConfig` のサブクラスを手書きして、モジュールの import 副作用
として `set_config()` で差し替える方式はもう契約に含みません。合成したインスタンスが
拡張の差し込みを上書きするため、`LILLA_EXTENSIONS` の並び順は設定に影響しません。

```python
class GoogleConfig(BaseModel):
    client_id: str | None = None
    redirect_uri: str = "http://localhost/google-callback"


class MyExtension(Extension):
    name = "my-extension"

    def config_models(self):
        return {"google": GoogleConfig}

    def env_fields(self):
        return {"google_client_secret": "GOOGLE_CLIENT_SECRET"}
```

これで `get_config().google.client_id` と `get_config().env.google_client_secret` が
プロセス全体から読めるようになります。`get_config()` の型は基底の `AppConfig` なので、
型検査や補完のためにセクションをそのモデルの型で受け取りたいときは `get_section()` を
使ってください。

```python
from lilla_core.core.config import get_section

client_id = get_section("google", GoogleConfig).client_id
```

セクションが申告されていない場合や、値が渡したモデルのインスタンスでない場合は
`ValueError` になります。名前の綴りを間違えても静かに空を返すことはありません。

- セクションは、モデルが必須フィールドを 1 つでも持てば **必須**、そうでなければ
  省略可能になります。必須セクションが `lilla.yaml` に無ければ起動時に落ちます
- 合成される env フィールドの型は常に `str | None`（既定値 `None`）です。契約が型を
  運ばないため、必須フィールドや文字列以外の秘匿情報はこの経路では表現できません
- 同じセクション名・同じ env フィールド名を 2 つの拡張が提供したら、たとえモデルが
  同一でも fail-fast します。コア確定の名前も同様に予約済みです
- `required_config_sections()` には、自分では提供しないが読むセクション名を並べます
  （別のパックが持つ共有の `google:` セクションなど）。`required_env_fields()` と
  `required_tool_context_keys()` は `cfg.env` のフィールドとツール context のキーについて
  同じことをします。誰も提供しておらず、コア確定の名前でもなければロードに失敗し、
  要求した拡張の名前を示します

### 拡張どうしの依存

依存する拡張は、クラス属性 `requires` に名前を並べて宣言します。コアはロード時に、
それらが **ロード済みで、かつ `LILLA_EXTENSIONS` で自分より前に並んでいる** ことを
検証します。メッセージフック・`setup()`・ツールルートはロード順に処理されるため、
コアが並べ替えることはありません。

```python
class GoogleCalendarExtension(Extension):
    name = "lilla-google-calendar"
    requires = ("lilla-google-oauth",)

    def required_config_sections(self):
        return ["google"]

    def required_env_fields(self):
        return ["google_client_secret"]

    def required_tool_context_keys(self):
        return ["google_client"]
```

`requires` は「相手のパックが正しい順でロードされているか」を、`required_*` の各メソッドは
「自分が読む具体的なものを誰かが提供しているか」を検証します。汎用の `validate()` フックは
持たず、実行時の検査は `setup()` で行ってください。

`LILLA_EXTENSIONS` に並べたモジュールは同一プロセスで動く **信頼コード** です。
サンドボックスではありません。ツールを読み込むディレクトリ（`paths.tool_root`・
各拡張の `tool_roots()`・`${CONFIG_ROOT}/tools`）も同じ扱いで、そこへ書き込める者は
Bot のプロセス内でコードを実行できます。そのためツールパスの許可リストは別途持ちません。
ローダーは、解決後のツールファイルがこれらのディレクトリの配下にあることだけを確認します
（`..` を含む `type` や、外を指すシンボリックリンクは読み込みません）。

### 拡張契約の互換性

`lilla_core.core.extension.EXTENSION_API_VERSION` はこのコアが提供する `Extension`
契約のバージョン、`SUPPORTED_EXTENSION_API_VERSIONS` はロード時に受け付けるバージョンの
集合です。拡張は自分が書かれたバージョンを固定できます。

```python
from lilla_core.core.extension import EXTENSION_API_VERSION, Extension


class MyExtension(Extension):
    name = "my-extension"
    api_version = 1  # 省略すると EXTENSION_API_VERSION
```

宣言したバージョンを受け付けない場合、`load_extensions()` は拡張名と両方のバージョンを
示して失敗します。古い契約で書かれた拡張をそのまま読み込んで、起動後に壊れるのを
防ぐためです。

契約を変えるときの方針:

- **非破壊（バージョンは上げない）**: `Extension` へのメソッド追加（必ず「何も貢献しない」
  既定を持たせる）、`*Context` データクラス（`SetupContext` / `ConversationContext`）への
  フィールド追加、参照関数やコア確定の context キーの追加
- **破壊的（`EXTENSION_API_VERSION` を上げる）**: メソッドのシグネチャや戻り値の形の変更、
  メソッド・`*Context` のフィールド・context キー・参照関数の削除や改名、フックが呼ばれる
  タイミングの変更。こうした変更は `CHANGELOG.md` に **BREAKING** として記録します
- 拡張パッケージは `lilla-core` をバージョン範囲で依存指定してください
  （例: `lilla-core>=0.3,<0.4`）。破壊的なコアのリリースを気付かず取り込まないためです

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
- YAML の `type` にドットが含まれる場合（`type: lilla_google_calendar.tools.calendar_get`）は
  **import パス** として扱い、ツールルートを探索する代わりに `importlib` でそのモジュールを
  読み込みます。インストール済みパッケージ（PyPI で配布する拡張など）がツールを同梱する
  ための経路です。通常の import なので、そのドット区切り名で `sys.modules` に登録され
  （stem で解決したファイルは独立したモジュールとして実行され、登録されません）、
  モジュール内の相対 import が使え、ディレクトリの検査も行いません。import に失敗した
  場合はファイルが見つからないときと同じく警告を出し、そのツールだけ読み飛ばします。
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
- `type` はこちらでも import パスを受け付けます（`type: some_package.tasks.daily_summary`）。
  クラスはファイルから読む場合と同じ規則で、import したモジュールから探します。

**`execute` に渡される `context`** は呼び出し元によって内容が異なります。LLM
ツールでは常に `client_type` と、入れ子呼び出し用のヘルパー
`call_tool(tool_name, tool_input)` に加え、そのツールの YAML 固有のキーと、
拡張の `tool_context_providers()` が返すキー、そして呼び出し元クライアントが
`run_conversation()` に渡した場合は `client_state` が入ります。task ツールにも同じく
拡張が提供するキーが入り、加えてスケジュール実行時は `discord_client` / `now` /
`llm_tools` が、`!runtask` による手動実行時はさらに `params` が渡されます。
どちらのコア確定キーも予約済みで、`tool_context_providers()` が同じ名前を返す拡張は
静かに上書きされる代わりにロード時に失敗します。

## コントリビュート

Issue / Pull Request を歓迎します。セットアップ・テスト・ブランチ / PR の進め方・
コミット規約は [`CONTRIBUTING.md`](./CONTRIBUTING.md) を参照してください。
設計の詳細や開発ルールは [`CLAUDE.md`](./CLAUDE.md) にまとめています。

## 変更履歴

変更履歴は [`CHANGELOG.md`](./CHANGELOG.md) を参照してください。

## ライセンス

MIT
