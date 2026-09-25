"""Google OAuth2 のトークン処理（取得・更新）と認可フロー開始。

`google-oauth` 拡張の中身で、アクセストークンの有効性判定・リフレッシュ・
認可 URL の組み立てだけを持つ。どの API をどのスコープで呼ぶかは各クライアント
（`GoogleOAuthClient` のサブクラス）の関心事のため、スコープ定数はここに置かない。
"""

from __future__ import annotations

import json
import logging
import secrets
from urllib.parse import urlencode

import aiohttp

from lilla_core.core.exceptions import ReauthenticationRequiredError
from lilla_core.core.http_util import send_http_request
from lilla_core.repository.credentials_repository import CredentialsRepository
from lilla_core.utils.oauth2_authorization_code_utils import (
    build_token_credentials,
    has_refresh_token,
    is_access_token_valid,
)


_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


async def get_google_access_token(
    credentials_repo: CredentialsRepository,
    client_id: str,
    client_secret: str,
    credential_type: str,
) -> str:
    """有効なアクセストークンを返す（共通ロジック）。

    `GoogleOAuthClient` のサブクラス（Google API の各クライアント）から呼び出される。
    有効な access_token が保存されていればそのまま返し、期限切れの場合は
    refresh_token で更新して MongoDB に保存する。リフレッシュトークンがない
    場合は ReauthenticationRequiredError を送出する。

    Parameters
    ----------
    credentials_repo : CredentialsRepository
        認証情報リポジトリ
    client_id : str
        Google OAuth クライアント ID
    client_secret : str
        Google OAuth クライアントシークレット
    credential_type : str
        認証情報の種別（"google_calendar" など）

    Returns
    -------
    str
        アクセストークン

    Raises
    ------
    ReauthenticationRequiredError
        リフレッシュトークンが存在しない、または更新に失敗した場合
    """
    creds = await credentials_repo.get_by_type(credential_type)

    # 有効なアクセストークンがある場合はそのまま返す
    if is_access_token_valid(creds):
        return creds["access_token"]

    if not has_refresh_token(creds):
        raise ReauthenticationRequiredError("No refresh token. Re-authentication is required.")

    return await _refresh_google_access_token(
        credentials_repo, client_id, client_secret, credential_type, creds
    )


async def _refresh_google_access_token(
    credentials_repo: CredentialsRepository,
    client_id: str,
    client_secret: str,
    credential_type: str,
    creds: dict,
) -> str:
    """リフレッシュトークンを使ってアクセストークンを更新する（共通の内部関数）。

    更新に成功したら新しいトークンを MongoDB に保存して返す。トークン
    エンドポイントが invalid_grant を返した場合（HTTP 4xx・JSON 両方）は
    ReauthenticationRequiredError に変換する。

    Parameters
    ----------
    credentials_repo : CredentialsRepository
        認証情報リポジトリ
    client_id : str
        Google OAuth クライアント ID
    client_secret : str
        Google OAuth クライアントシークレット
    credential_type : str
        認証情報の種別
    creds : dict
        現在の認証情報（refresh_token を含む）

    Returns
    -------
    str
        新しいアクセストークン

    Raises
    ------
    ReauthenticationRequiredError
        トークンの更新に失敗した場合
    """
    try:
        response_text = await send_http_request(
            _GOOGLE_TOKEN_URL,
            form_data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": creds["refresh_token"],
            },
        )
    except aiohttp.ClientResponseError as e:
        try:
            error_body = json.loads(e.message)
        except (json.JSONDecodeError, TypeError):
            error_body = {}
        if error_body.get("error") == "invalid_grant":
            raise ReauthenticationRequiredError(
                "Failed to refresh token (re-authentication required): invalid_grant"
            ) from e
        raise
    result = json.loads(response_text)

    if "error" in result:
        raise ReauthenticationRequiredError(
            f"Failed to refresh token (re-authentication required): {result['error']}"
        )

    new_creds = build_token_credentials(
        result, fallback_refresh_token=creds["refresh_token"]
    )
    await credentials_repo.upsert(credential_type, new_creds)

    return new_creds["access_token"]


async def start_google_authentication(
    credentials_repo: CredentialsRepository,
    client_id: str,
    redirect_uri: str,
    logger: logging.Logger,
    scopes: list[str],
    credential_type: str,
) -> str:
    """Google認証フローを開始し、認可URLを返す（共通ロジック）。

    `GoogleOAuthClient` のサブクラスから呼び出され、指定された credential_type と
    scopes で認証フローを開始する。state には credential_type を埋め込み、
    コールバック側で復元できるようにする。

    Parameters
    ----------
    credentials_repo : CredentialsRepository
        認証情報リポジトリ
    client_id : str
        Google OAuth クライアント ID
    redirect_uri : str
        リダイレクト URI
    logger : logging.Logger
        ロガー
    scopes : list[str]
        発行するスコープリスト
    credential_type : str
        認証情報の種別（"google_calendar" など）

    Returns
    -------
    str
        Google の認可 URL
    """
    random_token = secrets.token_urlsafe(32)
    state = f"{credential_type}:{random_token}"
    await credentials_repo.upsert(credential_type, {"state": state})

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }

    auth_url = f"{_GOOGLE_AUTH_URL}?{urlencode(params)}"
    logger.info("Started Google authentication flow: credential_type=%s state=%s", credential_type, state)
    return auth_url
