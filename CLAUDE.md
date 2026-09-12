# プロジェクト概要
Lilla のコアランタイム（`lilla-core`）。
AI エージェントを構築するための汎用基盤ライブラリ。Discord ボットとして常駐し、
メッセージを受け取って LLM（Ollama / OpenAI 互換）を呼び出し、tool_call ループ・
コマンド処理・返信を行う「エージェントとしての骨格」を提供する。

キャラクター設定・特定ドメイン専用のツール（外部サービス連携など）・特定用途の
HTTP/ダッシュボードサーバーといった、利用者ごとに異なる要素はコアには含めない。
そうした要素は `core/extension.py` の `Extension` サブクラスと、起動時に読み込む
`LILLA_EXTENSIONS` 環境変数を通じて外部から拡張できるようにする（詳細は
「拡張（`core/extension.py`）まとめ」節を参照）。

lilla-core は拡張が一切登録されていない状態でも Discord bot として単体で起動できる
ことを設計上の前提にしている。`Extension` の各メソッドは「何も貢献しない」
デフォルトを持ち、コアが拡張の有無に依存しないようにする。

# 開発ルール
- セキュリティ懸念事項があれば遠慮なく伝える（特にプロンプトインジェクションの危険がある場合など）
- コアの汎用範囲を超える要素（キャラクター設定・特定ドメイン専用ツール・特定用途の
  HTTP/ダッシュボードサーバーなど）はこのリポジトリに持ち込まない。コアがそうした
  拡張側の情報を必要とする場合は、直接参照せず `core/extension.py` の `Extension`
  にメソッドを追加し、拡張する側でオーバーライドしてもらう形にする
- 関数には docstring を日本語で書く
- `logger.*()` / `raise` に渡すメッセージ文字列は英語で書く（docstring・コメントは日本語のまま）
- Discord に見える文言（コマンドの返信・ボタンのラベル・`notify_error` に渡す文脈など）は
  Python コードに直書きせず、`lilla_core/locales/{locale}.yaml` のカタログへ置き
  `lilla_core.ui.messages.t()` 経由で参照する。文言を足すときは同じ変更で `ja.yaml` と
  `en.yaml` の両方に入れること（対象外: logger / raise のメッセージ・コメント・docstring・
  システムプロンプト組み立て用の見出し・ツール SCHEMA の description）
- `README.md`（英語）と `README.ja.md`（日本語）は内容を対にして保つ。どちらかを更新
  するときは同じ変更をもう一方にも反映すること

## 依存関係の管理ルール
- 本番依存: `pyproject.toml` の `[project.dependencies]`
- 開発・テスト依存: `pyproject.toml` の `[project.optional-dependencies.dev]`
- 新しい依存を追加するときは `pyproject.toml` を更新し、`pip install -e ".[dev]"` で反映する

## 設定管理ルール
- lilla-core 自体は `.env` / `lilla.yaml` の実体を持たない。`core/config.py` の `AppConfig`
  （pydantic-settings）が読み込みスキーマを定義するだけで、実ファイルは lilla-core を
  利用するアプリケーション側が `CONFIG_ROOT` 環境変数の指すディレクトリに用意する
- `lilla.yaml` の探索先（`CONFIG_ROOT`）は OS 環境変数と `.env` のどちらから指定してもよい
  （OS 環境変数が優先。どちらも無ければ既定 `/app/config`）。`EnvConfigSettingsSource` が
  解決した `env.config_root` を `settings_customise_sources()` がそのまま YAML パスの決定に
  使うため、`.env` にだけ `CONFIG_ROOT` を書いた場合でも同じディレクトリの `lilla.yaml` が
  読まれる
- 秘匿情報（token, secret, key, URI）は `.env` / OS 環境変数から読ませるフィールドとして
  `EnvConfig`（`cfg.env`）に定義し、`EnvConfigSettingsSource._VAR_NAMES` に OS 変数名を追加する
- 非秘匿の構造設定（URL, パス, モデル名など）は `lilla.yaml` と同じネスト構造のセクション
  モデル（`DiscordConfig` / `PathsConfig` / `LlmConfig` など）に定義する。YAML のキー名と
  フィールド名は必ず一致させる（フラット名への変換層は持たない）
- コア確定の設定項目を追加・変更したときは `core/config.py` の `AppConfig` を更新すること。
  `.env.example` / `config.example/lilla.yaml` の実体は利用側アプリケーションにあるため、
  そちらの更新が必要なことを合わせて伝える
- Discord に見せる文言のロケールは `ui.locale`（既定 `ja`）で切り替える。カタログの無い
  ロケール名を書いても起動は落とさず、`ja` へフォールバックする
- コアの汎用範囲を超える固有の設定フィールド（特定の外部サービス連携など）は `AppConfig` に
  追加しない。拡張する側で `AppConfig` のサブクラスを定義して `set_config()` で差し替え、
  YAML 由来のフィールドは `settings_customise_sources()` を override して独自の設定ソースを
  追加する

## HTTPアクセスのルール
- HTTPリクエストは必ずプロキシ経由で行う（プロキシ設定は `AppConfig` から自動適用される）
- `core/http_util.py` の `send_http_request` が使える場合は必ずそれを使う
  - JSON ボディ送信: `data=` パラメータ
  - フォームエンコード送信: `form_data=` パラメータ
- `aiohttp` を直接使って独自にセッションを張ることは禁止（バイト列が必要な添付ダウンロードなど、
  `send_http_request` の戻り値（str）では扱えない場合のみ例外。`services/attachment_download.py`
  がその唯一の例外実装で、他から参照して共有すること）

# テスト
- コードを変更したら、タスク終了前に必ず以下を実行して確認すること
- `pytest tests/`（このリポジトリ直下。`pyproject.toml` の `pythonpath = ["src"]` により
  `lilla_core` を解決する）
- テストが失敗した場合は修正してから完了とすること
- `src/lilla_core/api`, `src/lilla_core/repository` 配下のソースはテストクラスを作らないこと。
  それ以外のソースはテストクラスを作ること

