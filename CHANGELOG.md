# Changelog

このプロジェクトの主な変更点を記録します。
形式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) に、
バージョニングは [Semantic Versioning](https://semver.org/lang/ja/) に、
それぞれ準じています。

## [Unreleased]

### Added

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
