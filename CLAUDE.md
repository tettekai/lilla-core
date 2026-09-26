# プロジェクト概要
Lilla のコアランタイム（`lilla-core`）。
AI エージェントを構築するための汎用基盤ライブラリ。Discord ボットとして常駐し、
メッセージを受け取って LLM（Ollama / OpenAI 互換）を呼び出し、tool_call ループ・
コマンド処理・返信を行う「エージェントとしての骨格」を提供する。

キャラクター設定・特定ドメイン専用のツール（外部サービス連携など）・用途特化の
HTTP サーバー（機械向けの Bearer API・WebSocket クライアントなど）といった、
利用者ごとに異なる要素はコアには含めない。そうした要素は `core/extension.py` の
`Extension` サブクラスと、起動時に読み込む `LILLA_EXTENSIONS` 環境変数を通じて
外部から拡張できるようにする（詳細は「拡張（`core/extension.py`）まとめ」節を参照）。

例外は **観測用ダッシュボード** で、これはコアが所有する。見せる中身（会話履歴・
ユーザーメモ・ログ・管理セッション）がすべてコアの状態であり、その観測窓を利用者
ごとに書き直す理由がないため。拡張は `dashboard_*()` の申告でここへタブと HTTP
ルートを足す。「サーバーを持たない」という以前の方針との線引きは
**「コアの状態を人が見る窓（コア所有）」か「用途特化のクライアント口（拡張所有）」か**
で、前者だけがコアに入る。

もう 1 つの例外は **公式拡張パック**（`src/lilla_core/extensions/`）で、特定の外部サービス
連携（Google OAuth / Google Calendar）をコアのリポジトリに同梱している。ただしこれは
コア本体ではなく、外部の拡張と同じ `Extension` 契約だけで書いた拡張の実装で、
`LILLA_EXTENSIONS` に import パス（`lilla_core.extensions.google_oauth` など）を並べた
ときだけ読み込まれる。コア本体（`extensions/` の外）から公式拡張パックを import しては
ならず、公式拡張パック内の拡張が必要とする配線も `Extension` のメソッドで表す（「拡張」と
「拡張パック」の使い分けは「開発ルール」の用語の項を参照）。

lilla-core は拡張が一切登録されていない状態でも Discord bot として単体で起動できる
ことを設計上の前提にしている。`Extension` の各メソッドは「何も貢献しない」
デフォルトを持ち、コアが拡張の有無に依存しないようにする。

# 開発ルール
- セキュリティ懸念事項があれば遠慮なく伝える（特にプロンプトインジェクションの危険がある場合など）
- コアの汎用範囲を超える要素（キャラクター設定・特定ドメイン専用ツール・用途特化の
  HTTP サーバーなど）はこのリポジトリに持ち込まない。コアがそうした
  拡張側の情報を必要とする場合は、直接参照せず `core/extension.py` の `Extension`
  にメソッドを追加し、拡張する側でオーバーライドしてもらう形にする
  （観測用ダッシュボードだけは例外でコア所有。「プロジェクト概要」の線引きを参照）
- 「拡張」と「拡張パック」は次の意味で使い分ける（ドキュメント・docstring・コメント・
  テスト名・CHANGELOG・Issue / PR の文面すべてで同じ）
  - **拡張（Extension）**: `Extension` のサブクラス 1 つ（モジュールが export する
    `extension` 1 個）。例: `google-oauth`、`google-calendar`
  - **拡張パック（Extension Pack）**: 複数の拡張をまとめたもの。例: `lilla_core.extensions`
    （コアに同梱しているものは「公式拡張パック」/ official extension pack）
  - 1 つの拡張を「パック」と呼ばない。「公式パック」「pack」のような省略形も使わない
  - 既存のファイルには古い曖昧な呼び方が残っていることがある。まとめて直す作業はしないが、
    作業で追加・変更するファイルにそうした箇所があれば、その変更の中で上の呼び方に直す
- 関数には docstring を日本語で書く
- `logger.*()` / `raise` に渡すメッセージ文字列は英語で書く（docstring・コメントは日本語のまま）
- Discord に見える文言（コマンドの返信・ボタンのラベル・`notify_error` に渡す文脈など）は
  Python コードに直書きせず、`lilla_core/locales/{locale}.yaml` のカタログへ置き
  `lilla_core.ui.messages.t()` 経由で参照する。文言を足すときは同じ変更で `ja.yaml` と
  `en.yaml` の両方に入れること（対象外: logger / raise のメッセージ・コメント・docstring・
  システムプロンプト組み立て用の見出し・ツール SCHEMA の description）
- 利用者向けドキュメントは **日本語が正**、英語はその翻訳（AI 翻訳でよい）。手で直すのは
  日本語側で、同じ変更で英語側にも反映すること。英語ファイルの先頭には、日本語版が正で
  ある旨と翻訳である旨を一行書く
  - 入口はリポジトリ直下: `README.ja.md`（正）と `README.md`（英訳。GitHub / PyPI の既定）。
    README に置くのは「何か・入れる・最低限動かす・詳細へのリンク」までで、詳細は `docs/` へ書く
  - 詳細はディレクトリで言語を分ける: `docs/ja/`（正）と `docs/en/`（英訳）。同じファイル名
    （ステム）で対にし、同じフォルダに `*.ja.md` を並べない。ロゴなど言語に依らない資産は
    `docs/` 直下に置く
  - 目次は `docs/ja/README.md` / `docs/en/README.md`。ページを足したら目次にも相対リンクで
    足す。README から docs へ張るのはこの目次だけ（個別ページへ直リンクしない）
  - `README.md` は PyPI の説明文にそのまま載り相対パスが切れるため、README（日英とも）からの
    リンクと画像は GitHub の絶対 URL（`https://github.com/tettekai/lilla-core/blob/main/...`）で書く。
    `docs/` 内部は相対パスでよい

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
- 「人間側の今日 / いま」のタイムゾーンは `ui.timezone`（IANA 名。既定は未指定）で決める。
  未指定なら OS のローカルタイムゾーン、文字列を書けばそのタイムゾーンだけを使う。
  ロケールと違いフォールバックせず、空文字や `ZoneInfo` が受け付けない名前は起動時に落とす。
  新しく「今日」「いま」を扱うコードは `date.today()` / `datetime.now()` を直接書かず、
  `utils/datetime_utils.py` の `local_now()` / `local_timezone()` を通すこと
- コアの汎用範囲を超える固有の設定フィールド（特定の外部サービス連携など）は `AppConfig` に
  追加しない。拡張する側が `Extension.config_model()`（セクションモデル 1 つ。節名は申告せず
  `name` のハイフンをアンダースコアにしたもの＝`core/config.py` の `extension_section_name()`）と
  `Extension.env_fields()`（`EnvConfig` のフィールド名 → OS 環境変数名）で申告し、
  `load_extensions()` が `core/config.py` の `compose_config()` で 1 つの `AppConfig` へ合成する。
  拡張の YAML セクションはトップレベルではなくコア確定の `extensions:` の下に置き
  （`cfg.extensions.<節名>`。トップレベルの `cfg.<節名>` は作らない）、コアが後から
  トップレベルに節を足しても拡張の名前と衝突しないようにする。`extensions:` の下の
  未知キー（ロードしていない拡張の払い残し）と、申告した節がトップレベルに書かれている
  場合（移し忘れ。コア確定と同名のキーは除く）はどちらも起動時に落とす
  ホストが `AppConfig` のサブクラスを手書きして import 副作用で `set_config()` する方式は使わない
  （呼んでも合成結果で上書きされる）
- 拡張側のコードが自分のセクションを読むときは、`get_config().extensions.<節名>`（型は付かない）
  ではなく `core/config.py` の `get_section("<拡張の name>", <モデル>)` を使うと静的な型が付く
  （節名を渡してもよい）。
  `get_section()` は `extensions:` の下だけを探し、コア確定の節は対象外（`get_config().ui` のように直接読む）
- 合成の規則: セクションモデルが必須フィールドを 1 つでも持てばそのセクションは必須になり
  （その場合 `extensions:` 自体も必須）、全フィールドにデフォルトがあれば `lilla.yaml` に節が
  無くてもよい。`env_fields()` で足す
  フィールドの型は常に `str | None`（既定値 `None`）で、必須フィールドや文字列以外は表現できない
- 1 拡張が足せる設定モデルは 1 つだけ。複数の設定のまとまりは 1 つのモデルの子として並べる
  （節名を自分で書く API は持たない。旧 `config_models()` / `required_config_sections()` は
  `EXTENSION_API_VERSION` 2 で廃止し、定義したままの拡張はロード時に落とす）
- 他の拡張の節を読む依存は `requires`（拡張名）で表す。自分では提供しないが読む秘匿
  フィールドとツール実行 context キーは `required_env_fields()` /
  `required_tool_context_keys()` に並べ、誰も提供していなければロード時に fail-fast する
- 他の拡張に依存する場合はクラス属性 `requires`（拡張名のタプル）で宣言する。依存先が
  未ロード、または `LILLA_EXTENSIONS` で自分より後ろに並んでいればロード時に fail-fast する
  （コアは並べ替えない）。汎用の `validate()` フックは持たず、実行時の検査は `setup()` で行う