## テストのモック方針
モジュールレベルの `sys.modules[...] = ...` や `sys.modules.setdefault(...)` による依存差し替えは禁止する。
テスト収集順によって本物のモジュールが呼ばれ、タイムアウトや他テストへの汚染が起きるため。

代わりに `@pytest.fixture`（原則 function scope）と `unittest.mock.patch.dict(sys.modules, {...})` を使う。
対象モジュールの import は `patch.dict` が有効になったあと（`yield` の前後）で行う。

```python
@pytest.fixture
def mock_llm_client():
    mock = MagicMock()
    mock.chat_to_llm_with_tools = AsyncMock()
    return mock

@pytest.fixture
def with_mocked_modules(mock_llm_client):
    with patch.dict(sys.modules, {
        "lilla_core.api.llm_client": mock_llm_client,
    }):
        yield

async def test_xxx(with_mocked_modules, mock_llm_client):
    from lilla_core.some.module import target  # patch.dict 有効後に import
    ...
```

テスト関数内での一時的な `monkeypatch.setitem(sys.modules, ...)` や `patch.dict` は許可する。
`aiohttp` を `MagicMock` で差し替えると `@web.middleware` が壊れるため、本物が必要な場合は差し替えない。

`tests/conftest.py` はこの方針を補強するため、各テストモジュールの import 直前に
`api` / `commands` / `handlers` / `repository` / `services` / `loaders` / `lilla_core` および
`discord` / `motor` / `pymongo` / `bson` に対する `MagicMock` の残留を `sys.modules` から
自動的に取り除くカスタム pytest コレクタ（`_CleaningModule`）を登録している。上記ルールを
守っていても取りこぼした汚染がある場合の保険であり、これに頼ってルール自体を省略しないこと。

## ブランチ戦略
- main / developブランチへの直接pushは禁止（必ずPR経由でマージすること）
- mainには触れない
- デフォルトはdevelopブランチから作業ブランチを作り、developへのPRを作成すること
- featureブランチを使う場合は都度指示する
- featureブランチ名: `feature/#{issue番号}-{概要}`

## Git commit messages

Use Conventional Commits, in English.

```
type: short summary
```

Allowed types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`.
Add a scope when it helps (`feat(config): ...`).

- subject in imperative mood (`add`, `remove`, `fix`)
- no trailing period
- one logical change per commit
- do not rewrite published history

Examples:

- `feat(config): add ui.locale`
- `fix(selftest): handle LLM timeout`
- `refactor: remove yaml flatten from core`
- `docs: describe locale fallback`

### 関連 Issue のクローズ（PR マージ時）

対応する Issue がある変更では、本文の末尾（`Co-Authored-By` などの行より前）に
独立した行でクローズキーワードを書き、PR マージ時に GitHub 側で Issue を自動クローズ
させる。キーワード自体の効果はどれも同じ（`close(s)` / `fix(es)` / `resolve(s)`）だが、
読み手にとっての意味合いを揃えるため type に対応させて選ぶ。

| type | キーワード |
|------|-----------|
| `fix` | `Fixes #123` |
| それ以外（`feat` / `refactor` / `test` / `docs` / `chore`） | `Closes #123` |

複数の Issue にまたがる場合は行を分けて書く（`Fixes #123` と `Closes #456` など）。
対応する Issue が無いコミットには付けない。

例:

```
fix(selftest): handle LLM timeout

Fixes #123
```

## Pull request descriptions

Write the PR title and body in English. Keep it short.

```
## Summary
- what changed, in one or two bullets

## Test plan
- [x] pytest tests/
```

# コード構成

## エントリポイント
lilla-core 自体は起動スクリプトを持たない（ライブラリとして `pip install -e` される前提）。
`src/lilla_core/bot.py` が Discord bot のエントリポイントで、ホスト側の起動スクリプトが
`LILLA_EXTENSIONS` 環境変数を設定したうえで `python -m lilla_core.bot`
相当の呼び出しを行う想定（`if __name__ == "__main__"` を持つ）。拡張を一切登録しなくても
そのまま起動できる。

## src/lilla_core/ — メインソースコード

### ルート直下
| ファイル | 役割 |
|----------|------|
| `bot.py` | Discord ボット本体。設定・コマンド・ツール・コア確定リポジトリの初期化と、Discord イベントの各 handler への委譲を統括する。起動直後（他の import より前）に `load_extensions()` で環境変数 `LILLA_EXTENSIONS`（カンマ区切り）が指すモジュールを import し、各モジュールの `extension` を集めて検証する（未設定ならコア単体で起動する）。`on_ready` / `on_message` / `on_interaction` は `handlers/` の各ハンドラへ委譲するだけの薄いラッパー |
| `bot_client.py` | Discord `commands.Bot` インスタンスの生成のみを担う共有モジュール。`bot.py` をスクリプト実行した際の多重ロード（Discord 未接続の幽霊インスタンス生成）を防ぐため、`bot` インスタンスを参照する側は必ずこのモジュールから import する |
| `log_handler.py` | MongoDB へのログ書き込みハンドラー（`MongoDBHandler`。レベル別 TTL 付き） |

