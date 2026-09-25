"""Google OAuth2 の認可・トークン処理だけを受け持つ公式パック。

`LILLA_EXTENSIONS` に `lilla_core.extensions.google_oauth` として、それを `requires`
する拡張（`lilla_core.extensions.google_calendar` など）より前に並べて読み込む。
Google API を呼ぶ各クライアントはこの拡張が提供する `GoogleOAuthClient`
（`lilla_core.extensions.google_oauth.client`）を継承し、スコープと `CREDENTIAL_TYPE`
だけを自分で持つ。どの Google API を使うかはこの拡張の関心事ではないため、
Calendar を載せない構成でも、他の Google API 拡張のために OAuth だけを載せられる。

この拡張が申告する設定は OAuth 専用で、YAML の `extensions.google_oauth`
（`client_id` / `redirect_uri`。節名はコアが `name` から導く）と秘匿情報の
`env.google_client_secret` のみ。取得・作成対象のカレンダー一覧は
`extensions.google_calendar`（`google_calendar` 拡張が申告）に分けてある。

認可コードフローの戻り先（`GET /oauth/google-oauth/callback`）は、この拡張が
`dashboard_public_routes()` で申告する公開ルートとしてダッシュボードの認証
ミドルウェアの外側に載る（パスはコアの定数を参照せず `/oauth/{name}/callback` を
リテラルで書く。守るのはコアの定数ではなくこの契約そのもの）。利用者のブラウザが
Google から戻ってくる公開 GET なのでセッション Cookie は要求できず、代わりに
`state` の照合（`callback.py`）で「自分が始めたフローの戻りか」を確かめる。

モジュールの import 時に `extension` インスタンスを生成するため、ここでは
サブモジュール（`client` / `token` / `callback`）をトップレベルで import しない。
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from lilla_core.core.extension import DashboardRoute, Extension


class GoogleConfig(BaseModel):
    """lilla.yaml の `extensions.google_oauth` セクション（OAuth2 クライアントの設定）。

    Google API の各拡張は同じ Google Cloud のクライアントを共有するため、ここは
    API ごとに分けず 1 つだけ持つ（MongoDB 側の認証情報は `CREDENTIAL_TYPE` ごとに
    分かれたまま）。`client_secret` は秘匿情報のため `env_fields()` 経由で
    `cfg.env.google_client_secret` に入る。
    """

    client_id: str | None = None
    #: Google Cloud の「承認済みのリダイレクト URI」と一致させる値。既定はダッシュボード
    #: （`dashboard.port`）に載る公開ルートを指す。ホストとポートは運用に合わせて
    #: `lilla.yaml` で上書きする。
    redirect_uri: str = "http://localhost:8765/oauth/google-oauth/callback"


class GoogleOAuthExtension(Extension):
    """Google OAuth2 の設定と認可コードフローの戻り先を申告する拡張。"""

    name = "google-oauth"

    def config_model(self) -> type[BaseModel]:
        """OAuth2 用の YAML セクションのモデルを返す（節名は `extensions.google_oauth`）。"""
        return GoogleConfig

    def env_fields(self) -> dict[str, str]:
        """OAuth2 の秘匿情報を「フィールド名 -> OS 環境変数名」で返す。"""
        return {"google_client_secret": "GOOGLE_CLIENT_SECRET"}

    def locale_dirs(self) -> list[Path]:
        """認可完了時にブラウザへ返す文言のカタログ（`locales/`）を返す。"""
        return [Path(__file__).resolve().parent / "locales"]

    def dashboard_public_routes(self) -> list[DashboardRoute]:
        """認可コードフローの戻り先を、認証の外側に載せる公開ルートとして申告する。

        パスは `/oauth/{name}/callback`（＝`/oauth/google-oauth/callback`）。拡張が
        守る契約は「公開ルートは `/oauth/{name}` 配下」であって、コアの
        `DASHBOARD_PUBLIC_PREFIX` 配下ではないため、接頭辞はコアの定数を参照せず
        リテラルで書く（公開の対象パスが将来増えて定数の意味が変わっても、この
        拡張の経路が黙って動かないようにする）。

        ここは誰でも叩けるエンドポイントなので、要求が自分の始めたフローの戻りかは
        `callback.py` が `state` の照合で確かめる。ハンドラの import はメソッド内に
        置く（`extension` の生成はモジュールの import 時に走るため）。
        """
        from lilla_core.extensions.google_oauth.callback import handle_google_callback

        return [
            DashboardRoute("GET", f"/oauth/{self.name}/callback", handle_google_callback)
        ]


extension = GoogleOAuthExtension()