## HTTPアクセスのルール
- HTTPリクエストは必ずプロキシ経由で行う（プロキシ設定は `AppConfig` から自動適用される）
- `core/http_util.py` の `send_http_request` が使える場合は必ずそれを使う
  - JSON ボディ送信: `data=` パラメータ
  - フォームエンコード送信: `form_data=` パラメータ
- `aiohttp` を直接使って独自にセッションを張ることは禁止（バイト列が必要な添付ダウンロードなど、
  `send_http_request` の戻り値（str）では扱えない場合のみ例外。`services/attachment_download.py`
  がその唯一の例外実装で、他から参照して共有すること）

## バージョンと CHANGELOG
- 利用者に見える変更（API / 起動方法 / 設定 / 互換性）は、同じ PR で `CHANGELOG.md` の `[Unreleased]` に日本語で追記する。`[Unreleased]` セクションが無い場合はファイルの一番上に追加する。
- テストやコメントのみの変更は CHANGELOG に書かない
- `pyproject.toml` の version は触らない
- `Extension` 契約を破壊的に変えるとき（メソッドのシグネチャ・戻り値の形・`*Context` の
  フィールド・context キー・参照関数の削除や改名、フックのタイミング変更）は、同じ PR で
  `core/extension.py` の `EXTENSION_API_VERSION` を上げ、CHANGELOG に **BREAKING** で書く。
  メソッドや `*Context` フィールドの追加は非破壊で、バージョンは上げない

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
- main / develop への直接 push は禁止。必ず PR 経由でマージする
- 普段は main に触れない
- 普段: develop の最新から作業ブランチを切り、develop 向け PR を作る
- 作業ブランチを自分で切るとき: `issue-{番号}-{概要}`
- すでに作業ブランチが割り当てられているとき（例: Claude Code Actions の `claude/issue-{番号}-…`）は、そのブランチのまま進める。リネームしない。別名を要求しない。違反ともみなさない
- Issue 本文や実装依頼コメントで、作業ブランチ名を指定しない（base が特殊なときだけ base を書く）
- 大きな変更で長期ブランチを使うのは、指示があったときだけ。そのときは長期ブランチから作業ブランチを切り、長期ブランチ向け PR にする
- 長期ブランチ名: `feature/{番号}-{概要}`

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
| `bot.py` | Discord ボット本体。設定・コマンド・ツール・コア確定リポジトリの初期化と、Discord イベントの各 handler への委譲を統括する。起動直後（他の import より前）に `load_extensions()` で環境変数 `LILLA_EXTENSIONS`（カンマ区切り）が指すモジュールを import し、各モジュールの `extension` を集めて検証する（未設定ならコア単体で起動する）。`on_ready` / `on_message` / `on_interaction` は `handlers/` の各ハンドラへ委譲するだけの薄いラッパー。`main()` は拡張の `setup()` をロード順に await したあと `handlers/dashboard_server.py` の `start_dashboard_server()` で観測用ダッシュボードを起こし、最後に Discord へ接続する |
| `bot_client.py` | Discord `commands.Bot` インスタンスの生成のみを担う共有モジュール。`bot.py` をスクリプト実行した際の多重ロード（Discord 未接続の幽霊インスタンス生成）を防ぐため、`bot` インスタンスを参照する側は必ずこのモジュールから import する |
| `log_handler.py` | MongoDB へのログ書き込みハンドラー（`MongoDBHandler`。レベル別 TTL 付き） |

### コア基盤 (`src/lilla_core/core/`)
| ファイル | 役割 |
|----------|------|
| `config.py` | Pydantic ベースの設定管理（`AppConfig`）。`${CONFIG_ROOT}/lilla.yaml` はネスト構造のまま同じ形のセクションモデル（`cfg.discord.my_user_id` など）へ読み込み、`.env` / OS 環境変数は `EnvConfig`（`cfg.env.discord_token` など）へ読み込む（YAML の項目を環境変数で上書きする経路は持たない。YAML トップレベルの `env:` は警告して無視する）。複数 LLM プロバイダの動的選択に対応。`dashboard`（`DashboardConfig`。`host` / `port` / `cookie_secure`。全項目に既定があり節そのものを省略できる）は観測用ダッシュボードの listen 先と Cookie 属性を決める。`ui.locale`（`UiConfig`）は Discord に見せる文言のロケールを、`ui.timezone`（同じく `UiConfig`。IANA 名か未指定）は「人間側の今日 / いま」のタイムゾーンを決める（未指定なら OS のローカル。不正な名前・空文字はバリデーションで起動時に落とす）。コアの汎用範囲を超えるフィールドは持たず、拡張側が申告した YAML セクション・秘匿フィールドを `compose_config()` が `pydantic.create_model` で動的に足して 1 つのモデルに合成する。YAML セクションはコア確定の `extensions`（`ExtensionsConfig`。`extra="forbid"` で未知キーは起動時に落とし、中身の無い `extensions:` は空として扱う）のサブクラスへ足し、秘匿フィールドは `EnvConfig` へ足す。申告した節がトップレベルに書かれていたら `AppConfig` の before バリデータが移し忘れとして落とす（コア確定と同名のキーは除く）（拡張分の OS 変数名はモジュールレベルの `_extra_env_var_names` に登録し、`EnvConfigSettingsSource` が `_VAR_NAMES` へ重ねて読む。pydantic のモデル本体に置いたアンダースコア始まりの属性はプライベート属性扱いになり `settings_customise_sources()` から読めないため、クラス属性ではなくモジュールのレジストリで持つ）。コア確定の名前の一覧として `core_config_section_names()` / `core_env_field_names()` を公開する。拡張の `name` から `extensions:` 下の節名を導く `extension_section_name()`（ハイフン → アンダースコア）と、拡張が申告したセクションを型付きで取り出す `get_section(name, model, config=None)` も持つ（`name` は拡張名でも節名でもよい。`extensions:` の下だけを探す。未申告の名前・モデル不一致は `ValueError`。コア確定のセクションは対象外）。`get_config()` / `set_config()` でプロセス全体の設定インスタンスを共有し、通常は `load_extensions()` が合成結果を `set_config()` する。`set_config()` が一度も呼ばれていなければ `get_config()` は `_default_config()`（`_UncomposedAppConfig`）を返し、これはどの拡張が載るか分からないため `extensions:` の中身を検証せずに捨てる（拡張をロードしない運用スクリプトが、拡張の節を書いた `lilla.yaml` で落ちないようにするため。素の `AppConfig()` と `compose_config()` の結果は未知キーで落とす） |
| `extension.py` | コアの外から機能を差し込むための `Extension` 基底クラスと、そのロード・参照 API。観測用ダッシュボードへの差し込み（`dashboard_page` / `dashboard_static_dir` / `dashboard_routes` / `dashboard_public_routes`）も申告の型（`DashboardPage` / `DashboardRoute` / `DashboardPageEntry` / `DashboardStaticMount`）と集約をここに持つ（それを載せる HTTP サーバー本体は `handlers/dashboard_server.py`）。`Extension` は Adapter 型で、起動時リポジトリ・メッセージフック・起動処理（`setup`。引数は `SetupContext` 1 つ）・結果配送・クライアント固有プロンプト（加算式）・会話開始フック（加算式。引数は `ConversationContext` 1 つ）・ツール実行 context プロバイダ・追加ツールルート・追加コマンドパッケージの各メソッドに「何も貢献しない」デフォルトを持つ。`load_extensions()` が `LILLA_EXTENSIONS` のモジュールを import して各 `extension` を集め、`set_extensions()` が貢献キーの衝突を検証して登録し、続けて `compose_config()` の結果を `set_config()` でプロセスの設定に据える（拡張どうしの重複は fail-fast）。設定の合成そのものは `core/config.py` に閉じており、このモジュールは pydantic の組み立て詳細を知らない。`set_extensions()` は登録と検証だけで設定を差し替えないため、テストは拡張を登録してもプロセスの設定を壊さない。`lilla_core/bot.py` が拡張モジュールを直接 import しないための唯一の橋渡し層 |
| `exceptions.py` | `ReauthenticationRequiredError`（外部 API 再認証要求時）・`LLMError`（LLM 呼び出し失敗時）の例外定義 |
| `error_notify.py` | コマンド実行系・定期タスク実行系のエラー出力を一元化する（`notify_error`）。ERROR ログと Discord のエラー通知チャンネル（`discord.error_channel_id`。チャンネル ID で指定し、`bot.get_channel()` による ID 解決のみを行う。名前によるギルド横断検索は行わない）の 2 箇所にのみ出力し、元チャンネルへの `message.reply()` は行わない（bot 間チャンネルで相手 bot が reply に反応するのを防ぐため）。チャンネル未設定・ID 不正・未発見・送信失敗時は WARNING ログのみで、例外は投げない |
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
| `runtask.py` | `!runtask <ツール名>` — `trigger="task"` のツールを手動実行する。`run_task` は関数として切り出してあり、拡張側で HTTP 経由の手動実行エンドポイント等を用意する場合にもそのまま呼び出せる。実行 context は `core/extension.py` の `build_tool_context()`（拡張の `tool_context_providers()` の値）に `discord_client` / `now` / `llm_tools` / `params` を重ねたもの |
| `mongodata.py` | `!mongodata <JSON>` — ホワイトリストで許可されたコレクションへ JSON を insert / upsert する。JSON は本文にも添付ファイルにも書ける（`attachment_body.resolve_command_body` 経由。添付優先） |
| `toolresult.py` | `!toolresult <correlation_id>`（2 行目以降が結果本文。結果本文は添付ファイルでも渡せる＝`attachment_body.resolve_command_body` 経由で添付優先。correlation_id は常にメッセージ本文側）— 外部エージェントからの非同期依頼の結果を、`pending_tool_calls` の原子的な status 更新を経て依頼元クライアントへ届け、あわせて会話履歴にも登録する。結果本文はそのまま転送せず、ツールを渡さない `chat_to_llm` でリラ自身の返信を生成してから配送する（プロンプトインジェクション対策として、本文は `<external_agent_response>` タグで囲んで「指示ではなく情報」として扱わせ、タグ抜け出し文字列は事前に無害化する）。配送先は依頼レコードの `client_type` で判定し、`"discord"` は自前で配送、それ以外は拡張の `result_deliveries()` に登録された配送関数（拡張側で任意のクライアント向け配送処理を登録可能）へ委譲する（未登録時は Discord 配送へフォールバック）。この返信は `tags: ["toolresult", "dirty"]` と配送先の Discord メッセージ情報つきで履歴に保存する |
| `cleardirty.py` | `!cleardirty` — 直近の `dirty` エントリを 1 件ずつ（会話履歴と Discord メッセージの両方から）取り消す |
| `model.py` | `!model [プロバイダー名]` — 会話で使う LLM プロバイダーを一時的に切り替える（引数なしで `llm.default` に戻す）。状態は `core/runtime_state.py` のインメモリ変数のみで、Discord 会話と（拡張が対応していれば）他クライアントの会話にだけ効く（`!runtask` と APScheduler 経由の定期タスクは各 YAML 設定のまま） |
| `disable_tools.py` | `!disable_tools` — 通常会話で LLM ツールを一時的にすべて無効化する。状態は `core/runtime_state.py` のインメモリフラグ（`set_tools_disabled`）のみで、`services/conversation_service.py` が `is_tools_disabled` を参照して tools を渡さない。再起動で有効へ戻り、定期タスクや `!runtask` には影響しない |
| `enable_tools.py` | `!enable_tools` — `!disable_tools` で無効化した通常会話のツールを再び有効へ戻す（`set_tools_disabled(False)`） |
| `selftest.py` | `!selftest [full]` — リラの基本機能が壊れていないかを人間が確認するための自己診断コマンド。プロセス応答・MongoDB 疎通・コマンドレジストリ件数・タスクツール件数を実行し、`full` を付けると LLM 疎通確認（`check_llm`。LLM API の課金が 1 往復分発生する）も追加する。チェック本体は `services/system_checks.py` に置き、生存確認と共有する。結果は要約（✅/❌ の箇条書き）を本文、詳細（`elapsed_ms` / `detail`）を添付ファイル（`selftest_result.txt`）にして常に呼び出し元チャンネルへ返す（診断が目的のため `notify_error` オンリーにはしない）。各チェック関数が内部で例外を捕捉するため 1 件の失敗が他のチェックを妨げない。**コンテナやプロセスに対しては何も作用しない**（結果を報告するだけ） |