### コア基盤 (`src/lilla_core/core/`)
| ファイル | 役割 |
|----------|------|
| `config.py` | Pydantic ベースの設定管理（`AppConfig`）。`${CONFIG_ROOT}/lilla.yaml` はネスト構造のまま同じ形のセクションモデル（`cfg.discord.my_user_id` など）へ読み込み、`.env` / OS 環境変数は `EnvConfig`（`cfg.env.discord_token` など）へ読み込む（YAML の項目を環境変数で上書きする経路は持たない。YAML トップレベルの `env:` は警告して無視する）。複数 LLM プロバイダの動的選択に対応。`ui.locale`（`UiConfig`）は Discord に見せる文言のロケールを決める。コアの汎用範囲を超えるフィールドは持たず、拡張側は `AppConfig` のサブクラスで `settings_customise_sources()` を override して独自ソースを足す。`get_config()` / `set_config()` でプロセス全体の設定インスタンスを共有し、`set_config()` により拡張側で定義したサブクラスへ差し替え可能 |
| `extension.py` | コアの外から機能を差し込むための `Extension` 基底クラスと、そのロード・参照 API。`Extension` は Adapter 型で、起動時リポジトリ・メッセージフック・起動処理（`setup`）・結果配送・クライアント固有プロンプト・会話開始フック・ツール実行 context プロバイダ・追加ツールルート・追加コマンドパッケージの各メソッドに「何も貢献しない」デフォルトを持つ。`load_extensions()` が `LILLA_EXTENSIONS` のモジュールを import して各 `extension` を集め、`set_extensions()` が貢献キーの衝突を検証して登録する（拡張どうしの重複は fail-fast）。`lilla_core/bot.py` が拡張モジュールを直接 import しないための唯一の橋渡し層 |
| `exceptions.py` | `ReauthenticationRequiredError`（外部 API 再認証要求時）・`LLMError`（LLM 呼び出し失敗時）の例外定義 |
| `error_notify.py` | コマンド実行系・定期タスク実行系のエラー出力を一元化する（`notify_error`）。ERROR ログと Discord のエラー通知チャンネル（`discord.error_channel`）の 2 箇所にのみ出力し、元チャンネルへの `message.reply()` は行わない（bot 間チャンネルで相手 bot が reply に反応するのを防ぐため）。チャンネル未設定・未発見・送信失敗時は WARNING ログのみで、例外は投げない |
| `http_util.py` | 全 HTTP リクエストの共通ユーティリティ（`send_http_request` / `stream_http_request`）。プロキシ自動適用、リクエスト/レスポンスの秘匿情報（`client_secret` 等）・base64 画像のログマスキングつき |
| `logging_setup.py` | `${CONFIG_ROOT}/logging.yaml` からのログ初期化。YAML が無ければ `basicConfig` にフォールバックする |
| `runtime_state.py` | 実行時に一時的に上書きされるグローバル状態をプロセス内メモリで保持する（`!model` で切り替える LLM プロバイダー名と、`!disable_tools` / `!enable_tools` で切り替える通常会話のツール無効化フラグ。いずれも永続化なしで、再起動するとデフォルトに戻る） |

### Discord コマンド (`src/lilla_core/commands/`)
`!コマンド名 引数` 形式の Discord コマンドを 1 コマンド 1 ファイルで実装する。各ファイルは
`@register_command("コマンド名")` でハンドラを登録し、`load_all_commands()`（`bot.py` の起動時に一度だけ呼ぶ）が
`pkgutil.iter_modules` でディレクトリ内の全モジュールを動的に import する。**コマンドを追加するときは
`src/lilla_core/commands/` にファイルを置くだけでよく、どこかにコマンド名を書き足す必要はない**。
ハンドラのシグネチャは `(message, arg, tools, bot)` で、`arg` はコマンド名の後ろに続く引数文字列（無い場合は空文字列）。
コアの汎用範囲を超える固有のコマンド（特定ドメイン専用のものなど）はここに置かず、拡張側で実装する想定。

| ファイル | 役割 |
|----------|------|
| `registry.py` | コマンド名 → ハンドラのレジストリ。登録デコレータ（`register_command`）、既知コマンド名の一覧（`known_command_names`）、ハンドラ取得（`get_command_handler`）を提供する。既知コマンド名を持つ唯一の場所。同じ名前を別のハンドラで登録しようとすると fail-fast する（同一性は `(__module__, __qualname__)` で見るため、モジュールの再 exec による同じ関数の再登録は許容する） |
| `__init__.py` | `src/lilla_core/commands/` 配下の全モジュールと、拡張の `command_packages()` が返すパッケージを動的に import する `load_all_commands` |
| `discord_util.py` | コマンド共通の Discord ユーティリティ。チャンネル ID の解決（キャッシュ → API 問い合わせの順、`resolve_discord_channel`） |
| `attachment_body.py` | コマンドの BODY をテキストと添付ファイルの両方から解決する共通処理（`resolve_command_body`）。添付があれば優先（複数なら先頭 1 件のみ）、無ければテキスト側を使い、どちらも無ければ `notify_error` で通知して None を返す。テキスト判定は拡張子（`.json` / `.txt`）または Content-Type（`text/*` / `application/json`）のどちらか一致、上限 1MB、文字コードは UTF-8（デコード失敗時は通知して中断）。ダウンロードは `services/attachment_download.py` 経由でプロキシ設定を尊重する |
| `runtask.py` | `!runtask <ツール名>` — `trigger="task"` のツールを手動実行する。`run_task` は関数として切り出してあり、拡張側で HTTP 経由の手動実行エンドポイント等を用意する場合にもそのまま呼び出せる |
| `mongodata.py` | `!mongodata <JSON>` — ホワイトリストで許可されたコレクションへ JSON を insert / upsert する。JSON は本文にも添付ファイルにも書ける（`attachment_body.resolve_command_body` 経由。添付優先） |
| `toolresult.py` | `!toolresult <correlation_id>`（2 行目以降が結果本文。結果本文は添付ファイルでも渡せる＝`attachment_body.resolve_command_body` 経由で添付優先。correlation_id は常にメッセージ本文側）— 外部エージェントからの非同期依頼の結果を、`pending_tool_calls` の原子的な status 更新を経て依頼元クライアントへ届け、あわせて会話履歴にも登録する。結果本文はそのまま転送せず、ツールを渡さない `chat_to_llm` でリラ自身の返信を生成してから配送する（プロンプトインジェクション対策として、本文は `<external_agent_response>` タグで囲んで「指示ではなく情報」として扱わせ、タグ抜け出し文字列は事前に無害化する）。配送先は依頼レコードの `client_type` で判定し、`"discord"` は自前で配送、それ以外は拡張の `result_deliveries()` に登録された配送関数（拡張側で任意のクライアント向け配送処理を登録可能）へ委譲する（未登録時は Discord 配送へフォールバック）。この返信は `tags: ["toolresult", "dirty"]` と配送先の Discord メッセージ情報つきで履歴に保存する |
| `cleardirty.py` | `!cleardirty` — 直近の `dirty` エントリを 1 件ずつ（会話履歴と Discord メッセージの両方から）取り消す |
| `model.py` | `!model [プロバイダー名]` — 会話で使う LLM プロバイダーを一時的に切り替える（引数なしで `llm.default` に戻す）。状態は `core/runtime_state.py` のインメモリ変数のみで、Discord 会話と（拡張が対応していれば）他クライアントの会話にだけ効く（`!runtask` と APScheduler 経由の定期タスクは各 YAML 設定のまま） |
| `disable_tools.py` | `!disable_tools` — 通常会話で LLM ツールを一時的にすべて無効化する。状態は `core/runtime_state.py` のインメモリフラグ（`set_tools_disabled`）のみで、`services/conversation_service.py` が `is_tools_disabled` を参照して tools を渡さない。再起動で有効へ戻り、定期タスクや `!runtask` には影響しない |
| `enable_tools.py` | `!enable_tools` — `!disable_tools` で無効化した通常会話のツールを再び有効へ戻す（`set_tools_disabled(False)`） |
| `selftest.py` | `!selftest [full]` — リラの基本機能が壊れていないかを人間が確認するための自己診断コマンド。プロセス応答・MongoDB 疎通・コマンドレジストリ件数・タスクツール件数を実行し、`full` を付けると LLM 疎通確認（`check_llm`。LLM API の課金が 1 往復分発生する）も追加する。チェック本体は `services/system_checks.py` に置き、生存確認と共有する。結果は要約（✅/❌ の箇条書き）を本文、詳細（`elapsed_ms` / `detail`）を添付ファイル（`selftest_result.txt`）にして常に呼び出し元チャンネルへ返す（診断が目的のため `notify_error` オンリーにはしない）。各チェック関数が内部で例外を捕捉するため 1 件の失敗が他のチェックを妨げない。**コンテナやプロセスに対しては何も作用しない**（結果を報告するだけ） |

