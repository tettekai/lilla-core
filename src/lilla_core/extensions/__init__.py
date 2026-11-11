"""コアに同梱する公式拡張パック（`Extension` を実装した拡張の集まり）。

ここに置く拡張は外部の拡張と同じ `Extension` 契約だけで書かれており、コア本体から
import されることはない。有効化は利用者が `LILLA_EXTENSIONS` に import パス
（`lilla_core.extensions.google_oauth` など）を並べたときだけで、未指定なら
今までどおりコア単体（と標準機能の `notes`）で起動する。

- `google_oauth`（`name = "google-oauth"`）: Google OAuth2 のトークン処理・認可フロー・
  コールバック。他の Google API 拡張が継承する `GoogleOAuthClient` を提供する
- `google_calendar`（`name = "google-calendar"`、`requires = ("google-oauth",)`）:
  Google Calendar のクライアントと LLM ツール
- `google_drive`（`name = "google-drive"`、`requires = ("google-oauth",)`）:
  Google Drive のテキストファイルの読み書きと Google Sheets の読み取りのクライアントと
  LLM ツール（フォルダのホワイトリストで範囲を絞る）
- `files`（`name = "files"`）: ローカルのディレクトリのホワイトリスト内のテキストファイルの読み書き

`notes`（`name = "notes"`。MongoDB のノート置き場）も同じ場所に置いているが、これは公式拡張パックの
拡張ではなく標準機能で、`load_extensions()` が `LILLA_EXTENSIONS` によらず常に読み込む
（`core/extension.py` の `BUILTIN_EXTENSION_MODULES`。コア本体は import パスを文字列で持つだけで、
モジュールを直接 import しない）。
"""
