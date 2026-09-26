# lilla-core ドキュメント

lilla-core の利用者向けドキュメントの目次です。日本語版が正で、
[英語版](../en/README.md) はその翻訳です。

## はじめに

- [概要と設計方針](overview.md) — コアに入れるもの・入れないもの、制約、主な機能
- [インストールと起動](getting-started.md) — 起動手順、`lilla.yaml`、秘匿情報、拡張の読み込み
- [Discord ボットのセットアップ](discord-bot-setup.md) — Intent と招待時の権限

## 機能

- [登録チャンネル](channels.md) — `discord.channels`
- [部屋のノート（深夜要約）](channel-notes.md)
- [会話履歴の部屋名検索](history-search.md)
- [タイムゾーン](timezone.md) — `ui.timezone`
- [LLM プロバイダーの回しごと選択](llm-resolver.md) — `llm.providers` の `type: resolver`
- [観測用ダッシュボード](dashboard.md) — 公開面への注意を含む
- [ツール契約](tools.md) — LLM ツール・task ツール・組み込みツール
- [Google OAuth / Google Calendar（公式拡張パック）](google.md) — `lilla_core.extensions` の同梱拡張

## 拡張

- [拡張の基本](extensions.md) — メソッド表、衝突ルール、ロケールカタログ、依存、互換性
- [設定の合成](extension-config.md) — `config_model()` / `env_fields()` / `get_section()`
- [ダッシュボードへの差し込み](extension-dashboard.md)
- [拡張のテストの書き方](extension-testing.md) — `lilla_core.testing`

## リポジトリ

- [ディレクトリ構成（概要）](directory-layout.md)
- [コントリビュート](../../CONTRIBUTING.md)
- [変更履歴](../../CHANGELOG.md)
- [セキュリティポリシー](../../SECURITY.md)