### 外部トリガー入り口層 (`src/lilla_core/handlers/`)
コアが持つのは Discord のメッセージ/インタラクション/コマンドのディスパッチと定期タスクの実行のみ。
HTTP サーバー・ダッシュボードサーバー・WebSocket サーバーのような、対話クライアントを
増やす実装はこのリポジトリには含めず、`Extension.setup()` を通じて `main()` の
起動シーケンスへ差し込むことができる。

| ファイル | 役割 |
|----------|------|
| `approval_flow.py` | オーナー以外から届いたコマンドの承認フローを担う共通ロジック。`#lilla-approval` への承認依頼投稿（`send_approval_request`）、承認 / 拒否ボタン押下の処理（`handle_approve_interaction` / `handle_reject_interaction`）、承認対象コマンド文字列の組み立て（`extract_approvable_command` / `build_toolresult_command`）を提供する。`bot` / `tools` はモジュール変数ではなく引数で受け取る。**信頼境界は「承認依頼メッセージ（bot 自身の投稿）」に置く**: 承認依頼を作る時点で `resolve_full_command` が BODY（テキスト直書き／添付ファイルのどちらでも）を解決して「実行される内容の全文」を組み立て、300 文字以内なら承認依頼メッセージ本文の区切り行（`command_marker()`。文言はロケールごとに異なるため定数ではなく関数で持ち、復元時は他ロケールの区切り行も候補にする）以降にそのまま書き、超える場合はプレビュー＋bot が新規作成した `message.txt` に全文を添付する（元メッセージの添付は使い回さない）。BODY を解決できない場合は承認依頼を作成しない。承認ボタン押下時は `interaction.message` 以外を一切参照せず（元メッセージの `fetch_message()` は行わない）、復元した全文と、添付を空にした代理メッセージ（`ApprovedMessage`）を `command_handler.handle_command` へ渡す。`custom_id` の channel_id は実行内容には使わず、`message.reply` の返信先（`ApprovedMessage.channel`）の解決にのみ使う（解決できなければ承認チャンネルへフォールバック）。これにより承認待ちの間に元メッセージが編集されても実行内容は変わらない（TOCTOU 対策）。元メッセージへの `jump_url` は送信者・文脈の確認用リンクとしてのみ表示する。なお承認ボタンの二重押下による二重実行防止は行っていない（許容リスク） |
| `command_handler.py` | `!` プレフィックスの Discord コマンドのディスパッチのみを担う。コマンド文字列を最初の空白で 1 回だけ分割し、コマンド名の完全一致で `commands/` のレジストリを引いてハンドラへ委譲する（個別コマンドのロジックは持たない）。既知コマンドで始まる行以降を切り出す `extract_command_content` もレジストリのコマンド名一覧から判定する |
| `interaction_handler.py` | Discord のインタラクション（ボタン押下）イベントのディスパッチ。`custom_id` のプレフィックスで処理を振り分け、`approve:` / `reject:` は `handlers/approval_flow.py` へ、`command:{コマンド文字列}` は `handlers/command_handler.py` へ汎用的に委譲し、`action:{uuid}` 形式の保留中アクション（`button_actions`）のみ自身で取得・実行して結果を followup で返す。保留中アクションはツール名で特別扱いせず常に `execute_tool_call` を通すため、会話履歴には残らない。デフォルトではオーナー以外のインタラクションを拒否する（プレフィックス分岐より前で一括拒否。`message_handler.py` と同じ判定パターン）。コアは汎用ランタイムであり、より緩い権限モデルは利用側の拡張で差し替え可能という位置づけ。`bot` / `tools` / `llm_tools` は引数で受け取る |
| `message_handler.py` | Discord のメッセージ受信イベントのディスパッチ。メッセージフック（`extension.dispatch_on_message()`。拡張が 0 個なら常に `False`）→ 承認フロー振り分け → コマンド処理 → 通常会話、の順に処理する。通常会話は画像添付の変換（`services/image_attachment.py` へ委譲。サイズ超過・ダウンロード失敗で None が返ったら会話処理自体を行わない）・`run_conversation` の呼び出し・応答の分割送信と会話履歴保存を担い、送信中タスクをチャンネル単位で保持して後続メッセージ受信時に先行タスクをキャンセルする。`bot` / `tools` / `llm_tools` / `message_hook` は引数で受け取る |
| `request_params.py` | HTTP ハンドラー共通のリクエスト入力解析ユーティリティ。整数クエリパラメータのデフォルト値・範囲丸め付き取得（`parse_int_param`）、JSON ボディのパース（`parse_json_body`）、`ObjectId` へのパス変数変換（`parse_object_id`）を提供する。aiohttp のレスポンス生成自体は呼び出し側（拡張側の HTTP ハンドラーなど）に委ねる |
| `task_handler.py` | `trigger="task"` のツールの実行を管理する。APScheduler（`BackgroundScheduler`、タイムゾーン `Asia/Tokyo`）による定期ジョブ管理。ジョブは `asyncio.run_coroutine_threadsafe` で Discord の `bot.loop` に投げる |

