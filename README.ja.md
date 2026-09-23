<img src="docs/lilla-logo.svg" alt="Lilla" width="160" />

# lilla-core

Lilla のコアランタイム。AI エージェントを構築するための汎用基盤ライブラリです。

Discord ボットとして常駐し、メッセージを受け取って LLM（Ollama / OpenAI 互換）を
呼び出し、tool_call ループ・コマンド処理・返信を行う「エージェントとしての骨格」を
提供します。

キャラクター設定・特定ドメイン専用のツール（外部サービス連携など）・用途特化の
HTTP サーバー（機械向けの Bearer API など）といった、利用者ごとに異なる要素はコアには
含めません。そうした要素は `Extension`（`core/extension.py`）のサブクラスと、起動時に
読み込む `LILLA_EXTENSIONS` 環境変数を通じて外部から拡張できるようにしています。

例外は **観測用ダッシュボード** で、これはコアが持ちます。見せる中身（会話履歴・
ユーザーメモ・ログ）がすべてコアの状態だからです。拡張は `dashboard_*()` の申告で
ここへタブと HTTP ルートを足せます。

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
- Discord に見せる文言はロケールカタログ（`ja` / `en`）から取得。拡張も自分の名前で
  カタログを同梱できる
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

### 登録チャンネル

`lilla.yaml` の `discord.channels` に登録したチャンネルでは、入口の作法を変えられます。

```yaml
discord:
  my_user_id: "XXXXXXXXX"
  channels:
    - name: dev
      channel_id: "123456789012345678"
      mention_optional: true
    - name: lounge
      channel_id: "234567890123456789"
      # mention_optional 省略時は false
```

- `name`: 設定上の別名。Discord 側の現在のチャンネル名と一致していなくてかまいません
- `channel_id`: チャンネルの snowflake 文字列（`approval_channel_id` などと同じ形式）
- `mention_optional`: `true` なら、そのチャンネルではオーナーのメンションなしの発言にも
  応答します。省略時は `false` で、受信条件は現行どおり（メンションまたは DM）です
- `channels` 未設定・空リストなら、受信動作はまったく変わりません
- `name` または `channel_id` が重複していると起動時に失敗します

登録チャンネルでの会話では、システムプロンプトに「今この登録チャンネルにいる」旨の
短い一節が入ります（未登録チャンネル・DM には入りません）。会話履歴そのものは登録の
有無によらず全チャンネル横断のままで、チャンネルごとに分かれることはありません。

### 部屋のノート（深夜要約）

登録チャンネルは「部屋のノート」を持てます。その日にその部屋で何を話したかを短い事実
として残すもので、会話履歴の TTL で消えたあとも残ります。これを書く深夜バッチはコア
組み込みのタスクツールとして同梱していますが、既定では有効化されていません。
`${CONFIG_ROOT}/tools/task_channel_summary.yaml` に以下の YAML を置くと opt-in で
有効になります。

```yaml
type: lilla_core.builtin_tools.task_channel_summary
# schedule: "0 2 * * *"      # 既定値。ui.timezone で解釈されます
# llm_name: summarizer       # 既定は llm.default
# max_turns: 500             # 1 チャンネルあたり読む発言数
# max_transcript_chars: 20000
```

- 実行のたびに `discord.channels` をループし、**前日**（`ui.timezone` の暦日）を要約
  します。2 時実行で「当日」を対象にすると 0:00–2:00 しか入らないためです
- 対象は `discord_channel_id` が付いている発言だけで、付いていない既存の発言は
  対象外です（穴埋めはしません）
- 対象日の発言が無いチャンネルは、既存のノートをそのまま残します
- 要約にはキャラクター用のシステムプロンプトを使わず、短い事実抽出用のプロンプトを
  使います。本文は `<channel_transcript>` タグで囲んだデータとして渡します
- ノートは `channel_summaries` コレクションに、1 チャンネル 1 ドキュメントで保存します
  （`discord_channel_id` がユニークで、実行のたびに upsert します）
- `discord.channels` が空のとき、またはこの YAML が無いときは、動作は現行どおりです

