"""コアに同梱する公式拡張パック（`Extension` を実装した拡張の集まり）。

ここに置く拡張は外部の拡張と同じ `Extension` 契約だけで書かれており、コア本体から
import されることはない。有効化は利用者が `LILLA_EXTENSIONS` に import パス
（`lilla_core.extensions.google_oauth` など）を並べたときだけで、未指定なら
今までどおりコア単体で起動する。

- `google_oauth`（`name = "google-oauth"`）: Google OAuth2 のトークン処理・認可フロー・
  コールバック。他の Google API 拡張が継承する `GoogleOAuthClient` を提供する
- `google_calendar`（`name = "google-calendar"`、`requires = ("google-oauth",)`）:
  Google Calendar のクライアントと LLM ツール
"""