### ビジネスロジック層 (`src/lilla_core/services/`)
特定ドメイン向けのサービス（外部サービス連携のデータ集計など）はここに置かず、拡張側で実装する想定。

| ファイル | 役割 |
|----------|------|
| `attachment_download.py` | Discord 添付ファイルのダウンロード共通処理（`download_attachment_bytes` / `resolve_proxy_settings` / `normalize_content_type`）。プロキシ設定を尊重して Discord CDN から取得する。画像添付（`services/image_attachment.py`）とコマンドの BODY 添付（`commands/attachment_body.py`）で共有する |
| `image_attachment.py` | Discord の画像添付を LLM へ渡す `image_url` パート（data URL）へ変換する処理。対応 MIME タイプの絞り込み（`filter_image_attachments`）と、サイズ上限ガード付きのダウンロード＋base64 化（`build_image_content_parts`）を担う。上限は `AppConfig.bot.max_image_attachment_size_mb`（既定 8MB。未設定・不正値・0 以下なら既定値）で、ダウンロード前に `attachment.size` で早期に弾き、Discord 側の申告値を過信しないようダウンロード後の実バイト数でも再検証する。上限超過・ダウンロード失敗はいずれも `notify_error` で通知して None を返し（例外は呼び出し元へ伝播させない）、呼び出し元は会話処理そのものを中止する |
| `conversation_service.py` | tool_call ループと会話履歴の読み書きを担う共通ロジック。Discord をはじめ、複数の対話クライアントのエントリポイントから再利用できる。LLM 最終応答の META ブロック（`actions`）を種別ごとにディスパッチして適用する（`set_session_memory` でセッションメモリを更新/クリア）。クライアント種別の判定は `"task"` かどうかだけで行い、それ以外の対話クライアント種別（`"discord"` や拡張が増やす種別）はコア側に列挙しない。会話開始フック（`extension.get_conversation_start_hook`）・ツール実行 context プロバイダ（`extension.get_tool_context_providers`）を経由して拡張の差し込みポイントを利用する |
| `memory_manager.py` | 会話履歴・ユーザーメモ・セッションメモリを統合し、LLM 向けシステムプロンプトを構築する（`build_system_prompt`）。クライアント種別ごとのプロンプト追記は `_resolve_client_prompt()` が「拡張の `client_prompt_providers()` → コア内蔵（`"discord"` のみ）→ 付けない」の順で解決する。ツールキャッシュ（`tool_cache_repository`）の有効なレコードも `## Cached Tool Results` としてシステムプロンプトへ埋め込む |
| `message_splitter.py` | LLM 応答を `---SPLIT---` / 改行2つ / タイムスタンプ境界で分割し、意味のない断片とタイムスタンプ prefix を除去するユーティリティ（`split_response`） |
| `message_util.py` | メッセージ送信ユーティリティ。フラグパース（`parse_message_flags`）・タイムスタンプ prefix の付与/除去（`prepend_timestamp_prefix` / `strip_timestamp_prefix`）・システムプロンプト埋め込み用セッションメモリブロックの整形（`format_session_memory_block`）・LLM 出力の META ブロック（JSON）の抽出（`extract_meta_block`）・外部エージェントとやりとりする FrontMatter 付きメッセージの組み立て/解釈（`build_correlation_frontmatter` / `parse_correlation_frontmatter`）・DM チャンネルの解決と Discord への送信（`resolve_dm_channel` / `send_to_discord`） |
| `session_memory_manager.py` | 単一領域のセッションメモリ（作業の途中状態や一時的な意図）をプロセス内メモリで保持する。TTL 付き、MongoDB 永続化なし。更新は LLM 出力の META アクション `set_session_memory` 経由で行う |
| `system_checks.py` | 生存確認（liveness。拡張側の HTTP ヘルスチェックエンドポイントなどから利用される想定）と自己診断（`!selftest`）が共有する個別チェック関数群（`CheckResult` / `check_process_alive` / `check_mongodb` / `check_command_registry` / `check_task_tools` / `check_llm`）。どの関数を組み合わせるかは呼び出し側が選ぶ。各関数は内部で例外を捕捉し、失敗時も例外を投げず `ok=False` の `CheckResult` を返す（1 件の失敗が他のチェックを妨げない）。`check_mongodb` は 3 秒、`check_llm` は LLM 往復のみ 30 秒の内部タイムアウトを持つ。`check_llm` は本番同様に `build_system_prompt` でプロンプトを組み立ててトークン数（tiktoken `cl100k_base` の概算。システムプロンプトのみが対象）を計測してから `chat_to_llm("ping")` を送る。プロバイダーは `runtime_state.get_active_llm_name()`（`None` なら `llm.default`）で解決した「今実際に使われているもの」を使う。detail にはシステムプロンプト本文を含めない（ユーザーメモ等の私的な内容が結果に残るのを避けるため） |

