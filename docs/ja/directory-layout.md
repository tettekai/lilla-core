# ディレクトリ構成（概要）

```
src/lilla_core/
├── bot.py                # Discord bot エントリポイント
├── bot_client.py         # commands.Bot インスタンスの共有モジュール
├── core/                 # 設定管理・Extension 基底クラス・共通ユーティリティ
├── commands/             # `!コマンド名` の実装（1 コマンド 1 ファイル）
├── handlers/             # Discord イベント/コマンドのディスパッチ、定期タスク管理、ダッシュボードサーバー
├── services/             # tool_call ループ・会話履歴・システムプロンプト構築など
├── dashboard/            # 観測用ダッシュボードの SPA（静的ファイル）
├── ui/ locales/          # Discord に見せる文言のカタログ（ja / en）
├── utils/                # 日付・パス・ハッシュ等の共通ユーティリティ
├── loaders/              # ツール（llm_*.yaml / task_*.yaml）の動的ロード
├── api/                  # LLM クライアント（Ollama / OpenAI 互換）
├── repository/           # MongoDB へのデータ永続化
├── builtin_tools/        # コア組み込みツール（opt-in）
├── tool_support/         # ツール実装向けの opt-in ヘルパー群
└── testing/              # 拡張リポジトリ向けのテストヘルパー（opt-in）
tests/                    # pytest による単体テスト
config.example/           # lilla.yaml / logging.yaml のサンプル
docs/                     # ドキュメント（ja が正、en は翻訳）
```

各ファイルの役割や詳細な設計は [`CLAUDE.md`](../../CLAUDE.md) にまとめています。