### 外部トリガー入り口層 (`src/lilla_core/handlers/`)
コアが持つのは Discord のメッセージ/インタラクション/コマンドのディスパッチ・定期タスクの実行と、
観測用ダッシュボードの HTTP サーバー（`dashboard_server.py`）。ダッシュボードは
コアの状態を人が見る窓なのでコア所有で、`bot.py` の `main()` が起こす。

一方、**対話クライアントを増やす実装**（機械向け Bearer API・WebSocket サーバーなど）は
このリポジトリには含めず、`Extension.setup()` を通じて `main()` の起動シーケンスへ
差し込む。拡張はダッシュボードにもタブ・HTTP ルートを足せるが、それは
`dashboard_*()` の申告経由で、リスナーを自分で持つわけではない。

| ファイル | 役割 |
|----------|------|
| `approval_flow.py` | オーナー以外から届いたコマンドの承認フローを担う共通ロジック。`discord.approval_channel_id`（チャンネル ID。`bot.get_channel()` による ID 解決のみを行い、名前によるギルド横断検索は行わない）で設定した承認チャンネルへの承認依頼投稿（`send_approval_request`）、承認 / 拒否ボタン押下の処理（`handle_approve_interaction` / `handle_reject_interaction`）、承認対象コマンド文字列の組み立て（`extract_approvable_command` / `build_toolresult_command`）を提供する。`bot` / `tools` はモジュール変数ではなく引数で受け取る。**信頼境界は「承認依頼メッセージ（bot 自身の投稿）」に置く**: 承認依頼を作る時点で `resolve_full_command` が BODY（テキスト直書き／添付ファイルのどちらでも）を解決して「実行される内容の全文」を組み立て、300 文字以内なら承認依頼メッセージ本文の区切り行（`command_marker()`。文言はロケールごとに異なるため定数ではなく関数で持ち、復元時は他ロケールの区切り行も候補にする）以降にそのまま書き、超える場合はプレビュー＋bot が新規作成した `message.txt` に全文を添付する（元メッセージの添付は使い回さない）。BODY を解決できない場合は承認依頼を作成しない。承認ボタン押下時は `interaction.message` 以外を一切参照せず（元メッセージの `fetch_message()` は行わない）、復元した全文と、添付を空にした代理メッセージ（`ApprovedMessage`）を `command_handler.handle_command` へ渡す。`custom_id` の channel_id は実行内容には使わず、`message.reply` の返信先（`ApprovedMessage.channel`）の解決にのみ使う（解決できなければ承認チャンネルへフォールバック）。これにより承認待ちの間に元メッセージが編集されても実行内容は変わらない（TOCTOU 対策）。元メッセージへの `jump_url` は送信者・文脈の確認用リンクとしてのみ表示する。なお承認ボタンの二重押下による二重実行防止は行っていない（許容リスク） |
| `command_handler.py` | `!` プレフィックスの Discord コマンドのディスパッチのみを担う。コマンド文字列を最初の空白で 1 回だけ分割し、コマンド名の完全一致で `commands/` のレジストリを引いてハンドラへ委譲する（個別コマンドのロジックは持たない）。既知コマンドで始まる行以降を切り出す `extract_command_content` もレジストリのコマンド名一覧から判定する |
| `interaction_handler.py` | Discord のインタラクション（ボタン押下）イベントのディスパッチ。`custom_id` のプレフィックスで処理を振り分け、`approve:` / `reject:` は `handlers/approval_flow.py` へ、`command:{コマンド文字列}` は `handlers/command_handler.py` へ汎用的に委譲し、`action:{uuid}` 形式の保留中アクション（`button_actions`）のみ自身で取得・実行して結果を followup で返す。保留中アクションはツール名で特別扱いせず常に `execute_tool_call` を通すため、会話履歴には残らない。デフォルトではオーナー以外のインタラクションを拒否する（プレフィックス分岐より前で一括拒否。`message_handler.py` と同じ判定パターン）。コアは汎用ランタイムであり、より緩い権限モデルは利用側の拡張で差し替え可能という位置づけ。`bot` / `tools` / `llm_tools` は引数で受け取る |
| `message_handler.py` | Discord のメッセージ受信イベントのディスパッチ。メッセージフック（`extension.dispatch_on_message()`。拡張が 0 個なら常に `False`）→ 承認フロー振り分け → コマンド処理 → 通常会話、の順に処理する。通常会話は画像添付の変換（`services/image_attachment.py` へ委譲。サイズ超過・ダウンロード失敗で None が返ったら会話処理自体を行わない）・`run_conversation` の呼び出し・応答の分割送信と会話履歴保存を担い、送信中タスクをチャンネル単位で保持して後続メッセージ受信時に先行タスクをキャンセルする。`bot` / `tools` / `llm_tools` / `message_hook` は引数で受け取る |
| `request_params.py` | HTTP ハンドラー共通のリクエスト入力解析ユーティリティ。整数クエリパラメータのデフォルト値・範囲丸め付き取得（`parse_int_param`）、JSON ボディのパース（`parse_json_body`）、`ObjectId` へのパス変数変換（`parse_object_id`）を提供する。aiohttp のレスポンス生成自体は呼び出し側（拡張側の HTTP ハンドラーなど）に委ねる |
| `dashboard_server.py` | 観測用ダッシュボードの HTTP サーバー。**コアが所有し、`bot.py` の `main()` が拡張の `setup()` のあと・Discord 接続の前に起こす**（申告の集約が終わっていること・拡張の起動が失敗したら観測窓も開かないこと、の 2 つが理由）。listen 先は `dashboard.host` / `dashboard.port`。**パスワード認証つき**: `auth_middleware` が既定拒否でリクエストを振り分け、`/`・`/static/*`・`/oauth/*`・`/api/auth/status`（画面分岐用）・`/api/login` のみ認証対象外、`/api/setup` はパスワード未登録時のみ通し登録済みなら 403（未登録のあいだは起動時に生成して起動ログへ WARNING で一度だけ出す一度きりのセットアップトークンがボディの `setup_token` と一致したときだけ登録を受け付け、登録に成功したら捨てる。画面や認証前の API 応答には載せない）、それ以外はセッション Cookie 必須で未認証は 401。パスワードは bcrypt（コスト 12。イベントループを塞がないようスレッドプール実行）で `admin_credentials` に 1 件だけ保持し、ログイン成功時に `secrets.token_urlsafe(32)` のセッション ID を発行して SHA-256 ハッシュだけを `admin_sessions` に保存する（生の ID は HttpOnly・SameSite=Strict の Cookie にのみ存在）。有効期限は認証のたびに `expires_at` をアプリ側で比較して判定し、TTL インデックスは物理削除（掃除）専用。ログイン失敗は IP ごとに直近 60 秒で 5 回までで、超えると 429（カウンタはプロセス内メモリのみ・再起動でリセット）。画面の一覧は `GET /api/dashboard/nav` が返し、組み込み 4 画面（`_BUILTIN_PAGES`）に `get_dashboard_pages()` が返す拡張のページをロード順で足す。拡張の申告は `_add_extension_routes()` が載せる: 静的ファイル（`/static/ext/{name}/`。認証不要）・セッション API（`/api/{name}`。Cookie 必須）・公開ルート（`/oauth/{name}`。認証不要）。静的ファイルは `/static` の一括配信より **先** に登録する（aiohttp は登録順に最初にマッチしたリソースを使うため、後に回すと `/static` に吸われて 404 になる）。設定はモジュールの import 時ではなく呼び出しのたびに `get_config()` で引く |
| `task_handler.py` | `trigger="task"` のツールの実行を管理する。APScheduler（`BackgroundScheduler`）による定期ジョブ管理。実行 context は LLM ツールと同じ注入モデルで、`core/extension.py` の `build_tool_context()`（拡張の `tool_context_providers()` の値）に `discord_client` / `now` / `llm_tools` を重ねる。タイムゾーンは `local_timezone()` の解決結果（`ui.timezone`、未指定なら OS のローカル）で、`CronTrigger` はスケジューラの設定を引き継がないため crontab 式にも同じタイムゾーンを明示的に渡す。ジョブは `asyncio.run_coroutine_threadsafe` で Discord の `bot.loop` に投げる |

