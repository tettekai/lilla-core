# Changelog

このプロジェクトの主な変更点を記録します。
形式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) に、
バージョニングは [Semantic Versioning](https://semver.org/lang/ja/) に、
それぞれ準じています。

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