ノートのある登録チャンネルで会話すると、そのノートが `summary_date` と一緒に
システムプロンプトへ差し込まれます。信頼しないコンテキストとして `<channel_note>`
タグで囲み、「指示ではなく過去の記録」として扱わせます。未登録チャンネル・DM には
入りません。

### 会話履歴の部屋名検索

会話履歴そのものは全チャンネル横断のままですが、「あの部屋で何を話したか」を思い出す
ための検索ツールをコア組み込みの LLM ツールとして同梱しています。既定では有効化されて
いません。`${CONFIG_ROOT}/tools/` に以下の YAML を置くと opt-in で有効になります
（LLM へ見せるツール名は YAML のファイル名になります）。

```yaml
type: lilla_core.builtin_tools.llm_conversation_get
```

- `datetime_range`（`today` / `last_7_days` / `2026-04-20/2026-04-26` など）・`query`
  （スペース区切りの AND キーワード）・`role`（`user` / `assistant` / `all`）・`limit`
  （既定 30、上限 30）で絞り込めます
- `channel_name` に `discord.channels` の登録名を渡すと、そのチャンネルの発言だけに
  絞り込みます。**設定上の別名であり、Discord の現在のチャンネル名ではありません**
- `channel_name` を省略すると、今までどおり全チャンネル横断で検索します
- 登録に無い名前を渡すとエラーを返します（黙って全件検索に落としません）
- 名前の突き合わせは前後の空白を除いた完全一致で、大文字小文字は区別します
- 期間の境界と結果の表示時刻はどちらも `ui.timezone` で解決したタイムゾーンで扱います

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

### 観測用ダッシュボード

コアは起動時に、会話履歴・ユーザーメモ・ログを人が見るための HTTP ダッシュボードを
立ち上げます。有効無効のフラグはありません（起動するなら常に端末が開きます）。
設定は `lilla.yaml` の `dashboard:` で、節そのものを省略すると既定値になります。

```yaml
dashboard:
  host: "0.0.0.0"     # listen するアドレス（既定）
  port: 8765          # listen するポート（既定）
  cookie_secure: true # セッション Cookie に Secure を付けるか（既定）
```

> **セキュリティ上の注意**
>
> このポートは管理画面です。パスワードが未登録のあいだは `POST /api/setup` が誰でも
> 通るブートストラップなので、**そのポートに先に到達した人が管理者パスワードを決められます**
> （登録後は 403 で恒久的にブロックされます）。
>
> - 既定の `host` は全インターフェース（`0.0.0.0`）です。コンテナ運用を前提にしているため
>   で、**このポートを公開ネットワークへ直接晒さないでください**。前段にアクセス制御
>   （リバースプロキシや Zero Trust など）を置くか、同一ホストからしか使わないなら
>   `host: 127.0.0.1` に絞ってください
> - **起動したら最初に初期パスワードを設定してください**（`/` を開くと初期設定画面が出ます）
> - `cookie_secure: true` は HTTPS 経由を前提にした安全側の既定です。LAN 内で
>   `http://<host>:8765` へ直接アクセスする運用では `false` にしないと、ログインはできても
>   Cookie が送信されずログイン状態を維持できません

