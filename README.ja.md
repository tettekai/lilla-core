<img src="https://raw.githubusercontent.com/tettekai/lilla-core/main/docs/lilla-logo.png" alt="Lilla" width="160" />

# lilla-core

[English](https://github.com/tettekai/lilla-core/blob/main/README.md)

Discord ボットとして常駐し、LLM（Ollama / OpenAI 互換）との tool_call ループ・コマンド
処理・返信を行う、AI エージェントの骨格となるコアランタイムです。

> **注意:** 会話履歴・切り替え中の LLM プロバイダー・セッションメモリは、チャンネルや
> ユーザーで分かれず **プロセス全体で 1 つ** を共有します。単一オーナーが 1 つの bot
> プロセスと会話する構成が前提で、複数ユーザー・マルチテナント向けではありません。

## 動作要件

- Python 3.12 以上
- MongoDB
- Discord Bot トークン

## インストールと起動

```bash
pip install lilla-core

export CONFIG_ROOT=/path/to/config   # lilla.yaml を置いたディレクトリ
export DISCORD_TOKEN=...
export MONGODB_URI=mongodb://...

python -m lilla_core.bot
```

`CONFIG_ROOT` のディレクトリには `lilla.yaml` を置きます。書き方と Discord 側の設定は
ドキュメントを参照してください。

## 拡張

`LILLA_EXTENSIONS` 環境変数にモジュールの import パスをカンマ区切りで並べると、起動時に
拡張として読み込みます。未設定ならコア単体の Discord bot として起動します。

拡張は `lilla_core.core.extension.Extension` をサブクラスし、必要なメソッドだけを
オーバーライドして、モジュールから `extension = MyExtension()` として公開します。

公式パックとして Google OAuth（`lilla_core.extensions.google_oauth`）と Google Calendar
（`lilla_core.extensions.google_calendar`）を同梱しています。どちらも `LILLA_EXTENSIONS` に
並べたときだけ読み込まれます。

## ドキュメント

- [ドキュメント目次](https://github.com/tettekai/lilla-core/blob/main/docs/ja/README.md)
- [Issues](https://github.com/tettekai/lilla-core/issues)

## ライセンス

MIT