### ビジネスロジック層 (`src/lilla_core/services/`)
特定ドメイン向けのサービス（外部サービス連携のデータ集計など）はここに置かず、拡張側で実装する想定。

| ファイル | 役割 |
|----------|------|
| `attachment_download.py` | Discord 添付ファイルのダウンロード共通処理（`download_attachment_bytes` / `resolve_proxy_settings` / `normalize_content_type`）。プロキシ設定を尊重して Discord CDN から取得する。画像添付（`services/image_attachment.py`）とコマンドの BODY 添付（`commands/attachment_body.py`）で共有する |
| `image_attachment.py` | Discord の画像添付を LLM へ渡す `image_url` パート（data URL）へ変換する処理。対応 MIME タイプの絞り込み（`filter_image_attachments`）と、サイズ上限ガード付きのダウンロード＋base64 化（`build_image_content_parts`）を担う。上限は `AppConfig.bot.max_image_attachment_size_mb`（既定 8MB。未設定・不正値・0 以下なら既定値）で、ダウンロード前に `attachment.size` で早期に弾き、Discord 側の申告値を過信しないようダウンロード後の実バイト数でも再検証する。上限超過・ダウンロード失敗はいずれも `notify_error` で通知して None を返し（例外は呼び出し元へ伝播させない）、呼び出し元は会話処理そのものを中止する |
| `conversation_service.py` | tool_call ループと会話履歴の読み書きを担う共通ロジック。Discord をはじめ、複数の対話クライアントのエントリポイントから再利用できる。LLM 最終応答の META ブロック（`actions`）を種別ごとにディスパッチして適用する（`set_session_memory` でセッションメモリを更新/クリア）。クライアント種別の判定は `"task"` かどうかだけで行い、それ以外の対話クライアント種別（`"discord"` や拡張が増やす種別）はコア側に列挙しない。会話開始フック（`extension.get_conversation_start_hooks`。`ConversationContext` を組み立ててロード順に await する）・ツール実行 context プロバイダ（`extension.build_tool_context`。実体は `core/extension.py` にあり、ここからも import できる）を経由して拡張の差し込みポイントを利用する。呼び出し元クライアントが `client_state=` で渡した任意の状態は、コアでは解釈せずフックの `ConversationContext.client_state` とツール実行 context の `client_state` キーへそのまま載せる |
| `memory_manager.py` | 会話履歴・ユーザーメモ・セッションメモリを統合し、LLM 向けシステムプロンプトを構築する（`build_system_prompt`）。履歴の対象期間とタイムスタンプ表示、プロンプトに埋め込む現在時刻はいずれも `local_timezone()` の解決結果を使う。クライアント種別ごとのプロンプト追記は `_resolve_client_prompt()` が「拡張の `client_prompt_providers()`（複数あればロード順に空行区切りで連結）→ コア内蔵（`"discord"` のみ。拡張が 1 つも出していないときだけ）→ 付けない」の順で解決する。ツールキャッシュ（`tool_cache_repository`）の有効なレコードも `## Cached Tool Results` としてシステムプロンプトへ埋め込む。登録チャンネルでの会話では `channel_summaries` の要約（部屋のノート）を `## Channel Note` として差し込む（`_resolve_channel_summary_section`。`summary_date` を併記し、本文は信頼しないコンテキストとして `<channel_note>` タグで囲む。未登録チャンネル・DM には出さず、取得に失敗しても WARNING ログのみでプロンプト組み立ては続行する） |
| `message_splitter.py` | LLM 応答を `---SPLIT---` / 改行2つ / タイムスタンプ境界で分割し、意味のない断片とタイムスタンプ prefix を除去するユーティリティ（`split_response`） |
| `message_util.py` | メッセージ送信ユーティリティ。フラグパース（`parse_message_flags`）・タイムスタンプ prefix の付与/除去（`prepend_timestamp_prefix` / `strip_timestamp_prefix`）・システムプロンプト埋め込み用セッションメモリブロックの整形（`format_session_memory_block`）・LLM 出力の META ブロック（JSON）の抽出（`extract_meta_block`）・外部エージェントとやりとりする FrontMatter 付きメッセージの組み立て/解釈（`build_correlation_frontmatter` / `parse_correlation_frontmatter`）・DM チャンネルの解決と Discord への送信（`resolve_dm_channel` / `send_to_discord`） |
| `session_memory_manager.py` | 単一領域のセッションメモリ（作業の途中状態や一時的な意図）をプロセス内メモリで保持する。TTL 付き、MongoDB 永続化なし。更新は LLM 出力の META アクション `set_session_memory` 経由で行う |
| `system_checks.py` | 生存確認（liveness。拡張側の HTTP ヘルスチェックエンドポイントなどから利用される想定）と自己診断（`!selftest`）が共有する個別チェック関数群（`CheckResult` / `check_process_alive` / `check_mongodb` / `check_command_registry` / `check_task_tools` / `check_llm`）。どの関数を組み合わせるかは呼び出し側が選ぶ。各関数は内部で例外を捕捉し、失敗時も例外を投げず `ok=False` の `CheckResult` を返す（1 件の失敗が他のチェックを妨げない）。`check_mongodb` は 3 秒、`check_llm` は LLM 往復のみ 30 秒の内部タイムアウトを持つ。`check_llm` は本番同様に `build_system_prompt` でプロンプトを組み立ててトークン数（tiktoken `cl100k_base` の概算。システムプロンプトのみが対象）を計測してから `chat_to_llm("ping")` を送る。プロバイダーは `runtime_state.get_active_llm_name()`（`None` なら `llm.default`）で解決した「今実際に使われているもの」を使う。detail にはシステムプロンプト本文を含めない（ユーザーメモ等の私的な内容が結果に残るのを避けるため） |

### 観測用ダッシュボード UI (`src/lilla_core/dashboard/`)
`handlers/dashboard_server.py` が静的ファイルとして配信する SPA。`locales/` と同じく
パッケージ同梱で、hatchling が wheel に含める（`packages = ["src/lilla_core"]` 配下）。

| ファイル | 役割 |
|----------|------|
| `index.html` | ダッシュボードの HTML（Alpine.js ベース） |
| `app.js` | ダッシュボードの JavaScript ロジック |
| `style.css` | ダッシュボードのスタイル |

画面は `view`（`loading` / `setup` / `login` / `dashboard`）で切り替える単一 SPA で、
`init()` が `GET /api/auth/status` を叩いて分岐する（`setup_required` の判定を
`authenticated` より必ず先に行う。DB リセット直後に古いセッション Cookie が残っていても
`/setup` を優先表示するため）。API 呼び出しは共通ラッパー `authedFetch` を通し、401 を
検知したら `login` 画面へ強制遷移させる。