`/oauth/{拡張名}` 配下だけは認証ミドルウェアの外に出ます（OAuth の戻り先など、ブラウザが
セッションを持たない状態で叩く公開 GET のため）。前段でアクセス制御を掛ける場合は、この
接頭辞だけをバイパスする構成になります。

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
├── tool_support/         # ツール実装向けの opt-in ヘルパー群
└── testing/              # 拡張リポジトリ向けのテストヘルパー（opt-in）
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
| `tool_config_roots` | 拡張が同梱する既定のツール YAML（`llm_*.yaml` / `task_*.yaml`）のディレクトリ。`${CONFIG_ROOT}/tools` に同じ stem の YAML があればそちらが丸ごと勝つ。同梱ツールを止めるにはそこに `enabled: false` の YAML を置く |
| `locale_dirs` | 拡張が同梱する UI 文言カタログ（`{locale}.yaml`）のディレクトリ。カタログのトップレベルキーはその拡張の `name` ただ 1 つでなければならない |
| `command_packages` | `@register_command` を探す追加パッケージ |
| `dashboard_page` | 観測用ダッシュボードへ足すタブ 1 つ（`DashboardPage(label, group)`）。`group` は `main`（常用ナビ）か `admin`（管理メニュー）。経路は申告せず `name` から導出する |
| `dashboard_static_dir` | `/static/ext/{name}/` に載せる静的ファイルのディレクトリ。タブを出すなら直下に `page.js` を置く |
| `dashboard_routes` | セッション認証の内側に足す HTTP ルート（`DashboardRoute`）。パスは `/api/{name}` 配下のみ |
| `dashboard_public_routes` | 認証の外側に載せる公開ルート（OAuth の戻り先など）。パスは `/oauth/{name}` 配下のみ。`state` の検証は拡張側の責任 |
| `config_model` | この拡張が足す YAML セクションのモデル 1 つ（足さないなら `None`）。置き場は `extensions.<name のハイフンをアンダースコアにしたもの>`（`google-oauth` なら `cfg.extensions.google_oauth`） |
| `env_fields` | この拡張が `cfg.env` に足す秘匿フィールド |
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

拡張は `locale_dirs()` で Discord に見せる文言のカタログも同梱できます。各ディレクトリには
コアと同じ命名の `{locale}.yaml`（`ja.yaml` / `en.yaml` など）を置き、カタログの
**トップレベルのキーはその拡張の `name` ただ 1 つ** でなければなりません。`name = "lilla-habits"`
なら YAML は `lilla-habits:` の 1 ノードだけを持ち、呼び出しは `t("lilla-habits.notify.title")`
になります。コアが自動で prefix を付けることはせず、YAML 上のキーと `t()` に書くキーは
同じ文字列です。コアがコア自身のカタログへ拡張のカタログをロード順に重ねるため、
解決順は従来どおり「`ui.locale` → `ja` → キー名そのもの」で、`ja.yaml` しか同梱して
いない拡張でも `ui.locale: en` で例外になりません。1 つの拡張が複数のディレクトリを
返した場合は、その拡張のノードをロード順に浅くマージします（同じキーは後のディレクトリが
勝ちます）。トップレベルキーが拡張名と異なるカタログや、拡張名がコアのトップレベルキー
（`selftest` など）と衝突している場合は、拡張の登録時に `ValueError` で fail-fast します
（`t()` 自体は従来どおり例外を投げません）。存在しないディレクトリは WARNING を出して
（ロケールごとに 1 回）読み飛ばし、壊れた YAML は ERROR ログを出してそのロケール分だけ
空として扱います。

`run_conversation()` を自分で呼ぶクライアント拡張は、任意のオブジェクトを
`client_state` として渡せます（接続中ソケットの集合など）。コアは中身を解釈せず、
フックには `ConversationContext.client_state` として、LLM ツールには context の
`client_state` キーとしてそのまま渡します。

### ダッシュボードへの差し込み

拡張は観測用ダッシュボードにタブを 1 つと、HTTP ルートを足せます。経路の識別子は
`Extension.name` ただ 1 つで、ハッシュ・API 接頭辞・公開コールバック・静的 URL は
すべてコアが `name` から導出します（新しい ID 欄はありません）。`name = "google-oauth"`
のとき次のようになります。

| 用途 | 値 |
|------|-----|
| 常用ハッシュ | `#/google-oauth` |
| 管理ハッシュ | `#/admin/google-oauth` |
| セッション API | `/api/google-oauth` |
| 公開コールバック | `/oauth/google-oauth/callback` |
| 静的ファイル | `/static/ext/google-oauth/` |
| JS モジュール | `/static/ext/google-oauth/page.js` |

```python
class MyExtension(Extension):
    name = "my-pack"

    def dashboard_page(self) -> DashboardPage | None:
        return DashboardPage(label="My Pack", group="main")

    def dashboard_static_dir(self) -> Path | None:
        return Path(__file__).parent / "dashboard"

    def dashboard_routes(self) -> list[DashboardRoute]:
        return [DashboardRoute("GET", "/api/my-pack/items", handle_items)]
```

