"""Google OAuth2 API クライアントの共通基底クラス。

`google-oauth` 拡張の中身で、トークンの取得・更新と認可フローの開始という
OAuth2 側の処理だけを持つ。個々の API のクライアント（Calendar は
`lilla_core.extensions.google_calendar.client`。他の Google API は利用者側の拡張）は
`CREDENTIAL_TYPE` と `SCOPES` を自分で定義して本クラスを継承する。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Self

from lilla_core.core.http_util import send_http_request
from lilla_core.repository.credentials_repository import CredentialsRepository
from . import token as google_token


class GoogleOAuthClient:
    """Google OAuth2 を用いる API クライアントの共通基底クラス。

    OAuth2 トークンの取得・更新（``_get_access_token``）と認証フロー開始
    （``start_authentication``）という、Google API の各クライアントで共通する
    処理を提供する。サブクラスは ``CREDENTIAL_TYPE``
    と ``SCOPES`` クラス変数を定義するだけでよい。
    """

    #: 認証情報の種別（"google_calendar" など。API ごとに一意にする）。
    CREDENTIAL_TYPE: str = ""
    #: 認証フローで発行するスコープリスト。
    SCOPES: list[str] = []

    def __init__(
        self,
        credentials_repository: CredentialsRepository,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ) -> None:
        """OAuth クライアントの認証情報を保持する。

        Args:
            credentials_repository: 認証情報リポジトリ。
            client_id: Google OAuth クライアント ID。
            client_secret: Google OAuth クライアントシークレット。
            redirect_uri: リダイレクト URI。
        """
        self._repo = credentials_repository
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri

    @classmethod
    def from_config(cls) -> Self:
        """アプリ設定と共有リポジトリからクライアントを生成する。

        Google OAuth の client_id / client_secret / redirect_uri を設定から
        読み込み、共有の認証情報リポジトリを注入して呼び出したサブクラスの
        インスタンスを返す。各クライアントのシングルトンファクトリが共通で使う。

        Returns:
            呼び出したサブクラス（``cls``）のインスタンス。
        """
        from lilla_core.core.config import get_config, get_section
        from lilla_core.extensions.google_oauth import GoogleConfig
        from lilla_core.repository.credentials_repository import get_credentials_repo

        config = get_config()
        oauth = get_section("google-oauth", GoogleConfig, config)
        return cls(
            get_credentials_repo(),
            oauth.client_id,
            config.env.google_client_secret,
            oauth.redirect_uri,
        )

    async def _get_access_token(self) -> str:
        """有効なアクセストークンを返す（`google_oauth.token` の共通関数を使用）。"""
        return await google_token.get_google_access_token(
            self._repo,
            self._client_id,
            self._client_secret,
            self.CREDENTIAL_TYPE,
        )

    async def _auth_headers(self, *, json_body: bool = False) -> dict[str, str]:
        """API リクエスト用の認証ヘッダを組み立てる。

        有効なアクセストークンを取得し、``Authorization: Bearer <token>``
        ヘッダを返す。Google API の各クライアントで共通のヘッダ生成。

        Args:
            json_body: True の場合、JSON ボディ送信用に
                ``Content-Type: application/json`` を併せて付与する。

        Returns:
            送信リクエストに渡す HTTP ヘッダの dict。
        """
        access_token = await self._get_access_token()
        headers = {"Authorization": f"Bearer {access_token}"}
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    async def _request_json(
        self,
        url: str,
        *,
        method: str = "GET",
        params: dict[str, str] | None = None,
        data: dict[str, Any] | None = None,
        json_body: bool = False,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """認証ヘッダ付きで HTTP リクエストを送り、レスポンスを JSON として返す。

        認証ヘッダの取得・リクエスト送信・レスポンスの JSON パースという、
        Google API の各クライアントで共通の定型処理。

        Args:
            url: リクエスト先 URL。
            method: HTTP メソッド（既定は ``"GET"``）。
            params: クエリパラメータ。
            data: JSON ボディとして送るデータ（POST / PUT / PATCH 用）。
            json_body: True の場合、認証ヘッダに ``Content-Type: application/json``
                を付与する。``headers`` を明示指定した場合は無視される。
            headers: 使用する HTTP ヘッダ。省略時は ``_auth_headers`` で取得する。
                ループ内で同じヘッダを使い回したい呼び出し元は、事前に取得した
                ヘッダを渡してトークン再取得を避けられる。

        Returns:
            レスポンスボディをパースした JSON（dict / list など）。

        Raises:
            ReauthenticationRequiredError: トークンの取得または更新に失敗した場合。
        """
        if headers is None:
            headers = await self._auth_headers(json_body=json_body)
        response_text = await send_http_request(
            url,
            method=method,
            params=params,
            data=data,
            headers=headers,
        )
        return json.loads(response_text)

    async def start_authentication(self) -> str:
        """Google 認証フローを開始し、認可 URL を返す（`google_oauth.token` の共通関数を使用）。"""
        # サブクラスのモジュール名でロガーを解決し、各モジュールの
        # logging.getLogger(__name__) と同じログ出力先を維持する。
        logger = logging.getLogger(type(self).__module__)
        return await google_token.start_google_authentication(
            self._repo,
            self._client_id,
            self._redirect_uri,
            logger,
            scopes=self.SCOPES,
            credential_type=self.CREDENTIAL_TYPE,
        )