画面の一覧は `startDashboard()` が `GET /api/dashboard/nav` から 1 度だけ取り、`setNav()` が
`routeByHash` / `hashByName` を組み立てる（対応表をフロントに直書きせず、組み込みも拡張も
同じカタログから引く）。取得に失敗したら `FALLBACK_PAGES`（組み込み 4 画面）へ落として、
ナビが取れないだけでダッシュボードごと使えなくならないようにする。`navigate()` は
`location.hash` を書き換えるだけで、描画とデータ取得は `hashchange` 経由の `resolveRoute()`
に一本化する。表に無いハッシュは Home へ落とし、`replaceState` で `#/` へ正規化する。
`group === 'main'` は中央ナビ（`mainNav`）、`'admin'` は右上のオーバーフローメニュー
（`adminNav`）に Logout と同列で並ぶ。

カタログの `module`（拡張ページの JS モジュール URL）を持つ画面は `x-ref="extSlot"` の
スロットに描く。`mountExtPage()` が `import(module)` して `mount(el, ctx)` を呼び、画面を
離れるときに `unmount()` を呼ぶ（`unmount` の export は任意）。`ctx` に渡すのは
`authedFetch` と `formatDate` だけで、Alpine のコンポーネント内部は渡さない。`import()` の
失敗・`mount()` の不在はスロット内のエラー表示に留めて SPA 全体を止めない。タブを素早く
切り替えたときに古い `import()` の完了が新しい画面を上書きしないよう、`_extToken` の世代で
捨てる。組み込み 4 画面（Home / Conversations / Memos / Logs）の DOM は `index.html` の
ままで、JS モジュール化はしていない。

**この SPA の文言は日本語の直書き**で、`ui/messages.py` のカタログ管理外。`t()` のルールは
Discord に見せる文言が対象で、ダッシュボードの国際化は別途対応する。

### UI 文言 (`src/lilla_core/ui/`・`src/lilla_core/locales/`)
Discord に見せる短い文言のカタログ。表示言語は `lilla.yaml` の `ui.locale`（既定 `ja`）だけで
決まり、OS の `LANG` や Discord 側の言語設定は見ない。拡張も `Extension.locale_dirs()` で
同じ命名のカタログを同梱でき、コアのカタログへロード順に重ねて解決される。

| ファイル | 役割 |
|----------|------|
| `ui/messages.py` | カタログから文言を取り出す `t(key, **params)`。`key` は `selftest.summary` のような安定した英語のドット区切りで、YAML 上も同じネストで持つ。`params` は文言中の `{ok}` などに埋める値。解決順は「`ui.locale` のカタログ → `ja` → キー名そのもの」で、どの段階でも例外は投げない（文言の欠落で応答自体が失われないため）。フォールバック時は英語の WARNING ログを 1 キーにつき 1 回だけ出す。ロケールをまたいで同じ文言を突き合わせるための `translations(key)`（承認依頼の区切り行の復元に使う）と、カタログを列挙する `available_locales()` も提供する。カタログはコア同梱分だけでなく、拡張が `Extension.locale_dirs()` で同梱した `{locale}.yaml` を `_load_catalog()` がロード順に重ねた合成結果で、`t()` / `translations()` / `available_locales()` はいずれも合成後を見る。1 つの拡張が複数ディレクトリを返した場合、その拡張のノードはロード順に浅くマージする（同じキーは後のディレクトリが勝つ）。拡張のカタログはトップレベルのキーがその拡張の `name` ただ 1 つでなければならず（`t("lilla-habits.notify.title")` の形。コアは prefix を付けない）、違反や拡張名とコアのトップレベルキーの衝突は `ValueError` で fail-fast する。ただし検出は `t()` の呼び出し時ではなく **拡張の登録時** で、`set_extensions()` がグローバルを書き換える前に `validate_catalogs(locale_dirs)` で全ロケール分を組み立てて検証する（`t()` は従来どおり例外を投げず、失敗しても壊れた登録は残らない）。存在しないディレクトリは WARNING で読み飛ばし（`lru_cache` によりロケールごとに 1 回）、壊れた YAML と「拡張名の下が辞書でない」カタログは ERROR ログを出してそのロケール分だけ空として扱う。合成結果は登録済み拡張に依存するため、`set_extensions()` は登録後に `clear_cache()` も呼ぶ（循環 import を避けるため `core/extension.py` 側は関数内の遅延 import）|
| `locales/ja.yaml` | 日本語カタログ（既定ロケール） |
| `locales/en.yaml` | 英語カタログ |

カタログは `CONFIG_ROOT` ではなくパッケージ同梱で、hatchling が wheel に含める
（`[tool.hatch.build.targets.wheel]` の `packages = ["src/lilla_core"]` 配下）。

### ユーティリティ (`src/lilla_core/utils/`)
| ファイル | 役割 |
|----------|------|
| `datetime_utils.py` | UTC の現在日時（`utc_now`）と、アプリが「人間側の今日 / いま」として使うタイムゾーン（`local_timezone`）・その現在日時（`local_now`）を返す共通ユーティリティ。`local_timezone()` は呼び出しのたびに `ui.timezone` から解決する（未指定なら `datetime.now().astimezone().tzinfo`）ため、`get_config()` をモジュール import 時に束縛しないこと。datetime を timezone-aware な UTC に正規化する `ensure_utc`・ISO 文字列を UTC datetime に変換する `parse_iso_utc` も提供する。カレンダー日付で比較したい処理向けの `to_jst_date`（日付へ変換）と `jst_day_end_utc`（その日付の終端＝翌 0:00 を UTC で返す。Mongo クエリの上限に使う）は、名前に反して `local_timezone()` の解決結果を基準にする（名前は互換のため維持）。`JST` 定数だけは `ui.timezone` の値によらず UTC+9 固定で、日本時間を明示したいコード向けに残している |
| `oauth2_authorization_code_utils.py` | OAuth2 認可コードフロー（Authorization Code Grant）専用の認証情報ユーティリティ。アクセストークンの有効期限判定（`is_access_token_valid`）、リフレッシュトークン保持判定（`has_refresh_token`）、トークンレスポンスからの認証情報構築（`build_token_credentials`）を提供し、複数の OAuth2 クライアント（実装は拡張側）で共有できる（クライアントクレデンシャルフローや、API キー方式の認証情報は対象外） |
| `resource_loader.py` | `file:` / `dir:` プレフィックス付き source spec を受け取り、単一ファイル・ディレクトリ一括（`.md`/`.txt` をファイル名昇順）読み込みを統一的に扱うユーティリティ（`load_text_resources`。`${config_root}` 展開対応） |
| `path_utils.py` | ユーザー入力由来の相対パスを安全に扱う共通ユーティリティ。絶対パス・`..` セグメントの拒否（`validate_relative_path`）と、解決後のパスがルート配下にあることの確認つき解決（`resolve_within_root`）を提供する。ファイルシステム上のリソースを LLM の入力由来のパスで読み書きするツール（拡張側の実装）が利用する想定 |
| `content_hash.py` | ファイル内容の SHA-256 ハッシュ計算ユーティリティ（`compute_content_hash` / `normalize_newlines`）。改行コードを `\n` に正規化してから計算する。外部ストレージへの書き込みの楽観的排他制御など、拡張側のツールが利用する想定 |
| `media_utils.py` | メディアファイルの参照情報（拡張子を除いた `media_id` と `/media/files/` 配信 URL）を組み立てる共通ユーティリティ（`build_media_ref` / `MediaRef`）。メディア配信サーバー自体はコアには含まれず、拡張側で実装する想定 |