コアが集めた結果は `get_dashboard_pages()`（導出済みの `DashboardPageEntry`）・
`get_dashboard_static_mounts()`・`get_dashboard_routes()`・`get_dashboard_public_routes()`
から、いずれもロード順で読めます。これらを実際に載せるのはコアのダッシュボード
サーバー（`handlers/dashboard_server.py`）で、`bot.py` の `main()` が拡張の `setup()` の
あとに起こします。

`name` は URL にそのまま埋まるため、`^[a-z0-9][a-z0-9-]*$` に合わない名前と、
コアが押さえている予約名（`api` / `oauth` / `static` / `admin` / `dashboard` /
`auth` / `login` / `logout` / `setup` / `home` / `conversations` / `memos` / `logs`）は
ロード時に fail-fast します。ルートのパスが自分の接頭辞の外にある場合、タブを出すのに
`dashboard_static_dir()` を返していない場合も同様です。

`dashboard_public_routes()` に載せたルートは **誰でも叩けます**。ホスト前段の
アクセス制御で公開コールバックだけを通す構成でも、`state` の検証は拡張側の責任です。
認証が要る処理は `dashboard_routes()` へ置いてください。

### 設定の合成

拡張は自分が足す設定を申告し、コアが起動時にすべての申告から 1 つの Pydantic
モデルを組みます。`AppConfig` のサブクラスを手書きして、モジュールの import 副作用
として `set_config()` で差し替える方式はもう契約に含みません。合成したインスタンスが
拡張の差し込みを上書きするため、`LILLA_EXTENSIONS` の並び順は設定に影響しません。

```python
class GoogleConfig(BaseModel):
    client_id: str | None = None
    redirect_uri: str = "http://localhost/google-callback"


class GoogleOAuthExtension(Extension):
    name = "google-oauth"

    def config_model(self):
        return GoogleConfig

    def env_fields(self):
        return {"google_client_secret": "GOOGLE_CLIENT_SECRET"}
```

1 つの拡張が足せるセクションのモデルは **1 つだけ** で、節名は自分では書きません。
キーは `name` のハイフンをアンダースコアに置き換えたものです（`google-oauth` →
`google_oauth`。ハイフンの無い名前はそのまま）。複数の設定のまとまりを持ちたいときは、
その 1 つのモデルの子として並べてください。拡張のセクションは `lilla.yaml` の
トップレベルではなく、コア確定の `extensions:` の下に置きます。コアが後からトップレベルに
節を足しても、拡張の名前と衝突しません。

```yaml
dashboard:
  port: 8765
extensions:
  google_oauth:
    client_id: ...
```

これで `get_config().extensions.google_oauth.client_id` と
`get_config().env.google_client_secret` がプロセス全体から読めるようになります
（トップレベルの `get_config().google_oauth` は作りません）。
`get_config()` の型は基底の `AppConfig` なので、
型検査や補完のためにセクションをそのモデルの型で受け取りたいときは `get_section()` を
使ってください。

```python
from lilla_core.core.config import get_section

client_id = get_section("google-oauth", GoogleConfig).client_id
```

引数には拡張の `name` を渡します（節名をそのまま渡しても構いません）。探すのは
`extensions:` の下だけです（コア確定の節は `get_config().ui` のように直接
読みます）。セクションが申告されていない場合や、値が渡したモデルのインスタンスでない
場合は `ValueError` になります。名前の綴りを間違えても静かに空を返すことはありません。

- セクションは、モデルが必須フィールドを 1 つでも持てば **必須**、そうでなければ
  省略可能になります。必須セクションが `lilla.yaml` に無ければ起動時に落ちます
- `extensions:` の下に未知のキーがあれば起動時に落ちます。外した拡張の節が払い残しの
  まま気付かれずに残ることを防ぐためです。拡張が 0 個なら `extensions` は空です