### UI 文言 (`src/lilla_core/ui/`・`src/lilla_core/locales/`)
Discord に見せる短い文言のカタログ。表示言語は `lilla.yaml` の `ui.locale`（既定 `ja`）だけで
決まり、OS の `LANG` や Discord 側の言語設定は見ない。

| ファイル | 役割 |
|----------|------|
| `ui/messages.py` | カタログから文言を取り出す `t(key, **params)`。`key` は `selftest.summary` のような安定した英語のドット区切りで、YAML 上も同じネストで持つ。`params` は文言中の `{ok}` などに埋める値。解決順は「`ui.locale` のカタログ → `ja` → キー名そのもの」で、どの段階でも例外は投げない（文言の欠落で応答自体が失われないため）。フォールバック時は英語の WARNING ログを 1 キーにつき 1 回だけ出す。ロケールをまたいで同じ文言を突き合わせるための `translations(key)`（承認依頼の区切り行の復元に使う）と、同梱カタログを列挙する `available_locales()` も提供する |
| `locales/ja.yaml` | 日本語カタログ（既定ロケール） |
| `locales/en.yaml` | 英語カタログ |

カタログは `CONFIG_ROOT` ではなくパッケージ同梱で、hatchling が wheel に含める
（`[tool.hatch.build.targets.wheel]` の `packages = ["src/lilla_core"]` 配下）。

### ユーティリティ (`src/lilla_core/utils/`)
| ファイル | 役割 |
|----------|------|
| `datetime_utils.py` | UTC の現在日時（`utc_now`）、実行環境のローカルタイムゾーン（`local_timezone`）、タイムゾーン aware なローカル現在日時（`local_now`）を返す共通ユーティリティ。JST タイムゾーン定数（`JST`）や、datetime を timezone-aware な UTC に正規化する `ensure_utc`・ISO 文字列を UTC datetime に変換する `parse_iso_utc` も提供する。JST のカレンダー日付で比較したい処理向けに、datetime を JST 日付（時刻切り捨て）へ変換する `to_jst_date` と、その JST 日付の終端（翌 0:00 JST）を UTC で返す `jst_day_end_utc`（Mongo クエリの上限に使う）も持つ（日次の期限判定などが利用する想定） |
| `oauth2_authorization_code_utils.py` | OAuth2 認可コードフロー（Authorization Code Grant）専用の認証情報ユーティリティ。アクセストークンの有効期限判定（`is_access_token_valid`）、リフレッシュトークン保持判定（`has_refresh_token`）、トークンレスポンスからの認証情報構築（`build_token_credentials`）を提供し、複数の OAuth2 クライアント（実装は拡張側）で共有できる（クライアントクレデンシャルフローや、API キー方式の認証情報は対象外） |
| `resource_loader.py` | `file:` / `dir:` プレフィックス付き source spec を受け取り、単一ファイル・ディレクトリ一括（`.md`/`.txt` をファイル名昇順）読み込みを統一的に扱うユーティリティ（`load_text_resources`。`${config_root}` 展開対応） |
| `path_utils.py` | ユーザー入力由来の相対パスを安全に扱う共通ユーティリティ。絶対パス・`..` セグメントの拒否（`validate_relative_path`）と、解決後のパスがルート配下にあることの確認つき解決（`resolve_within_root`）を提供する。ファイルシステム上のリソースを LLM の入力由来のパスで読み書きするツール（拡張側の実装）が利用する想定 |
| `content_hash.py` | ファイル内容の SHA-256 ハッシュ計算ユーティリティ（`compute_content_hash` / `normalize_newlines`）。改行コードを `\n` に正規化してから計算する。外部ストレージへの書き込みの楽観的排他制御など、拡張側のツールが利用する想定 |
| `media_utils.py` | メディアファイルの参照情報（拡張子を除いた `media_id` と `/media/files/` 配信 URL）を組み立てる共通ユーティリティ（`build_media_ref` / `MediaRef`）。メディア配信サーバー自体はコアには含まれず、拡張側で実装する想定 |

### ツール・スクリプトローダー群 (`src/lilla_core/loaders/`)
| ファイル | 役割 |
|----------|------|
| `llm_tool_loader.py` | `${CONFIG_ROOT}/tools/llm_*.yaml` と、`loaders/tool_paths.py` が解決したツールルート配下の `llm_*.py`（および `type: self` の場合は YAML と同名の `.py`）を動的に読み込む。LLM に渡す tools パラメータの構築（`build_tools_param`）と tool_call の実行（`execute_tool_call`）を担う。実行時にツールへ注入される context には `call_tool`（入れ子呼び出し用。深さ上限 `MAX_TOOL_CALL_DEPTH=5`）を自動的に加える。`tool_config` が実行時共通キー（`client_type` 等。拡張の `tool_context_providers()` が返すキー名も含む）と衝突していないか起動時に検証し、衝突時は fail-fast する（`_validate_no_runtime_key_collision`）。ツール設定の `cache.mode`（`disable` / `enable` / `auto`）に応じた実行結果の MongoDB キャッシュ保存（`_save_tool_cache`）、`client_type == "discord"` かつ通知コールバックが注入されている場合のツール呼び出しログ送信（`_notify_tool_call`）も担う |
| `task_tool_loader.py` | `${CONFIG_ROOT}/tools/task_*.yaml` と、`loaders/tool_paths.py` が解決したツールルート配下の Python クラスを動的に読み込む（`load_all_tools`）。定期実行タスクのクラスマップをキャッシュし、ファイル名プレフィックス（`task_` / `llm_` / `system_`）からトリガー種別を判定する |
| `script_loader.py` | ホワイトリスト検証（`AppConfig.paths.allowed_tool_paths_list`。拡張のロード後に差し替えられた設定を読むため、import 時ではなく呼び出しのたびに取得する）付きで外部 Python 関数・クラスを安全にロードする（`load_script_function` / `load_script_class`） |
| `tool_paths.py` | ツール探索ルートの解決（`resolve_tool_roots`。`paths.tool_root` の後に拡張の `tool_roots()` をロード順で足す）と、ツールファイルの検索（`find_tool_file`）。同名ファイルが複数ルートにあれば fail-fast し、1 ルート内の重複は従来どおり先頭マッチを使う。追加ルートはファイル探索専用で、`sys.path` へ入れるのは `paths.tool_root` の親だけ |