### ツール・スクリプトローダー群 (`src/lilla_core/loaders/`)
| ファイル | 役割 |
|----------|------|
| `llm_tool_loader.py` | `tool_paths.resolve_tool_config_files("llm_")` が集めた YAML（拡張の同梱分 + `${CONFIG_ROOT}/tools/llm_*.yaml`。`enabled: false` は除外）と、`loaders/tool_paths.py` が解決したツールルート配下の `llm_*.py`（`type: self` の場合は YAML と同名の `.py`、`type` が `.` を含む場合は import パスとして `importlib` で読んだモジュール）を動的に読み込む。LLM に渡す tools パラメータの構築（`build_tools_param`）と tool_call の実行（`execute_tool_call`）を担う。実行時にツールへ注入される context には `call_tool`（入れ子呼び出し用。深さ上限 `MAX_TOOL_CALL_DEPTH=5`）を自動的に加える。`tool_config` が実行時共通キー（`client_type` 等。拡張の `tool_context_providers()` が返すキー名も含む）と衝突していないか起動時に検証し、衝突時は fail-fast する（`_validate_no_runtime_key_collision`）。ツール設定の `cache.mode`（`disable` / `enable` / `auto`）に応じた実行結果の MongoDB キャッシュ保存（`_save_tool_cache`）、`client_type == "discord"` かつ通知コールバックが注入されている場合のツール呼び出しログ送信（`_notify_tool_call`）も担う |
| `task_tool_loader.py` | `tool_paths.resolve_tool_config_files("task_")` が集めた YAML（拡張の同梱分 + `${CONFIG_ROOT}/tools/task_*.yaml`。`enabled: false` は除外）と、`loaders/tool_paths.py` が解決したツールルート配下の Python クラス（`type` が `.` を含む場合は import パスで読んだモジュールのクラス）を動的に読み込む（`load_all_tools`）。定期実行タスクのクラスマップをキャッシュし、トリガー種別は YAML のファイル名 stem のプレフィックス（`task_` / `llm_` / `system_`）から判定する（`type` から判定すると import パス指定のツールが `other` になってしまうため） |
| `script_loader.py` | 外部 Python 関数・クラスをロードする（`load_script_function` / `load_script_class`）。読み込み済みモジュールからツールクラス（`execute` を持つ型）を探す `find_tool_class` はファイル経由・import パス経由の両方で共有する。ロードしてよい範囲は `tool_dirs` 引数（呼び出し側が探索に使ったルート）か、省略時は `tool_paths.resolve_tool_dirs()`（設定から都度導く。import 時に固定しない）で決め、解決後のパスがその配下に無ければ ERROR ログを出して `None` を返す |
| `tool_paths.py` | ツール探索ルートの解決（`resolve_tool_roots`。`paths.tool_root` の後に拡張の `tool_roots()` をロード順で足す）と、ツールファイルの検索（`find_tool_file`）。ツール YAML の収集は `resolve_tool_config_files(prefix, config_root)` が担い、拡張の `tool_config_roots()`（ロード順）→ `config_root/tools` の順で集めて同じ stem は後者が丸ごと上書き、拡張どうしの同じ stem は fail-fast する。`is_tool_enabled()` は `enabled: false` と明示した YAML だけを無効と判定する。YAML の `type` が `.` を含むかで import パスかどうかを判定し（`is_import_path`）、import パスなら `importlib` で解決する（`import_tool_module`。失敗は WARNING でそのツールだけスキップ。インストール済みパッケージは `LILLA_EXTENSIONS` と同じ信頼レベルなのでディレクトリ検査はしない）。同名ファイルが複数ルートにあれば fail-fast し、1 ルート内の重複は従来どおり先頭マッチを使う。追加ルートはファイル探索専用で、`sys.path` へ入れるのは `paths.tool_root` の親だけ。ツールの `.py` をロードしてよいディレクトリは `resolve_tool_dirs()`（探索ルート + `config_root/tools`）が返し、`is_within_tool_dirs()` が解決後のパスがその配下にあることを検査する（`..` を含む `type` や外を指すシンボリックリンクを弾く）。これは信頼境界ではなく不変条件の検査で、`allowed_tool_paths` のようなホワイトリスト設定は持たない（`CONFIG_ROOT` と各ツールディレクトリへ書き込める者はコードを実行できる。`SECURITY.md` 参照） |

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
| `conversation_repository.py` | MongoDB に会話履歴を保存・取得（有効期限付き）。任意で分類タグ（`tags`）と、対応する Discord メッセージ情報（`discord_channel_id` / `discord_message_ids`）を保存でき、タグ指定の最新 1 件取得（`find_latest_by_tag`）と `_id` 指定の削除（`delete`）、チャンネル・期間指定の取得（`load_by_channel_between`。`discord_channel_id` の無い既存ドキュメントは対象外）、期間・キーワード・発言者・チャンネルを任意に重ねた検索（`search`。キーワードはエスケープしたうえで `message.content` への部分一致で AND 検索する）を提供する。インデックスは `time`（TTL）に加えて `(discord_channel_id, time)` の複合を張る |
| `channel_summary_repository.py` | 登録 Discord チャンネルごとの「部屋のノート」を `channel_summaries` コレクションに保持する。`discord_channel_id` がユニークで 1 チャンネル 1 ドキュメント、`upsert()` で上書きする（TTL は持たない）。`summary_date` は要約対象日（解決済みタイムゾーンの暦日、ISO 日付文字列）、`updated_at` は書き込み時刻 |
| `credentials_repository.py` | MongoDB に API 認証情報を `type` ごとに保存・更新する汎用リポジトリ（複数の OAuth クライアントが同一形状で利用する想定） |
| `user_memo_repository.py` | ユーザーメモ（指示・メモ）の CRUD。システムプロンプトに注入される |
| `tool_cache_repository.py` | ツール実行結果を `tool_cache` コレクションに TTL 付きでキャッシュ・取得する |
| `button_actions_repository.py` | Discord ボタン押下で実行する保留中アクションを MongoDB に保存（7 日間 TTL）。取得と削除を原子的に行う `find_one_and_delete` でボタンの二重押下による二重実行を防ぐ |
| `pending_tool_calls_repository.py` | 外部エージェントへの非同期依頼（結果待ち）を `pending_tool_calls` コレクションに保存・照会する。`expire_at` の TTL インデックス（`expireAfterSeconds: 0`）でレコードごとに有効期限を持ち、pending → completed の遷移は `find_one_and_update` で原子的に行う（結果の二重配送防止） |
| `admin_credential_repository.py` | 観測用ダッシュボード（`handlers/dashboard_server.py`）のログインに使う管理者パスワード（bcrypt ハッシュ）のリポジトリ。`admin_credentials` コレクションに固定 `_id` で 1 件だけ保持する。`$setOnInsert` の upsert で登録するため、既にある場合は上書きせず False を返す（再設定の禁止を DB 側でも担保）。パスワードを忘れた場合はこのレコードを手動削除すると再設定が有効になる |
| `admin_session_repository.py` | 上記の管理者ログインのセッションを `admin_sessions` コレクションで管理するリポジトリ。保存するのはセッション ID の SHA-256 ハッシュ（`_id`）と `expires_at`（発行時刻 + 30 日）で、生のセッション ID は保持しない。`expires_at` の TTL インデックス（`expireAfterSeconds: 0`）は放置セッションの掃除用途で、認証判定は `find_valid` が `expires_at > 現在時刻` を毎回比較して行う。ログアウト時は `delete` で即座に消す |

### tool_support/ — ツール開発者向け共通ヘルパー (`src/lilla_core/tool_support/`)
`execute(input, context) -> dict` というツールの契約自体は変えず、ツール本体の実装の中でだけ
任意に使える薄いラッパー・ヘルパー群を置く場所（いずれも opt-in）。

| ファイル | 役割 |
|----------|------|
| `tool_result.py` | LLM ツールの標準結果辞書（`success` / `tool_name` / `memory_entry` / `needs_auth` / `needs_auth_list` / `data` / `error`）を成功・失敗・再認証要求の用途別に生成する共通ヘルパー（`tool_success` / `tool_error` / `tool_needs_auth` / `tool_reauth_required`） |
| `context_ex.py` | `execute(input, context)` の `context`(dict) を便利に扱う薄いラッパー（`ContextEx`、opt-in）。`call_tool` の注入を前提とし、ローダー側の dict ベース処理には影響しない |
| `tool_response_ex.py` | ツール実行結果 dict（`success` / `tool_name` / `data` / `error` 形式）を便利に扱う薄いラッパー（`ToolResponseEx`、opt-in） |
| `date_range.py` | `today` / `yesterday` / `tomorrow` / `last_N_days` / `next_N_days` / `this_week` / `last_week` / `YYYY-MM-DD` / `YYYY-MM-DD/YYYY-MM-DD` 形式の日付範囲 Value Object（`DateRange`。週は日曜始まり・土曜終わり）と、時刻まで指定できる `DateTimeRange`。相対指定の基準日は `local_timezone()` が解決したタイムゾーンのカレンダー日付 |

### builtin_tools/ — コア組み込みツール (`src/lilla_core/builtin_tools/`)
個人データ・外部サービス依存の無い、コア単体でも動くツールを置く場所。`pip install`
するだけで使える組み込みツールの実例で、ツール YAML の `type` にドット区切りの import パス
（`loaders/tool_paths.py` の `is_import_path`）を指定して参照する。`${CONFIG_ROOT}/tools/` に
YAML を置いた人だけが有効化する opt-in で、コアが自動で読み込むことはない。

| ファイル | 役割 |
|----------|------|
| `llm_conversation_get.py` | 会話履歴を期間・キーワード・発言者で検索する LLM ツール（SCHEMA 上の関数名は `get_conversations`。LLM へ見せる名前は YAML の stem で上書きされる）。任意パラメータ `channel_name` に `discord.channels` の登録名を渡すと、その `discord_channel_id` の発言だけに絞る（前後空白を除いた完全一致・大文字小文字は区別。登録に無い名前は全件検索へ落とさず `tool_error`。省略時は全チャンネル横断）。検索本体は `ConversationRepository.search()` で、期間の境界と表示時刻は `local_timezone()` の解決結果を使う |
| `llm_current_datetime.py` | 現在日時を返すだけのサンプル LLM ツール。`SCHEMA` と `async def execute(input, context)` を持つ通常の LLM ツールで、`utils/datetime_utils.py` の `local_now()` を使う。有効化例は `docs/ja/tools.md`（英訳は `docs/en/tools.md`）を参照 |
| `task_channel_summary.py` | 登録チャンネル（`discord.channels`）の**前日**分の会話を LLM に要約させ、`channel_summaries` へ upsert する定期タスクツール（`ChannelSummaryTask`）。既定の cron は `0 2 * * *` で、暦日は `local_timezone()` の解決結果で数える（2 時実行で「当日」を対象にしない）。対象は `discord_channel_id` の付いた発言だけで、既存発言の穴埋めはしない。対象日の発言が無ければ upsert せず既存要約を残す。要約にはキャラ用システムプロンプトを使わず短い事実抽出プロンプトを使い、本文は `<channel_transcript>` タグで囲んだ「指示ではなくデータ」として渡す（タグ抜け出し文字列は事前に無害化）。1 チャンネルの失敗は ERROR ログのみで次へ進む。`schedule` / `llm_name` / `max_turns` / `max_transcript_chars` を YAML で上書きできる |