- 申告した節を `extensions:` ではなくトップレベルに書いた場合も起動時に落ちます
  （そのままだと無視され、モデルの既定値のまま気付かずに動いてしまうため）。
  コア確定の節と同じ名前のトップレベルキーはコアのものなので対象外です
- 合成される env フィールドの型は常に `str | None`（既定値 `None`）です。契約が型を
  運ばないため、必須フィールドや文字列以外の秘匿情報はこの経路では表現できません
- `config_model()` は pydantic の `BaseModel` のサブクラスか `None` を返します。それ以外は
  ロード時に失敗します。拡張名は一意でアンダースコアを含まないため、2 つの拡張が同じ
  キーを導くことはありません。数字で始まる名前の拡張は、キーが識別子にならないため
  モデルを申告できません。キーは名前空間が別なので、コア確定の節と同じでも構いません
- 同じ env フィールド名を 2 つの拡張が提供したら fail-fast します。コア確定の env
  フィールド名は予約済みです
- 他の拡張のセクションを読むときは、その拡張に `requires`（下記）で依存し、
  `cfg.extensions.<相手のキー>` を読みます。そのための別の申告はありません。
  `required_env_fields()` と `required_tool_context_keys()` は引き続き、自分では提供しないが
  読む `cfg.env` のフィールドとツール context のキーを並べます。誰も提供していなければ
  ロードに失敗し、要求した拡張の名前を示します（コア確定の env フィールドとツール
  context のキーは、書いても常に満たされます）

### 拡張どうしの依存

依存する拡張は、クラス属性 `requires` に名前を並べて宣言します。コアはロード時に、
それらが **ロード済みで、かつ `LILLA_EXTENSIONS` で自分より前に並んでいる** ことを
検証します。メッセージフック・`setup()`・ツールルートはロード順に処理されるため、
コアが並べ替えることはありません。

```python
class GoogleCalendarExtension(Extension):
    name = "lilla-google-calendar"
    requires = ("lilla-google-oauth",)  # extensions.lilla_google_oauth を読む依存もこれで表す

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
    api_version = 2  # 省略すると EXTENSION_API_VERSION
```

宣言したバージョンを受け付けない場合、`load_extensions()` は拡張名と両方のバージョンを
示して失敗します。古い契約で書かれた拡張をそのまま読み込んで、起動後に壊れるのを
防ぐためです。現在のバージョンは 2 で、`config_models()` と `required_config_sections()` を
`config_model()` に置き換えました。廃止したメソッドのどちらかを定義したままの拡張は、
`api_version` を宣言していなくても、代わりの経路を示してロード時に失敗します。

契約を変えるときの方針:

- **非破壊（バージョンは上げない）**: `Extension` へのメソッド追加（必ず「何も貢献しない」
  既定を持たせる）、`*Context` データクラス（`SetupContext` / `ConversationContext`）への
  フィールド追加、参照関数やコア確定の context キーの追加
- **破壊的（`EXTENSION_API_VERSION` を上げる）**: メソッドのシグネチャや戻り値の形の変更、
  メソッド・`*Context` のフィールド・context キー・参照関数の削除や改名、フックが呼ばれる
  タイミングの変更。こうした変更は `CHANGELOG.md` に **BREAKING** として記録します
- 拡張パッケージは `lilla-core` をバージョン範囲で依存指定してください
  （例: `lilla-core>=0.3,<0.4`）。破壊的なコアのリリースを気付かず取り込まないためです

### 拡張のテストの書き方

`lilla_core.testing` は、拡張リポジトリがそれぞれ書くことになる「自分の拡張を登録し、
設定を合成し、テストが終わったらプロセスの状態を元へ戻す」というセットアップを
肩代わりします。本番依存だけで動き、`pytest` を import するのは
`lilla_core.testing.pytest_plugin` だけです。

このプラグインは `pytest11` entry point による自動登録を **しません**（利用側の既存
conftest と黙って干渉しないためです）。ルートの `conftest.py` で opt-in してください。

```python
# conftest.py
pytest_plugins = ["lilla_core.testing.pytest_plugin"]
```

```python
# test_my_extension.py
from my_package import extension


def test_config_section_is_composed(lilla_extensions):
    cfg = lilla_extensions(extension)

    assert cfg.extensions.my_section.value == "default"
```