### 外部APIクライアント (`src/lilla_core/api/`)
特定の外部サービス向けの API クライアントはこのリポジトリには置かず、拡張側に実装する想定。

| ファイル | 役割 |
|----------|------|
| `llm_client.py` | LLM 統合インターフェース。Ollama（WakeOnLAN 対応）と OpenAI 互換プロバイダをサポート。`chat_to_llm`（非ストリーム・tools 無し）、`chat_to_llm_with_tools`（tools 付き、OpenAI 互換の `/chat/completions` 系エンドポイントを使用）、`chat_to_llm_responses`（`/responses` エンドポイント経由。xAI/Grok の組み込みツールなど、Chat Completions API では使えない機能向け）を提供する。推論モデルが最終回答を `reasoning_content` にしか出力しない場合のフォールバック処理や、リクエスト/レスポンスの秘匿情報マスキング付きログ出力も担う |

### データ永続化（リポジトリ層） (`src/lilla_core/repository/`)
特定ドメインのデータ（外部サービスから取得したデータなど）のリポジトリはここに置かず、
拡張側に実装する想定。

| ファイル | 役割 |
|----------|------|
| `motor_client.py` | Motor クライアントの共通ファクトリ（`create_motor_client`）。`tz_aware=True` を指定し、読み出す datetime を timezone-aware な UTC に統一する。`lru_cache(maxsize=1)` によりプロセス内で 1 インスタンスのみを共有する。全 Mongo アクセスはこのファクトリ経由でクライアントを生成する |
| `conversation_repository.py` | MongoDB に会話履歴を保存・取得（有効期限付き）。任意で分類タグ（`tags`）と、対応する Discord メッセージ情報（`discord_channel_id` / `discord_message_ids`）を保存でき、タグ指定の最新 1 件取得（`find_latest_by_tag`）と `_id` 指定の削除（`delete`）を提供する |
| `credentials_repository.py` | MongoDB に API 認証情報を `type` ごとに保存・更新する汎用リポジトリ（複数の OAuth クライアントが同一形状で利用する想定） |
| `user_memo_repository.py` | ユーザーメモ（指示・メモ）の CRUD。システムプロンプトに注入される |
| `tool_cache_repository.py` | ツール実行結果を `tool_cache` コレクションに TTL 付きでキャッシュ・取得する |
| `button_actions_repository.py` | Discord ボタン押下で実行する保留中アクションを MongoDB に保存（7 日間 TTL）。取得と削除を原子的に行う `find_one_and_delete` でボタンの二重押下による二重実行を防ぐ |
| `pending_tool_calls_repository.py` | 外部エージェントへの非同期依頼（結果待ち）を `pending_tool_calls` コレクションに保存・照会する。`expire_at` の TTL インデックス（`expireAfterSeconds: 0`）でレコードごとに有効期限を持ち、pending → completed の遷移は `find_one_and_update` で原子的に行う（結果の二重配送防止） |
| `admin_credential_repository.py` | 管理用ダッシュボード等を拡張側で実装する場合に使う想定の、管理者パスワード（bcrypt ハッシュ）のリポジトリ。`admin_credentials` コレクションに固定 `_id` で 1 件だけ保持する。`$setOnInsert` の upsert で登録するため、既にある場合は上書きせず False を返す（再設定の禁止を DB 側でも担保）。パスワードを忘れた場合はこのレコードを手動削除すると再設定が有効になる |
| `admin_session_repository.py` | 上記の管理者ログインのセッションを `admin_sessions` コレクションで管理する想定のリポジトリ。保存するのはセッション ID の SHA-256 ハッシュ（`_id`）と `expires_at`（発行時刻 + 30 日）で、生のセッション ID は保持しない。`expires_at` の TTL インデックス（`expireAfterSeconds: 0`）は放置セッションの掃除用途で、認証判定は `find_valid` が `expires_at > 現在時刻` を毎回比較して行う。ログアウト時は `delete` で即座に消す |

### tool_support/ — ツール開発者向け共通ヘルパー (`src/lilla_core/tool_support/`)
`execute(input, context) -> dict` というツールの契約自体は変えず、ツール本体の実装の中でだけ
任意に使える薄いラッパー・ヘルパー群を置く場所（いずれも opt-in）。

| ファイル | 役割 |
|----------|------|
| `tool_result.py` | LLM ツールの標準結果辞書（`success` / `tool_name` / `memory_entry` / `needs_auth` / `needs_auth_list` / `data` / `error`）を成功・失敗・再認証要求の用途別に生成する共通ヘルパー（`tool_success` / `tool_error` / `tool_needs_auth` / `tool_reauth_required`） |
| `context_ex.py` | `execute(input, context)` の `context`(dict) を便利に扱う薄いラッパー（`ContextEx`、opt-in）。`call_tool` の注入を前提とし、ローダー側の dict ベース処理には影響しない |
| `tool_response_ex.py` | ツール実行結果 dict（`success` / `tool_name` / `data` / `error` 形式）を便利に扱う薄いラッパー（`ToolResponseEx`、opt-in） |
| `date_range.py` | `today` / `yesterday` / `tomorrow` / `last_N_days` / `next_N_days` / `this_week` / `last_week` / `YYYY-MM-DD` / `YYYY-MM-DD/YYYY-MM-DD` 形式の日付範囲 Value Object（`DateRange`。週は日曜始まり・土曜終わり）と、時刻まで指定できる `DateTimeRange` |

