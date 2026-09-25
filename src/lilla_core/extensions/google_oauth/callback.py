"""Google OAuth2 認可コードフローのコールバック処理。

このハンドラは `GoogleOAuthExtension.dashboard_public_routes()` が
`GET /oauth/google-oauth/callback` として申告し、ダッシュボードが認証ミドルウェアの
**外側** に載せる。利用者のブラウザが Google から戻ってくる公開 GET なので、
セッション Cookie も Bearer トークンも要求できない。

誰でも叩けるエンドポイントであるため、「その要求が自分の始めたフローの戻りか」は
`state` の照合で確かめる（コアはここを検証しない。公開ルートの `state` 検証は
拡張側の責任という契約）。`state` は `start_google_authentication()` が
`{credential_type}:{推測不能な乱数}` の形で発行して credential_type ごとに保存した
ものなので、保存済みの値と完全一致しない要求はトークン交換まで進めずに 400 で弾き、
一致した直後に `state` を消して使い回しを防ぐ。

credential_type を固定の一覧で絞ることはしない。どの Google API の種別が来るかは
`GoogleOAuthClient` を継承する拡張ごとに決まり、この拡張は知らないため。
発行していない種別・値の `state` は保存済みの値と一致しないので、照合だけで弾ける。
"""
from __future__ import annotations

import json
import logging

from aiohttp import web

from lilla_core.core.http_util import send_http_request
from lilla_core.utils.oauth2_authorization_code_utils import build_token_credentials

logger = logging.getLogger(__name__)

_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


async def handle_google_callback(request: web.Request) -> web.Response:
    """Google OAuth2 認可コードフローのコールバックエンドポイント。

    `GoogleOAuthClient` を継承する全クライアントで共有する 1 本のエンドポイント。
    state に埋め込まれた credential_type を復元し、保存済みの state と一致したら
    その種別でアクセストークンとリフレッシュトークンを MongoDB に保存する。

    Args:
        request: aiohttp のリクエスト（`code` / `state` クエリを持つ）。

    Returns:
        認証結果を表す aiohttp のレスポンス。
    """
    from lilla_core.core.config import get_config, get_section
    from lilla_core.extensions.google_oauth import GoogleConfig
    from lilla_core.repository.credentials_repository import get_credentials_repo
    from lilla_core.ui.messages import t

    code = request.rel_url.query.get("code")
    state = request.rel_url.query.get("state")

    if not code or not state:
        return web.Response(status=400, text="Missing code or state parameter")

    credential_type, sep, _ = state.partition(":")
    if not sep or not credential_type:
        return web.Response(status=400, text="Invalid state format")

    repo = get_credentials_repo()
    creds = await repo.get_by_type(credential_type)

    if not creds or creds.get("state") != state:
        return web.Response(status=400, text="Invalid state parameter")

    await repo.upsert(credential_type, {"state": None})

    config = get_config()
    oauth = get_section("google-oauth", GoogleConfig, config)
    client_secret = config.env.google_client_secret
    if not oauth.client_id or not client_secret:
        logger.error("Google OAuth client_id or GOOGLE_CLIENT_SECRET is not configured")
        return web.Response(status=500, text="Google credentials not configured")

    response_text = await send_http_request(
        _GOOGLE_TOKEN_URL,
        form_data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": oauth.client_id,
            "client_secret": client_secret,
            "redirect_uri": oauth.redirect_uri,
        },
    )
    result = json.loads(response_text)

    if "error" in result:
        error_msg = result.get("error", "Unknown error")
        logger.error("Google token exchange error: %s", error_msg)
        return web.Response(status=500, text=f"Token exchange error: {error_msg}")

    await repo.upsert(credential_type, {
        **build_token_credentials(result),
        "scopes": result.get("scope", "").split(),
    })
    logger.info("Google authentication complete: credential_type=%s saved token", credential_type)

    return web.Response(text=t("google-oauth.callback.completed"), content_type="text/plain")