fixture は 2 つ（どちらも function scope）です。

- `lilla_config_root`: `tmp_path` に最小構成の `lilla.yaml` を書き、`CONFIG_ROOT` を
  そこへ向けます。`DISCORD_TOKEN` が環境に無ければダミー値も入れます。戻り値は
  そのディレクトリです
- `lilla_extensions`: `register(*extensions) -> AppConfig` を返します。拡張を登録し、
  `lilla_config_root` を `CONFIG_ROOT` として設定を合成し、`set_config()` まで行って
  合成結果を返します（以降 `get_config()` が合成モデルを返します）。teardown で
  すべて元へ戻り、`register` を複数回呼んだ場合は後入れ先出しで戻します

pytest が無い環境や、もっと細かく制御したい場合はヘルパーを直接使えます。

```python
from lilla_core.testing import use_extensions, write_minimal_lilla_yaml


def test_section(tmp_path):
    write_minimal_lilla_yaml(
        tmp_path, extra={"extensions": {"habits": {"channel": "habits-test"}}}
    )

    with use_extensions(extension, config_root=tmp_path) as cfg:
        assert cfg.extensions.habits.channel == "habits-test"
```

`use_extensions()` は入るときに現在の登録・設定インスタンス・`CONFIG_ROOT` を退避し、
抜けるときに（本体で例外が出ても）3 つとも元へ戻します。
`write_minimal_lilla_yaml(directory, *, my_user_id=..., llm_name=..., extra=...)` は
コアが必須にしている項目（`discord.my_user_id` と、`llm.default` に対応する
`llm.providers.<名前>`。あわせて日付が OS のタイムゾーンに左右されないよう
`ui.timezone: Asia/Tokyo`）を書き、`extra` を同じネストで深いマージして重ねます
（拡張が必須にしているセクション用）。戻り値は書き出したパスです。

## ツール契約

ツールは `${CONFIG_ROOT}/tools/` の YAML 設定ファイルをもとに、`${TOOL_ROOT}/**/*.py`
から動的にロードされます。各 YAML のファイル名（stem）がツール名になり、`type`
フィールドから対応する `.py` ファイルを探します（詳細は
`loaders/llm_tool_loader.py` / `loaders/task_tool_loader.py` の実装を参照）。

拡張は `tool_config_roots()` で既定の YAML を同梱できます。ローダーはまずそれらの
ディレクトリ（拡張のロード順）から、次に `${CONFIG_ROOT}/tools` から YAML を集め、
同じ stem のファイルが `${CONFIG_ROOT}/tools` にあれば同梱分を丸ごと置き換えます
（内容のマージはしません）。利用者の設定が常に勝つ形です。2 つの拡張が同じ stem を
同梱していると起動時に失敗します。同梱ツールを止めるには、`${CONFIG_ROOT}/tools` に
同じ stem で `enabled: false` と書いた YAML を置いてください。`enabled: false` は
同梱かどうかに関わらず、どのツール YAML でも無効化に使えます。

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

`lilla_core` は import パス形式の具体例として、組み込みの LLM ツールを 2 つ
同梱しています: `lilla_core/builtin_tools/llm_current_datetime.py`（サンプル）と
`lilla_core/builtin_tools/llm_conversation_get.py`（[会話履歴の部屋名検索](#会話履歴の部屋名検索)）。
どちらも既定では有効化されていません。`${CONFIG_ROOT}/tools/` に以下の YAML を置くと
opt-in で有効化できます:

```yaml
type: lilla_core.builtin_tools.llm_current_datetime
```

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
  トリガー種別は `type` ではなく YAML のファイル名から判定するため、import パス指定の
  task ツールも `task` ツールのままで、`!runtask` から実行できます。

`lilla_core` は task ツールも 1 つ同梱しています:
`lilla_core/builtin_tools/task_channel_summary.py`（[部屋のノート](#部屋のノート深夜要約)
で説明した深夜要約バッチ）。LLM ツールのサンプルと同じく opt-in です。

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
