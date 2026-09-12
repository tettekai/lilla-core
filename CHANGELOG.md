# Changelog

このプロジェクトの主な変更点を記録します。
形式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) に、
バージョニングは [Semantic Versioning](https://semver.org/lang/ja/) に、
それぞれ準じています。

## [Unreleased]

### Added

- `lilla.yaml` の `ui` に `timezone` を追加。「人間側の今日 / いま」に使うタイムゾーンを
  IANA 名で指定する。未指定なら従来どおり OS のローカルタイムゾーンに従う。空文字や
  `ZoneInfo` が受け付けない名前は、ロケールと違ってフォールバックせず起動時に失敗する

### Changed

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

## [0.2.0]

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