## tests/ — テスト
`tests/` 配下に各モジュールの単体テストを配置（pytest で実行）。`tests/repository/test_motor_client.py`
のようにサブディレクトリを切ることもある。`tests/conftest.py` が `sys.path` に `src/` とリポジトリ
ルートを追加し、`AppConfig.env` の必須フィールド用にダミーの環境変数（`DISCORD_TOKEN`）と、
YAML 由来の必須セクション（`discord.my_user_id`）を持つ `tests/fixtures/config_root/lilla.yaml` を
指す `CONFIG_ROOT` を設定する。

## 処理フロー概要
```
起動スクリプト（LILLA_EXTENSIONS を設定。未指定でも起動可能）
  → lilla_core/bot.py 起動
  ├→ load_extensions()（LILLA_EXTENSIONS の各モジュールを import し、module.extension を
  │    集めて衝突を検証。拡張側の set_config() は import 副作用。未指定ならスキップ）
  ├→ 設定読み込み（get_config()）+ ログ設定（setup_logging）
  ├→ コマンド読み込み (commands.load_all_commands で commands/ と command_packages() を動的ロード)
  ├→ ツール読み込み (llm_tool_loader + task_tool_loader。探索ルートは tool_paths.resolve_tool_roots())
  ├→ main() 実行
  │    ├→ 各拡張の setup() をロード順に await
  │    │    （HTTP サーバー等、対話クライアントを増やす拡張の起動など）
  │    └→ Discord へ接続（bot.start）
  ├→ 接続完了時 (on_ready):
  │    ├→ コア確定リポジトリ + 拡張の startup_repos() を init_collection() で初期化
  │    └→ 定期スケジューラ開始 (task_handler.start_scheduler)
  └→ メッセージ受信時 (on_message → handlers/message_handler.py):
       ├→ メッセージフック (dispatch_on_message()。各拡張の on_message をロード順に連鎖。
       │    拡張が 0 個なら常に False)
       ├→ コマンド (!) → command_handler がコマンド名で commands/ のハンドラへ委譲
       ├→ オーナー以外からのメッセージ → 既知コマンド、または FrontMatter に実在する
       │    correlation_id を持つ外部エージェントの結果（→ `!toolresult` へ変換）のみ
       │    #lilla-approval の承認フローへ。承認後に command_handler が結果を配送する
       └→ 通常会話 → memory_manager で履歴+メモ+セッションメモリ統合 → conversation_service
            （tool_call ループ。拡張経由で会話開始フック・ツール context を注入）
            → LLM 呼び出し → 返信保存・送信
```

## 拡張（`core/extension.py`）まとめ
コアの外から機能を差し込むには `Extension` を継承し、必要なメソッドだけをオーバーライド
して、モジュールから `extension` 属性として 1 つだけ export する。`LILLA_EXTENSIONS`
（カンマ区切りの import パス）に並べたモジュールをロード順に読む。いずれのメソッドも
「何も貢献しない」デフォルトを持ち、拡張が 0 個でもコア単体で動く。

| メソッド | コア側の参照 | 用途 |
|----------|----------|------|
| `startup_repos` | `get_startup_repos` | `on_ready` で `init_collection()` を呼ぶ追加リポジトリファクトリ |
| `on_message` | `dispatch_on_message` | `on_message` の冒頭でロード順に呼ばれる。`True` で以降を止める |
| `setup` | `run_setup_hooks` | `main()` で `bot.start()` の前に順に await される非同期関数（HTTP サーバー等の起動など） |
| `result_deliveries` | `get_result_delivery` | `!toolresult` が結果を届ける先を `client_type` ごとに差し替える |
| `client_prompt_providers` | `get_client_prompt_provider` | `client_type` ごとにシステムプロンプトへ追記する文字列を返すプロバイダ（呼ぶたびに評価される） |
| `conversation_start_hooks` | `get_conversation_start_hook` | `run_conversation` の冒頭で `client_type` ごとに呼ばれる非同期関数 |
| `tool_context_providers` | `get_tool_context_providers`（全件） | ツール実行 context へ注入する値を context キー名ごとに供給する |
| `tool_roots` | `get_tool_roots` | `paths.tool_root` に足すツール探索ディレクトリ（`allowed_tool_paths` は自動で広げない） |
| `command_packages` | `get_command_packages` | `load_all_commands()` が追加で走査するパッケージ |
| `config_models` / `env_fields` | （未使用） | 設定合成用の予約。今回のローダは読まない |

### 衝突は fail-fast
拡張どうしで以下が重複したら、静かな後勝ちにせずロード時に例外を投げる。

- `Extension.name`（未設定・空文字も落とす）
- ツール実行 context プロバイダのキー
- `result_deliveries` / `client_prompt_providers` / `conversation_start_hooks` の `client_type`
- `command_packages` 経由で登録されるコマンド名（`register_command` が検出）
- 複数のツールルートに同じ名前のツールファイルがあるとき（`find_tool_file` が検出）

コア内蔵のデフォルトとの重複は衝突にしない。`client_type="discord"` のシステム
プロンプトは拡張が出していればそれを使い、誰も出していなければコアの
`discord_client_prompt` を使う。逆に `"discord"` の `result_deliveries` はコアが
配送を持つ予約キーで、拡張が登録するとロード時に落ちる。

### `on_message` の実行時例外
ある拡張の `on_message` が例外を投げたときは、その 1 通の処理をそこで打ち切る。
後続の拡張も通常の会話フローも動かさず、ERROR ログと `core/error_notify.py` の
エラー通知チャンネルへ出したうえで「処理済み」として扱う（プロセスは落とさない）。

### 起動順の規約
ホストが `AppConfig` のサブクラスを使う場合、そのモジュールの import 副作用で
`set_config()` を呼ぶ仕組みは残す。そのモジュールを `LILLA_EXTENSIONS` の先頭に置くのは
ホスト側の規約で、コアは順番を検証しない。並べたモジュールは同一プロセスで動く
**信頼コード** であり、サンドボックスではない。