### extensions/ — 公式拡張パック (`src/lilla_core/extensions/`)
コアに同梱する `Extension` の実装。コア本体からは import されず（`tests/extensions/test_official_extension_pack.py`
が検査する）、`LILLA_EXTENSIONS` に import パスを並べたときだけ読み込まれる。各パッケージの
`__init__.py` はモジュールの import 時に `extension` を生成するため、サブモジュールを
トップレベルで import しない。利用者向けの説明は `docs/ja/google.md`（英訳 `docs/en/google.md`）。

| ファイル | 役割 |
|----------|------|
| `google_oauth/__init__.py` | `GoogleOAuthExtension`（`name = "google-oauth"`）と `extensions.google_oauth` のモデル `GoogleConfig`（`client_id` / `redirect_uri`。全項目に既定値）。申告するのは `config_model()`・`env_fields()`（`google_client_secret` → `GOOGLE_CLIENT_SECRET`）・`locale_dirs()`（`google_oauth/locales/`）・`dashboard_public_routes()`（`GET /oauth/google-oauth/callback`。接頭辞はコアの `DASHBOARD_PUBLIC_PREFIX` を参照せずリテラルで書く）。他の拡張に依存しないため、Calendar 抜きで単独でも載せられる |
| `google_oauth/client.py` | Google API クライアントの共通基底クラス `GoogleOAuthClient`。サブクラス（Calendar や利用者側の Tasks / Health など）は `CREDENTIAL_TYPE` と `SCOPES` だけを定義する。設定からの組み立て（`from_config()`。`get_section("google-oauth", GoogleConfig)` と `cfg.env.google_client_secret` を読む）・認証ヘッダ（`_auth_headers`）・JSON リクエスト（`_request_json`）・認可フロー開始（`start_authentication`）を共通提供する |
| `google_oauth/token.py` | アクセストークンの取得・リフレッシュ（`get_google_access_token`。invalid_grant は `ReauthenticationRequiredError`）と認可 URL の組み立て（`start_google_authentication`。`state` は `{credential_type}:{乱数}` で、種別ごとに `credentials` へ保存する）。スコープ定数は持たない |
| `google_oauth/callback.py` | `GET /oauth/google-oauth/callback`（認可コード → トークンの交換）。認証の外側に載る公開ルートのため、`state` から復元した種別の保存済み `state` と完全一致しない要求は 400 で弾き、一致した直後に `state` を消す。credential_type を固定の一覧では絞らない（どの種別が来るかは継承する拡張ごとに決まるため。発行していない `state` は照合で弾ける）。完了時の文言は `t("google-oauth.callback.completed")` |
| `google_oauth/locales/{ja,en}.yaml` | 上記の文言カタログ（トップレベルは `google-oauth` のみ） |
| `google_calendar/__init__.py` | `GoogleCalendarExtension`（`name = "google-calendar"`、`requires = ("google-oauth",)`）と `extensions.google_calendar` のモデル（`GoogleCalendarConfig` / `CalendarEntryConfig`。`calendars` のみ）。`required_env_fields()` で `google_client_secret` を要求し、`tool_roots()` で同梱の `tools/` をツール探索ルートへ足す（YAML は利用者の `${CONFIG_ROOT}/tools` のものを使い、`tool_config_roots()` での同梱はしない）。TZ 設定は持たず `ui.timezone` を使う |
| `google_calendar/client.py` | `GoogleCalendarClient`（`CREDENTIAL_TYPE = "google_calendar"`、スコープは `calendar.events` のみ）。複数カレンダーの予定の取得・マージ（`get_events`）と作成（`create_event`。時間指定の `timeZone` は `ui.timezone` から解決した IANA 名）。シングルトンは `get_google_calendar_client()` |
| `google_calendar/tools/llm_calendar_get.py` | （LLM ツール）予定の取得。`attendees` を除去し、登録済みカレンダーの ID を `friendly_name` に置き換える。カレンダー一覧は `get_section("google-calendar", GoogleCalendarConfig)` から読む |
| `google_calendar/tools/llm_calendar_create.py` | （LLM ツール）予定の作成。書き込み先は `calendars` に登録済みの `friendly_name` に限る。`build_schema` で `friendly_name` 一覧を `calendar` パラメータの enum へ注入する |

### testing/ — 拡張リポジトリ向けのテストヘルパー (`src/lilla_core/testing/`)
拡張を別リポジトリで開発するときに、どのリポジトリも書くことになる「自分の `Extension` を
登録し、設定を合成し、テストが終わったらプロセスの状態を元へ戻す」セットアップを肩代わりする
opt-in のヘルパー。**`__init__.py` は pytest を import しない**（pytest は
`[project.optional-dependencies.dev]` にしかないため、本番依存に混ぜない）。pytest に触るのは
`pytest_plugin.py` だけで、`pytest11` entry point による自動登録もしない（利用側の既存
conftest と黙って干渉しうるため）。利用側は自分のルート `conftest.py` に
`pytest_plugins = ["lilla_core.testing.pytest_plugin"]` と書いて読み込む。

| ファイル | 役割 |
|----------|------|
| `__init__.py` | pytest 非依存のヘルパー。`use_extensions(*extensions, config_root=None)` は登録内容・設定インスタンス（`core/config.py` の `_config_instance`。`get_config()` は未設定でも既定を返すためモジュール変数を直接退避する）・OS 変数名のレジストリ・`CONFIG_ROOT` を退避してから `set_extensions()` → `compose_config()` → `set_config()` を行い、合成した `AppConfig` を yield して、抜けるとき（例外時も）すべて元へ戻すコンテキストマネージャ。`write_minimal_lilla_yaml(directory, ...)` はコアが必須にしている項目（`discord.my_user_id` / `llm.default` と対応する `llm.providers.<名前>` / `ui.timezone: Asia/Tokyo`）だけの `lilla.yaml` を書き出し、`extra` があれば同じネストで深いマージをする（拡張が必須にしているセクション用。`{"extensions": {"habits": {...}}}` の形で渡す） |
| `pytest_plugin.py` | 上記を包む function scope の fixture 2 つ。`lilla_config_root` は `tmp_path` に最小構成の `lilla.yaml` を書いて `CONFIG_ROOT` を向け、`DISCORD_TOKEN` が無ければダミー値を入れてそのディレクトリを返す。`lilla_extensions` は `register(*extensions) -> AppConfig` を返し、内部で `use_extensions()` に入って teardown でまとめて抜ける（複数回呼んだら後入れ先出しで戻す） |

## tests/ — テスト
`tests/` 配下に各モジュールの単体テストを配置（pytest で実行）。`tests/repository/test_motor_client.py`
のようにサブディレクトリを切ることもある。`tests/conftest.py` が `sys.path` に `src/` とリポジトリ
ルートを追加し、`AppConfig.env` の必須フィールド用にダミーの環境変数（`DISCORD_TOKEN`）と、
YAML 由来の必須セクション（`discord.my_user_id`）を持つ `tests/fixtures/config_root/lilla.yaml` を
指す `CONFIG_ROOT` を設定する。

公式拡張パックのテストは `tests/extensions/<拡張のパッケージ名>/` に置く。`tests/` に `__init__.py` を置いて
いないため、テストモジュールのファイル名はディレクトリをまたいで一意にする
（`test_google_oauth_extension.py` のように拡張のパッケージ名を含める）。拡張単体のテストは他拡張の節を
含む共通 YAML に依存させず、`lilla_core.testing.write_minimal_lilla_yaml()` で自分の節だけを
書いた `tmp_path` を `use_extensions(config_root=...)` に渡す。

