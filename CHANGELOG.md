# Changelog

このプロジェクトの主な変更点を記録します。
形式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) に、
バージョニングは [Semantic Versioning](https://semver.org/lang/ja/) に、
それぞれ準じています。

## [Unreleased]

### Added

- 観測用ダッシュボードの HTTP サーバーをコアが起動するようになった
  （`handlers/dashboard_server.py`）。`bot.py` の `main()` が拡張の `setup()` を
  await したあと、Discord へ接続する前に起こす（申告の集約が `load_extensions()` で
  済んでいること・拡張の起動が失敗したら観測窓も開かないこと、の 2 つが理由）。
  提供するのは初期設定 / ログイン / セッション Cookie による認証と、会話履歴・
  ユーザーメモ・ログの API、拡張のページを並べる `GET /api/dashboard/nav`、および
  #105 で入った拡張の申告の配線（`/static/ext/{name}/`（認証不要）・`/api/{name}`
  （Cookie 必須）・`/oauth/{name}`（認証の外側））。SPA（Alpine.js・ハッシュ
  ルーティング・拡張ページの `import()` + `mount()` / `unmount()`）は
  `src/lilla_core/dashboard/` にパッケージ同梱し、`locales/` と同じく wheel へ入る。
  拡張が 0 個でも組み込み 4 画面（Home / Conversations / Memos / Logs）で起動する
  （`Extension` の契約は変えていないので `EXTENSION_API_VERSION` は 1 のまま）
- コア確定の YAML セクション `dashboard:` を追加（`DashboardConfig`）。`host`
  （既定 `0.0.0.0`）・`port`（既定 `8765`）・`cookie_secure`（既定 `true`）の 3 つで、
  全項目に既定があるため `lilla.yaml` に節そのものが無くても起動する。
  **このポートは管理画面を開く。** パスワード未登録の間は `POST /api/setup` に
  先に到達した者が管理者パスワードを決められるブートストラップなので、公開
  ネットワークへ晒さないこと（前段でアクセス制御を掛けるか、`host` を
  `127.0.0.1` に絞る）。詳細は README と `SECURITY.md` を参照

### Changed

- **BREAKING**: 拡張の設定節名を `Extension.name` から導くようにし、`EXTENSION_API_VERSION` を
  **2** に上げた（#110。受け付けるのも 2 だけ）
  - `Extension.config_models() -> dict[str, type[BaseModel]]` を廃止し、
    `config_model() -> type[BaseModel] | None` に置き換えた。1 拡張が足せるモデルは 1 つで、
    節名は `name` のハイフンをアンダースコアに置き換えたもの（`google-oauth` →
    `extensions.google_oauth`。ハイフンの無い名前はそのまま）。節名を自分で書く API は無い。
    複数の設定を持つ拡張は 1 つのモデルの子としてまとめること
  - `Extension.required_config_sections()` を廃止した。他の拡張の節を読む依存は
    `requires`（拡張名）で表す。`required_env_fields()` / `required_tool_context_keys()` は残る
  - 廃止した 2 メソッドのどちらかを定義したままの拡張は、`api_version` を宣言していなくても
    代わりの経路を示してロード時に fail-fast する（黙って無視しない）
  - `config_model()` が `BaseModel` のサブクラス以外を返す拡張、および `name` が数字で始まり
    節名が識別子にならない拡張がモデルを申告した場合もロード時に fail-fast する
  - `core/config.py` に `extension_section_name(name)` を追加。`get_section()` は拡張の `name`
    （`get_section("google-oauth", GoogleConfig)`）でも節名でも引ける
  - `get_config_models()` は「導いた節名 -> モデル」を返す（`compose_config()` の引数の形は不変）
- **BREAKING**: 拡張が `config_models()` で申告した YAML セクションの置き場を、
  トップレベルからコア確定の `extensions:` の下へ移した（#109）。読み出しは
  `get_config().extensions.<節名>` で、トップレベルの `get_config().<節名>` は作らない。
  `lilla.yaml` も同じ形に書き換えること（`google:` → `extensions:` の下の `google:`）。
  `config_models()` の書き方（節名を自分で返す形）は変えていないので
  `EXTENSION_API_VERSION` は 1 のまま
  - 名前空間が別になったため、コア確定の節と同名のセクションを申告しても起動できる
    （これまでは予約名としてロード時に fail-fast していた）
  - `extensions:` の下に、ロードしていない拡張のキーがあれば起動時に落とす
    （払い残しの検出）。拡張が 0 個なら `extensions` は空
  - 申告した節をトップレベルに書いたままの場合も、移し忘れとして起動時に落とす
    （トップレベルの未知キーは無視されるため、既定値のまま気付かず動くのを防ぐ）。
    コア確定と同名のトップレベルキーはコアのものなので対象外
  - `get_section(name, model)` は `extensions:` の下だけを探すようになった。
    **コア確定の節（`get_section("ui", UiConfig)` など）は取れなくなった**ので、
    `get_config().ui` のように直接読むこと
  - `required_config_sections()` はコア確定の節名では満たされなくなった
    （コアの節は `extensions:` の下に無いため）。書いている場合は外すこと
  - `load_extensions()` を経ずに `get_config()` を呼んだとき（拡張をロードしない
    運用スクリプトなど）は、`extensions:` の中身を検証せずに捨て、コア確定の節だけを
    読む。どの拡張が載るか分からないためで、未知キーの検査は合成結果にだけ掛かる
- **コアの設計方針の線引きを更新した。** これまで「HTTP/ダッシュボードサーバーは
  コアに含めない」としていたが、観測用ダッシュボードはコアが所有することにした
  （見せる中身がすべてコアの状態のため）。用途特化のサーバー（機械向けの Bearer
  API・WebSocket クライアントなど）を拡張側に置く方針は変わらない。
  `dashboard` はコア確定のセクション名になったため、**同名の YAML セクションを
  申告している拡張はロード時に fail-fast する**（`config_models()` から外すこと）

### Added

- 拡張が観測用ダッシュボードへ差し込むための申告を `Extension` に追加。
  `dashboard_page()`（タブ 1 つ。`DashboardPage(label, group)` で `group` は
  `main` / `admin`）・`dashboard_static_dir()`（`/static/ext/{name}/` に載せる
  ディレクトリ。タブを出すなら直下に `page.js`）・`dashboard_routes()`（セッション
  認証の内側。パスは `/api/{name}` 配下のみ）・`dashboard_public_routes()`（認証の
  外側。パスは `/oauth/{name}` 配下のみ）の 4 つで、いずれも既定は「何も貢献しない」。
  経路の識別子は既存の `Extension.name` だけで、ハッシュ（`#/{name}` / `#/admin/{name}`）・
  API 接頭辞・公開コールバック・静的 URL・JS モジュール URL はコアが `name` から導出する
  （新しい ID 欄は作らない）。集約結果は `get_dashboard_pages()`（導出済みの
  `DashboardPageEntry`）・`get_dashboard_static_mounts()`・`get_dashboard_routes()` ・
  `get_dashboard_public_routes()` からロード順で読める。ルートは aiohttp の型ではなく
  コア独自の `DashboardRoute(method, path, handler)` で受け取り、`Extension` 契約を
  HTTP ライブラリのバージョンに縛らない。ダッシュボードの HTTP サーバー本体はコアには
  含まれず、申告を集めて配るところまでがコアの役目（メソッドの追加のみで非破壊。
  `EXTENSION_API_VERSION` は 1 のまま据え置き）

### Changed

- **`Extension.name` の形を検査するようになった。** `name` はダッシュボードの URL
  パス・ハッシュ・静的ディレクトリ名へそのまま埋まるため、`^[a-z0-9][a-z0-9-]*$` に
  合わない名前（大文字・アンダースコア・空白・`/`・`..` など）はロード時に `ValueError`
  で落ちる。加えて、組み込みの経路と衝突する名前（`api` / `oauth` / `static` / `admin` /
  `dashboard` / `auth` / `login` / `logout` / `setup` / `home` / `conversations` /
  `memos` / `logs`）を `RESERVED_EXTENSION_NAMES` として予約し、使った拡張は落とす。
  該当する `name` の拡張は改名が必要（既存の `google-oauth` / `google-calendar` /
  `lilla-agent` はいずれも影響を受けない）

## [0.4.1] - 2026-09-19

### Added

- 拡張が UI 文言カタログを同梱できる `Extension.locale_dirs()` を追加。ディレクトリに
  コアと同じ命名の `{locale}.yaml`（`ja.yaml` / `en.yaml` など）を置くと、
  `lilla_core.ui.messages` がコアのカタログへロード順に重ねて解決する。カタログの
  トップレベルのキーはその拡張の `name` ただ 1 つでなければならず（`name = "lilla-habits"` なら
  `t("lilla-habits.notify.title")`。コアが prefix を付けることはしない）、違反や拡張名とコアの
  トップレベルキーの衝突は、拡張の登録時（`set_extensions()`）に `ValueError` で fail-fast する
  （`t()` 自体は従来どおり例外を投げない）。1 つの拡張が複数のディレクトリを返した場合は、その
  拡張のノードをロード順に浅くマージする。存在しないディレクトリは WARNING を出して
  （ロケールごとに 1 回）読み飛ばし、壊れた YAML は ERROR ログを出してそのロケール分だけ空として扱う。
  `t()` の解決順（`ui.locale` → `ja` → キー名）は従来どおりで、`ja.yaml` しか同梱していない
  拡張でも `ui.locale: en` で例外にならない。`translations()` / `available_locales()` も
  合成後のカタログを対象にする（メソッドの追加のみで非破壊。`EXTENSION_API_VERSION` は据え置き）
- 拡張リポジトリ向けのテストヘルパー `lilla_core.testing` を追加。
  `use_extensions(*extensions, config_root=None)`（登録・設定合成・`set_config()` を行い、
  抜けるときに登録内容・設定インスタンス・`CONFIG_ROOT` を元へ戻すコンテキストマネージャ）と
  `write_minimal_lilla_yaml(directory, ...)`（コアが必須にしている項目だけの `lilla.yaml` を
  書き出す。`extra` で拡張が必須にしているセクションを深いマージで足せる）を提供する。
  `lilla_core.testing` 本体は pytest を import しないため、pytest の無い環境でも import できる
- pytest 向けの fixture を `lilla_core.testing.pytest_plugin` に追加（`lilla_config_root` /
  `lilla_extensions`）。`pytest11` entry point による自動登録はしないので、利用側は自分のルート
  `conftest.py` に `pytest_plugins = ["lilla_core.testing.pytest_plugin"]` と書いて opt-in する

- `Extension.tool_config_roots()` を追加。拡張が既定のツール YAML（`llm_*.yaml` / `task_*.yaml`）を
  同梱できるようになった。ローダーは「拡張の同梱分（ロード順）→ `${CONFIG_ROOT}/tools`」の順に
  YAML を集め、同じ stem は `${CONFIG_ROOT}/tools` 側が丸ごと上書きする（利用者の設定が常に勝つ。
  内容のマージはしない）。拡張どうしで同じ stem を同梱した場合は起動時に fail-fast する。
  同梱 YAML が `type: self` なら同梱ディレクトリの同名 `.py` を読む
- ツール YAML に `enabled: false` と書くとそのツールをロードしなくなった（同梱かどうかを問わない）。
  拡張が同梱したツールを止めるには、`${CONFIG_ROOT}/tools` に同じ stem で `enabled: false` の
  YAML を置く。`enabled` キーが無い、または `false` 以外の値なら従来どおりロードする

- ツール YAML の `type` に import パス（`.` 区切りのモジュール名。例:
  `type: lilla_google_calendar.tools.calendar_get`）を書けるようになった。`.` を含む `type` は
  ツールルートを探索する代わりに `importlib` で解決し、LLM ツール・task ツールの両方で使える。
  インストール済みパッケージ（PyPI 配布の拡張など）がツールを同梱するための経路で、通常の
  import のため相対 import が使え、ディレクトリの検査は行わない（インストール済みパッケージは
  `LILLA_EXTENSIONS` と同じ信頼レベル）。import に失敗した場合はファイルが見つからないときと
  同じく WARNING を出してそのツールだけスキップする。`.` を含まない `type` と `type: self` の
  挙動は従来どおり

- コア組み込みのサンプル LLM ツール `lilla_core.builtin_tools.llm_current_datetime` を追加。
  個人データ・外部サービスへの依存を持たない軽量なツールで、`${CONFIG_ROOT}/tools/` に
  `type: lilla_core.builtin_tools.llm_current_datetime` の YAML を置くと opt-in で有効化できる
  （コアは自動では読み込まない）。import パス指定でコア組み込みツールを使う実例として README に記載

- `lilla.yaml` の `discord.channels` に登録チャンネルのリストを追加。`name`（設定上の
  別名）・`channel_id`（snowflake 文字列）・`mention_optional`（既定 `false`）を並べると、
  `mention_optional: true` のチャンネルではオーナーのメンションなしの発言にも応答する。
  未設定・空リストなら受信動作は現行のまま（メンションまたは DM）で、`name` または
  `channel_id` の重複は起動時に fail-fast する
- `DiscordConfig.find_channel_by_id()` / `find_channel_by_name()` を追加。登録チャンネルの
  エントリを Discord のチャンネル ID（int / str）や設定上の別名（前後空白を除いた完全一致・
  大文字小文字は区別）から引ける
- 登録チャンネルの「部屋のノート」を持つ `channel_summaries` コレクションと
  `repository/channel_summary_repository.py` を追加。1 チャンネル 1 ドキュメント
  （`discord_channel_id` がユニーク）で、起動時に `init_collection()` される
- コア組み込みのタスクツール `lilla_core.builtin_tools.task_channel_summary` を追加。
  登録チャンネル（`discord.channels`）ごとに**前日**（`ui.timezone` の暦日）の会話を LLM に
  要約させ、`channel_summaries` へ upsert する。既定の cron は `0 2 * * *`。
  `${CONFIG_ROOT}/tools/task_channel_summary.yaml` に
  `type: lilla_core.builtin_tools.task_channel_summary` を置いた場合だけ有効になる opt-in で、
  `discord.channels` が空、または YAML が無ければ何もしない。対象は `discord_channel_id` の
  付いた発言だけ（既存発言の穴埋めはしない）で、対象日の発言が無ければ既存の要約を残す。
  要約にはキャラクター用のシステムプロンプトを使わず、短い事実抽出用のプロンプトを使い、
  本文は `<channel_transcript>` タグで囲んだ「指示ではなくデータ」として渡す。
  `schedule` / `llm_name` / `max_turns` / `max_transcript_chars` を YAML で上書きできる
- `ConversationRepository.load_by_channel_between()` を追加（チャンネルと期間で絞った
  会話履歴の取得）。あわせて `conversations` に `(discord_channel_id, time)` の複合
  インデックスを張るようにした
- コア組み込みの LLM ツール `lilla_core.builtin_tools.llm_conversation_get` を追加。
  会話履歴を期間（`datetime_range`）・キーワード（`query`。スペース区切りの AND）・
  発言者（`role`）・件数（`limit`。既定・上限とも 30 で、0 や負数は 1 へ丸める）で検索する。
  `${CONFIG_ROOT}/tools/` に `type: lilla_core.builtin_tools.llm_conversation_get` の
  YAML を置いた場合だけ有効になる opt-in（LLM へ見せるツール名は YAML のファイル名）。
  任意パラメータ `channel_name` に `discord.channels` の登録名を渡すと、その
  `discord_channel_id` の発言だけに絞り込む。名前は設定上の別名であり Discord の現在の
  チャンネル名ではない。突き合わせは前後空白を除いた完全一致（大文字小文字を区別）で、
  登録に無い名前は全件検索へ落とさずエラーを返す。省略時は従来どおり全チャンネル横断。
  会話履歴そのものはチャンネルで分離せず、`tags` の扱いも変えていない
- `ConversationRepository.search()` を追加（期間・キーワード・発言者・チャンネルを
  任意に重ねた会話履歴の検索）。キーワードは正規表現としてではなくエスケープした
  部分一致（大文字小文字を区別しない）として扱う

### Changed

- 登録チャンネルでの会話では、システムプロンプトに「今この登録チャンネルにいる」旨の
  短い一節を追記するようにした（未登録チャンネル・DM では追記しない）。会話履歴は
  従来どおり全チャンネル横断のまま
- `MemoryManager.build_system_prompt()` に `discord_channel_id` 引数を追加（既定 `None`。
  登録チャンネルの一節の解決に使う）。`run_conversation()` が受け取ったチャンネル ID を
  そのまま渡す
- Discord 経由のユーザー発言も、アシスタント返信と同様に `discord_channel_id` つきで
  会話履歴へ保存するようにした（既存レコードの補完は行わない）
- 登録チャンネルでの会話では、その部屋の要約（`channel_summaries`）があればシステム
  プロンプトへ `## Channel Note` として差し込むようにした。`summary_date` を併記し、
  本文は信頼しないコンテキストとして `<channel_note>` タグで囲む（未登録チャンネル・DM には
  出さない）。`load_conversation_history_with_timestamps()` の挙動は変えていない

### Fixed

- task ツールのトリガー種別を、`type` ではなく YAML のファイル名 stem から判定するように
  修正した。`type` に import パス（`type: some_package.tasks.daily_summary`）を書いた
  task ツールが `trigger: other` と判定され、`!runtask` から実行できなかった

## [0.4.0] - 2026-09-13

### Added

- `core/config.py` に `get_section(name, model, config=None)` を追加。拡張が
  `config_models()` で申告したセクション（コア確定のセクションも可）を、申告したモデルの
  型で取り出す。未申告の名前や、値がそのモデルのインスタンスでない場合は `ValueError`
- `Extension` 契約のバージョン `EXTENSION_API_VERSION`（現在 1）と、ロード時に受け付ける
  集合 `SUPPORTED_EXTENSION_API_VERSIONS` を `core/extension.py` に追加。拡張はクラス属性
  `api_version` で自分が書かれたバージョンを宣言でき（既定は現在のバージョン）、受け付けない
  値や整数以外を宣言した拡張は拡張名とバージョンを含むエラーでロード時に fail-fast する。
  契約の互換性ポリシー（何が非破壊で何が破壊的か）を README に明文化した
- `Extension.requires`（クラス属性。依存する拡張の `name` のタプル）を追加。依存先が
  ロードされていない、または `LILLA_EXTENSIONS` で自分より後ろに並んでいる場合はロード時に
  fail-fast する（コアは並べ替えない）。汎用の `validate()` フックは追加しない
- `Extension.required_env_fields()` / `required_tool_context_keys()` を追加。
  `required_config_sections()` と同じ形で、自分では提供しないが読む `EnvConfig` の
  フィールド名 / ツール実行 context のキー名を並べると、誰も提供しておらずコア確定の
  名前でもない場合にロード時に fail-fast する

### Changed

- task ツール（定期実行と `!runtask`）の実行 context にも、拡張の `tool_context_providers()`
  の値が入るようになった。これまでは LLM ツールだけに注入され、task ツールは `discord_client` /
  `now` / `llm_tools`（手動実行時は `params` も）の固定キーしか受け取れなかった。組み立ては
  `core/extension.py` の `build_tool_context()` に一本化し（`services/conversation_service.py`
  からの import は互換のため残す）、両経路で同じ注入モデルになる
- **BREAKING**: コアがツール実行 context へ注入するキー（`client_type` / `llm_tools` /
  `client_state` / `discord_channel_id` / `discord_client` / `now` / `params` / `call_tool` 等）を
  予約キーにし、`tool_context_providers()` で同名を提供する拡張はロード時に fail-fast する
  （これまではコアの注入で静かに上書きされていた）
- **BREAKING**: 会話開始フック（`Extension.conversation_start_hooks()`）の引数を、WebSocket
  クライアント集合（`ws_clients`）から `ConversationContext`（`client_type` / `client_state` /
  `discord_channel_id` / `llm_name`）1 つに変更した。あわせて `run_conversation()` の引数
  `ws_clients` を `client_state` に、ツール実行 context のキー `ws_clients` を `client_state` に
  改名した。コアは `client_state` の中身を解釈せず、クライアント拡張が渡した値をそのまま
  フックとツールへ届ける（WebSocket はコアの概念ではないため）
- **BREAKING**: `Extension.client_prompt_providers()` / `conversation_start_hooks()` の戻り値を
  「`client_type` → 関数 1 つ」から「`client_type` → 関数のリスト」に変更し、同じ `client_type`
  への登録を拡張どうしで排他にせず加算式にした。コアは全拡張分をロード順に連結し、プロンプトは
  空でない戻り値を空行区切りで追記、フックは順に await する（1 件の失敗は後続を止めない）。
  コアの参照 API も `get_client_prompt_providers()` / `get_conversation_start_hooks()`（複数形。
  未登録なら空リスト）に改めた。旧契約のまま関数 1 つを返す拡張はロード時に落ちる。
  `result_deliveries()` は従来どおり排他で、`"discord"` は予約のまま
- **BREAKING**: `Extension.setup()` の引数を位置引数 3 つ（`tools, llm_tools, bot`）から
  `SetupContext` 1 つに変更した。`ctx.tools` / `ctx.llm_tools` / `ctx.bot` で従来と同じ値を、
  `ctx.config` でプロセスの設定（`get_config()` と同じインスタンス）を参照できる。
  今後フィールドを足しても既存の拡張の `setup()` を壊さないための変更。旧シグネチャの拡張は
  起動時に `TypeError` で落ちる
- **BREAKING**: `paths.allowed_tool_paths`（ツール実行パスのホワイトリスト）を撤去した。
  ツールの `.py` は起動時に `paths.tool_root`・拡張の `tool_roots()`・`${CONFIG_ROOT}/tools`
  の中だけで解決され、実行時にファイルパスが新たに解決される経路が無いため、`tool_root` の
  設定ミスに対する重複した検査になっていた。また拡張の `tool_roots()` が自動で許可されず、
  pip で入れた拡張のツールが `site-packages` 配下として静かにスキップされていた。YAML に
  残っていても無視される（`PathsConfig` は既定で未知のキーを捨てる）。代わりに
  `loaders/tool_paths.py` の `resolve_tool_dirs()` / `is_within_tool_dirs()` が、解決後の
  ツールファイルが探索ルートか `${CONFIG_ROOT}/tools` の配下にあることを設定なしで検査する
  （`..` を含む `type` や外を指すシンボリックリンクは読み込まない）。信頼境界は `SECURITY.md`
  に明記した（`CONFIG_ROOT` と各ツールディレクトリは拡張モジュールと同じ信頼レベル）
- `loaders/script_loader.py` の `load_script_function` / `load_script_class` に `tool_dirs`
  引数を追加した。省略時は設定から導いたディレクトリで検査する
- **BREAKING**: `lilla.yaml` の `discord:` セクションで、エラー通知・承認依頼の送信先チャンネルを
  名前ではなく ID で指定するようになった。`error_channel`（チャンネル名）は `error_channel_id`
  （チャンネル ID）へ、`approval_channel`（チャンネル名、既定 `lilla-approval`）は
  `approval_channel_id`（チャンネル ID、既定なし）へそれぞれ置き換え、旧キー名は読まなくなった
  （`extra="ignore"` のため YAML に残っていても無視される）。`core/error_notify.py` /
  `handlers/approval_flow.py` はいずれも `bot.get_channel()` による ID 解決のみを行い、
  複数ギルドに同名チャンネルがあっても意図しないギルドへ送信しないようにするための変更

## [0.3.0] - 2026-09-12

### Added

- 拡張が申告した設定差分をコアが `AppConfig` へ合成するようになった。`Extension.config_models()`
  に「YAML セクション名 → セクションモデル」を、`Extension.env_fields()` に「`EnvConfig` の
  フィールド名 → OS 環境変数名」を返すと、`load_extensions()` が全拡張の申告を 1 つの Pydantic
  モデルへ組み、`get_config().<セクション名>` / `get_config().env.<フィールド名>` で型付きで
  読めるようになる。セクションはモデルが必須フィールドを持てば必須、全フィールドにデフォルトが
  あれば省略可能。`env_fields()` で足すフィールドの型は常に `str | None`（既定値 `None`）
- `Extension.required_config_sections()` を追加。自分では提供しないが読む YAML セクション名を
  並べると、誰も提供しておらずコア確定のセクションでもない場合にロード時 fail-fast する
  （メッセージに要求元の拡張名を含む）。拡張どうしの依存を自動解決する仕組みは持たない
- 同名の YAML セクション・同名の `EnvConfig` フィールドを 2 つの拡張が提供した場合、および
  コア確定の名前と重複した場合はロード時 fail-fast するようになった
- `lilla.yaml` の `ui` に `timezone` を追加。「人間側の今日 / いま」に使うタイムゾーンを
  IANA 名で指定する。未指定なら従来どおり OS のローカルタイムゾーンに従う。空文字や
  `ZoneInfo` が受け付けない名前は、ロケールと違ってフォールバックせず起動時に失敗する

### Changed

- **BREAKING**: ホストが `AppConfig` のサブクラスを手書きし、拡張モジュールの import 副作用で
  `set_config()` して差し替える方式を廃止した。`load_extensions()` が拡張の登録後に必ず設定を
  合成して `set_config()` するため、import 時に差し込んだインスタンスは上書きされる。設定の差分は
  `config_models()` / `env_fields()` から出すこと。あわせて `LILLA_EXTENSIONS` の先頭にホスト
  設定モジュールを置く規約が不要になった（並び順は設定の合成に影響しない）
- **BREAKING**: `llm:` を未記載のまま起動、または `providers` が空、`llm.default` に
  対応する provider が無い `lilla.yaml` では起動できなくなった（`AppConfig()` 構築時に
  `ValidationError`）。初メッセージ受信時まで気付けなかった設定ミスを起動時に検出する
- **BREAKING**: `utils/datetime_utils.py` の `to_jst_date` / `jst_day_end_utc` の基準を
  UTC+9 固定から `ui.timezone` の解決結果へ変更（関数名は互換のため維持）。`local_timezone` /
  `local_now` も同じ解決結果を返す。`JST` 定数だけは `ui.timezone` によらず UTC+9 のまま
- **BREAKING**: 定期タスクのスケジューラのタイムゾーンを `Asia/Tokyo` 固定から
  `ui.timezone` の解決結果へ変更。あわせて crontab 式にも同じタイムゾーンを明示的に渡す
  （`CronTrigger` をインスタンスで渡す場合、スケジューラ側の timezone 設定は
  引き継がれず、これまでは OS のローカルタイムゾーンで解釈されていた）
- 会話履歴の対象期間の「今日」（`services/memory_manager.py`）と、`today` などの相対日付の
  基準日（`tool_support/date_range.py`）が、OS のタイムゾーンではなく `ui.timezone` の
  解決結果に従うようになった
- 上記に伴い、日付や実行時刻を日本時間で固定したいホストは `lilla.yaml` に
  `ui.timezone: Asia/Tokyo` を明示すること（コンテナの OS が UTC の場合、未指定だと
  cron も「今日」も UTC になる）

### Fixed

- `LlmProviderConfig.type` を自由文字列から `Literal["ollama", "openai_compat"]` に変更し、
  typo を起動時の `ValidationError` として検出できるようにした
- `logging.yaml` に `handlers.mongodb` が無い場合に `setup_logging()` が `KeyError` で
  落ちていた問題を修正。mongodb ハンドラが無いときは接続情報を注入せず、
  `root.handlers` / 各 `loggers.*.handlers` からも `mongodb` への参照を除去する
- 不正な cron 式を持つタスクが 1 件あるだけで `on_ready` のスケジューラ登録処理全体が
  失敗していた問題を修正。不正なタスクだけ `WARNING` でスキップし、他の正当なタスクは
  登録したうえでスケジューラを起動する

## [0.2.0]

### Fixed

- `run_conversation` の tool_call ループが `finish_reason == "tool_calls"` のみで
  分岐していたため、OpenAI 互換の一部プロバイダが `tool_calls` 付きで
  `finish_reason: "stop"` を返すケースで呼び出しが無視され、空返信になっていたのを修正。
  判定を `tool_calls` の有無に変更
- tool_call の引数 JSON が壊れている場合に `json.loads` が例外を投げ、会話全体が
  失敗していたのを修正。例外を捕捉し、ツールエラーとして LLM に返すように変更

### Changed

- **BREAKING**: 拡張 API を `register_*` + `LILLA_EXTENSIONS_MODULE` から、Adapter 型の
  `Extension` 基底クラス（`core/extension.py`）+ `LILLA_EXTENSIONS` へ置き換え。
  拡張は `Extension` を継承し、モジュールから `extension` インスタンスを 1 つ export する。
  `LILLA_EXTENSIONS` はカンマ区切りで、1 プロセスに 0 個以上の拡張を読み込める
  （未設定・空ならコア単体起動）
- **BREAKING**: `core/extension_points.py` と `register_*` / `get_*` 系のモジュール関数を削除
- メッセージフックはロード順の連鎖になり、`on_message` が `True` を返した時点で以降を止める。
  例外時はその 1 通の処理を打ち切り、ERROR ログとエラー通知チャンネルへ出す
- 同名コマンドの二重登録は、警告つきの後勝ちから fail-fast へ変更
  （同じハンドラの再登録は許容する）
- ツールの探索ルートを複数持てるようにし、`loaders/tool_paths.py` に解決を集約。
  ローダーが import 時に設定を束縛しないよう修正（`llm_tool_loader` / `task_tool_loader` /
  `script_loader`）

### Added

- `Extension.tool_roots()`: `paths.tool_root` に足すツール探索ディレクトリ
  （`allowed_tool_paths` のホワイトリストは自動で広げない）
- `Extension.command_packages()`: `load_all_commands()` が追加で走査するパッケージ
- 拡張どうしの貢献キー衝突（`name` / ツール context キー / `client_type` / コマンド名 /
  複数ルートの同名ツールファイル）をロード時に fail-fast
- `client_type="discord"` のシステムプロンプトをコア内蔵のデフォルトとして保持
  （拡張が出していればそちらを優先）

## [0.1.0]

初回公開版。

### Added

- Discord bot として単体で起動できるコアランタイム（`lilla_core.bot`）
- Ollama（WakeOnLAN 対応）/ OpenAI 互換 API の複数 LLM プロバイダ切り替え
- tool_call ループと、会話履歴・ユーザーメモ・セッションメモリの管理
- `!` コマンドのプラグイン的な追加の仕組み（`commands/` にファイルを置くだけ）
- APScheduler による定期タスク実行（`task_*.yaml`）
- オーナー以外からのコマンド実行に対する承認フロー（Discord 上で承認/拒否）
- 7 種類の拡張ポイント（`core/extension_points.py`）によるコア非改変でのカスタマイズ
- Pydantic ベースの設定管理（`AppConfig`）と `.env` / `lilla.yaml` の分離
- Discord に見せる文言のロケールカタログ（`ja` / `en`）
- pytest による単体テスト一式
