# Changelog

このプロジェクトの主な変更点を記録します。
形式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) に、
バージョニングは [Semantic Versioning](https://semver.org/lang/ja/) に、
それぞれ準じています。

## [Unreleased]

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

- **BREAKING**: `lilla.yaml` の `discord:` セクションで、エラー通知・承認依頼の送信先チャンネルを
  名前ではなく ID で指定するようになった。`error_channel`（チャンネル名）は `error_channel_id`
  （チャンネル ID）へ、`approval_channel`（チャンネル名、既定 `lilla-approval`）は
  `approval_channel_id`（チャンネル ID、既定なし）へそれぞれ置き換え、旧キー名は読まなくなった
  （`extra="ignore"` のため YAML に残っていても無視される）。`core/error_notify.py` /
  `handlers/approval_flow.py` はいずれも `bot.get_channel()` による ID 解決のみを行い、
  複数ギルドに同名チャンネルがあっても意図しないギルドへ送信しないようにするための変更
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