## 処理フロー概要
```
起動スクリプト（LILLA_EXTENSIONS を設定。未指定でも起動可能）
  → lilla_core/bot.py 起動
  ├→ load_extensions()（LILLA_EXTENSIONS の各モジュールを import し、module.extension を
  │    集めて衝突を検証 → config_model() / env_fields() の申告を compose_config() で
  │    AppConfig へ合成し set_config()。未指定でも合成は走り、素の AppConfig になる）
  ├→ 設定読み込み（get_config()）+ ログ設定（setup_logging）
  ├→ コマンド読み込み (commands.load_all_commands で commands/ と command_packages() を動的ロード)
  ├→ ツール読み込み (llm_tool_loader + task_tool_loader。探索ルートは tool_paths.resolve_tool_roots())
  ├→ main() 実行
  │    ├→ 各拡張の setup() をロード順に await
  │    │    （機械向け HTTP サーバー等、対話クライアントを増やす拡張の起動など）
  │    ├→ 観測用ダッシュボードを起動（handlers/dashboard_server.start_dashboard_server。
  │    │    組み込み 4 画面 + 拡張の dashboard_page() / dashboard_routes() /
  │    │    dashboard_public_routes() / dashboard_static_dir() を載せる）
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
       │    承認チャンネル（`discord.approval_channel_id`）の承認フローへ。承認後に command_handler が結果を配送する
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
| `setup` | `run_setup_hooks` | `main()` で `bot.start()` の前に順に await される非同期関数（HTTP サーバー等の起動など）。引数は `SetupContext`（frozen dataclass。`tools` / `llm_tools` / `bot` / `config`）1 つで、`run_setup_hooks` が 1 度だけ組み立てて全拡張へ同じインスタンスを渡す。フィールドの追加は非破壊、削除・改名は破壊的変更として扱う |
| `result_deliveries` | `get_result_delivery` | `!toolresult` が結果を届ける先を `client_type` ごとに差し替える |
| `client_prompt_providers` | `get_client_prompt_providers` | `client_type` ごとにシステムプロンプトへ追記する文字列を返すプロバイダの **リスト**（`{client_type: [provider, ...]}`）。加算式で、複数の拡張が同じ `client_type` に足せる。コアがロード順に連結し、`memory_manager._resolve_client_prompt()` が空でない戻り値を空行区切りで追記する（呼ぶたびに評価される） |
| `conversation_start_hooks` | `get_conversation_start_hooks` | `run_conversation` の冒頭で `client_type` ごとに呼ばれる非同期関数の **リスト**。加算式。各フックは `ConversationContext`（frozen dataclass。`client_type` / `client_state` / `discord_channel_id` / `llm_name`）1 つを受け取り、ロード順に await される。1 件の失敗は DEBUG ログのみで、後続のフックも会話本体も止めない |
| `tool_context_providers` | `get_tool_context_providers`（全件）/ `build_tool_context` | ツール実行 context へ注入する値を context キー名ごとに供給する。LLM ツールと task ツールの両方に届く。コアが注入するキー（`client_type` / `llm_tools` / `client_state` / `discord_client` / `now` / `params` / `call_tool` など）は予約済みで、同名を提供するとロード時に落ちる |
| `tool_roots` | `get_tool_roots` | `paths.tool_root` に足すツール探索ディレクトリ（ここに含めた時点でロード対象になり、別途の許可設定は要らない） |
| `tool_config_roots` | `get_tool_config_roots` / `tool_paths.resolve_tool_config_files` | 拡張が同梱する既定のツール YAML（`llm_*.yaml` / `task_*.yaml`）のディレクトリ。ローダーは「拡張の同梱分（ロード順）→ `config_root/tools`」の順に集め、同じ stem は `config_root/tools` 側が丸ごと上書きする。拡張どうしの同じ stem は fail-fast。`enabled: false` の YAML はロードしない（同梱ツールを止めるには `config_root/tools` に同名で置く）。`type: self` の `.py` が同梱ディレクトリに置かれうるため、`resolve_tool_dirs()` にも含める |
| `locale_dirs` | `get_locale_dirs(extensions=None)` / `ui.messages.validate_catalogs` | 拡張が同梱する UI 文言カタログ（`{locale}.yaml`）のディレクトリ。トップレベルのキーはその拡張の `name` ただ 1 つでなければならず、違反・コアのトップレベルキーとの衝突は登録時に `ValueError` で fail-fast する。1 つの拡張が複数返したらロード順に浅くマージする。存在しないディレクトリは WARNING で読み飛ばす（ロケールごとに 1 回） |
| `command_packages` | `get_command_packages` | `load_all_commands()` が追加で走査するパッケージ |
| `dashboard_page` | `get_dashboard_pages` | 観測用ダッシュボードへ足すタブ 1 つ（`DashboardPage(label, group)`。`group` は `main` / `admin`）。経路は申告せず `Extension.name` から導出する（`#/{name}` / `#/admin/{name}` / `/api/{name}` / `/oauth/{name}` / `/static/ext/{name}/`）。コアが返すのは導出済みの `DashboardPageEntry` |
| `dashboard_static_dir` | `get_dashboard_static_mounts` | ホストが `/static/ext/{name}/` に載せる静的ファイルのディレクトリ。タブを出すなら直下に `page.js` を置く（返さないとロード時に落ちる） |
| `dashboard_routes` | `get_dashboard_routes` | ホストがセッション認証の内側へ足す HTTP ルート（`DashboardRoute(method, path, handler)`）。パスは `/api/{name}` 配下のみ |
| `dashboard_public_routes` | `get_dashboard_public_routes` | ホストが認証の外側へ載せる公開ルート（OAuth の戻り先など）。パスは `/oauth/{name}` 配下のみで、`state` の検証は拡張側の責任 |
| `config_model` | `get_config_models`（全件。「導いた節名 → モデル」） | `extensions:` の下に足す YAML セクションのモデル 1 つ（足さないなら `None`）。節名は `name` のハイフンをアンダースコアにしたもの（`cfg.extensions.<節名>` で読む。コア確定の節と同名でもよい）。`BaseModel` サブクラス以外・数字始まりの `name` での申告はロード時に落とす |
| `env_fields` | `get_env_fields`（全件） | `EnvConfig` に足すフィールド名 → OS 環境変数名 |
| `required_env_fields` | `set_extensions` の検証 | 自分では提供しないが読む `EnvConfig` のフィールド名（コア確定のフィールドは常に利用可） |
| `required_tool_context_keys` | `set_extensions` の検証 | 自分では提供しないが、自分のツールが読むツール実行 context のキー名（`client_type` / `call_tool` などコアの共通キーは常に利用可） |
| `requires`（クラス属性） | `set_extensions` の検証 | 依存する拡張の `name` のタプル。未ロード、または自分より後ろに並んでいれば fail-fast |
| `api_version`（クラス属性） | `set_extensions` の検証 | 拡張が書かれた契約バージョン（既定は `EXTENSION_API_VERSION`。現在 2）。`SUPPORTED_EXTENSION_API_VERSIONS` に無い値、または整数以外は fail-fast。廃止したメソッド（`config_models` / `required_config_sections`）を定義している拡張も、版の宣言によらず fail-fast（`_REMOVED_EXTENSION_METHODS`） |

### 衝突は fail-fast
拡張どうしで以下が重複したら、静かな後勝ちにせずロード時に例外を投げる。

- `Extension.name`（未設定・空文字も落とす。加えて `^[a-z0-9][a-z0-9-]*$` に合わない形と、
  `RESERVED_EXTENSION_NAMES`（`api` / `oauth` / `static` / `admin` / `dashboard` / `auth` /
  `login` / `logout` / `setup` / `home` / `conversations` / `memos` / `logs`）の予約名も落とす。
  `name` はダッシュボードの URL・ハッシュ・静的ディレクトリ名へそのまま埋まるため）
- `env_fields()` のフィールド名（コア確定の名前との重複も落とす）。`config_model()` の節名は
  一意な `name` から導き、`name` はアンダースコアを含まないため衝突しない（`extensions:` の
  下に置かれるので、コア確定の節と同名でも構わない）
- ツール実行 context プロバイダのキー（コアが注入する共通キーとの重複も落とす）
- `result_deliveries` の `client_type`（`client_prompt_providers` / `conversation_start_hooks` は
  加算式で、同じ `client_type` に複数の拡張が足せる。値がリストでない場合だけ落とす）
- `command_packages` 経由で登録されるコマンド名（`register_command` が検出）
- 複数のツールルートに同じ名前のツールファイルがあるとき（`find_tool_file` が検出）
- 複数の拡張が同じ stem のツール YAML を同梱しているとき（`resolve_tool_config_files` が検出。
  `config_root/tools` の同名 YAML による上書きは衝突ではなく、利用者の設定が勝つ）
- 拡張の UI 文言カタログのトップレベルキーがその拡張の `name` と異なるとき、または拡張名が
  コアのカタログのトップレベルキー（`selftest` など）と同じとき（`set_extensions()` が
  `ui/messages.py` の `validate_catalogs()` で登録前に検出。拡張どうしの衝突は `name` の
  重複検査で防がれる）
- ダッシュボードのルート申告が自分の接頭辞（`/api/{name}` / `/oauth/{name}`）の外を指すとき、
  または `dashboard_page()` を返すのに `dashboard_static_dir()` を返していないとき
  （`page.js` が 404 になるため）

コア内蔵のデフォルトとの重複は衝突にしない。`client_type="discord"` のシステム
プロンプトは拡張が 1 つでも出していればそれら（連結）を使い、誰も出していなければ
コアの `discord_client_prompt` を使う。逆に `"discord"` の `result_deliveries` はコアが
配送を持つ予約キーで、拡張が登録するとロード時に落ちる。

### `on_message` の実行時例外
ある拡張の `on_message` が例外を投げたときは、その 1 通の処理をそこで打ち切る。
後続の拡張も通常の会話フローも動かさず、ERROR ログと `core/error_notify.py` の
エラー通知チャンネルへ出したうえで「処理済み」として扱う（プロセスは落とさない）。

### 起動順の規約
設定は `config_model()` / `env_fields()` の申告から合成するため、`LILLA_EXTENSIONS` の
並び順は設定に影響しない（ホスト設定モジュールを先頭に置く規約は不要になった）。順序が
効くのは `on_message` の連鎖・`setup()` の await 順・`tool_roots()` の探索順といった
「ロード順に処理するもの」だけ。コアが検証するのは `requires` で宣言された依存先が
自分より前に並んでいることまでで、自動で並べ替えはしない。並べたモジュールは同一
プロセスで動く **信頼コード** であり、サンドボックスではない。
