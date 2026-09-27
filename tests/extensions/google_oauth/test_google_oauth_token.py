"""`lilla_core.extensions.google_oauth.token` の共通トークン管理ロジックのテスト。"""
from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from lilla_core.extensions.google_oauth import token as google_token
from lilla_core.core.exceptions import ReauthenticationRequiredError


def _make_repo(creds: dict | None) -> MagicMock:
    """get_by_type / upsert を持つ CredentialsRepository のモックを生成する。"""
    repo = MagicMock()
    repo.get_by_type = AsyncMock(return_value=creds)
    repo.upsert = AsyncMock()
    return repo


async def _call(repo) -> str:
    """共通の引数で get_google_access_token を呼び出すヘルパー。"""
    return await google_token.get_google_access_token(
        repo, "client-id", "client-secret", "google_sample"
    )


class TestGetGoogleAccessToken:
    """`get_google_access_token()` の分岐（キャッシュ利用・更新・再認証）。"""

    async def test_returns_cached_token_when_valid(self, monkeypatch) -> None:
        """有効期限内のアクセストークンはそのまま返し、HTTP リクエストしない。"""
        repo = _make_repo({
            "access_token": "valid-token",
            "access_token_expires_at": time.time() + 3600,
            "refresh_token": "refresh",
        })
        send_mock = AsyncMock()
        monkeypatch.setattr(google_token, "send_http_request", send_mock)

        token = await _call(repo)

        assert token == "valid-token"
        send_mock.assert_not_called()
        repo.upsert.assert_not_called()

    async def test_raises_when_no_refresh_token(self, monkeypatch) -> None:
        """リフレッシュトークンがない場合は再認証エラーを送出する。"""
        repo = _make_repo({"access_token": "expired", "access_token_expires_at": 0})
        monkeypatch.setattr(google_token, "send_http_request", AsyncMock())

        with pytest.raises(ReauthenticationRequiredError):
            await _call(repo)

    async def test_refreshes_and_persists_new_token(self, monkeypatch) -> None:
        """期限切れの場合は refresh_token で更新し、新トークンを保存して返す。"""
        repo = _make_repo({
            "access_token": "expired",
            "access_token_expires_at": time.time() - 1,
            "refresh_token": "refresh",
        })
        send_mock = AsyncMock(return_value=json.dumps({
            "access_token": "new-token",
            "expires_in": 3600,
        }))
        monkeypatch.setattr(google_token, "send_http_request", send_mock)

        token = await _call(repo)

        assert token == "new-token"
        send_mock.assert_awaited_once()
        repo.upsert.assert_awaited_once()
        saved_type, saved_doc = repo.upsert.await_args.args
        assert saved_type == "google_sample"
        assert saved_doc["access_token"] == "new-token"
        # refresh_token がレスポンスに含まれない場合は既存値を引き継ぐ
        assert saved_doc["refresh_token"] == "refresh"

    async def test_invalid_grant_http_error_becomes_reauth(self, monkeypatch) -> None:
        """トークンエンドポイントが invalid_grant を 4xx で返したら再認証エラーに変換する。

        どのクライアント（`GoogleOAuthClient` のサブクラス）から呼ばれても同じ
        扱いになることを担保する。
        """
        repo = _make_repo({
            "access_token": "expired",
            "access_token_expires_at": 0,
            "refresh_token": "refresh",
        })
        err = aiohttp.ClientResponseError(
            MagicMock(), (), status=400, message=json.dumps({"error": "invalid_grant"})
        )
        monkeypatch.setattr(google_token, "send_http_request", AsyncMock(side_effect=err))

        with pytest.raises(ReauthenticationRequiredError):
            await _call(repo)
        repo.upsert.assert_not_called()

    async def test_error_in_json_body_becomes_reauth(self, monkeypatch) -> None:
        """200 でも本文に error が含まれる場合は再認証エラーを送出する。"""
        repo = _make_repo({
            "access_token": "expired",
            "access_token_expires_at": 0,
            "refresh_token": "refresh",
        })
        send_mock = AsyncMock(return_value=json.dumps({"error": "invalid_grant"}))
        monkeypatch.setattr(google_token, "send_http_request", send_mock)

        with pytest.raises(ReauthenticationRequiredError):
            await _call(repo)
        repo.upsert.assert_not_called()


class TestStartGoogleAuthentication:
    """`start_google_authentication()` が組み立てる state と認可 URL。"""

    async def test_state_is_prefixed_with_credential_type(self) -> None:
        """state は `{credential_type}:{乱数}` の形で保存される。"""
        repo = _make_repo(None)

        await google_token.start_google_authentication(
            repo,
            "client-id",
            "http://localhost/google-callback",
            MagicMock(),
            scopes=["https://www.googleapis.com/auth/tasks"],
            credential_type="google_sample",
        )

        saved_type, saved_doc = repo.upsert.await_args.args
        assert saved_type == "google_sample"
        assert saved_doc["state"].startswith("google_sample:")

    async def test_auth_url_carries_scopes_and_offline_access(self) -> None:
        """認可 URL に scope・access_type=offline・redirect_uri が載る。"""
        repo = _make_repo(None)

        url = await google_token.start_google_authentication(
            repo,
            "client-id",
            "http://localhost/google-callback",
            MagicMock(),
            scopes=["scope-a", "scope-b"],
            credential_type="google_calendar",
        )

        assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
        assert "scope=scope-a+scope-b" in url
        assert "access_type=offline" in url
        assert "redirect_uri=http%3A%2F%2Flocalhost%2Fgoogle-callback" in url
