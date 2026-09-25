"""`lilla_core.extensions.google_oauth.client.GoogleOAuthClient` のテスト。"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlparse

import pytest

from lilla_core.extensions import google_oauth
from lilla_core.extensions.google_oauth import client as client_module
from lilla_core.extensions.google_oauth.client import GoogleOAuthClient


class _SampleClient(GoogleOAuthClient):
    """テスト用のサブクラス（利用者側の Google API 拡張に相当）。"""

    CREDENTIAL_TYPE = "google_sample"
    SCOPES = ["scope-a", "scope-b"]


def _make_repo(creds: dict | None = None) -> MagicMock:
    """get_by_type / upsert を持つ CredentialsRepository のモックを生成する。"""
    repo = MagicMock()
    repo.get_by_type = AsyncMock(return_value=creds)
    repo.upsert = AsyncMock()
    return repo


def _make_client(repo: MagicMock | None = None) -> _SampleClient:
    """固定の認証情報でサブクラスのインスタンスを生成する。"""
    return _SampleClient(
        repo or _make_repo(), "client-id", "client-secret", "http://localhost/cb"
    )


class TestFromConfig:
    """設定からの組み立て。"""

    def test_builds_the_subclass_from_the_composed_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`extensions.google_oauth` と `env.google_client_secret` から呼び出し側の型で組み立てる。"""
        from lilla_core.repository import credentials_repository
        from lilla_core.testing import use_extensions, write_minimal_lilla_yaml

        repo = _make_repo()
        monkeypatch.setattr(credentials_repository, "get_credentials_repo", lambda: repo)
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "gc-secret")
        write_minimal_lilla_yaml(
            tmp_path,
            extra={
                "extensions": {
                    "google_oauth": {"client_id": "gc-id", "redirect_uri": "http://x/cb"}
                }
            },
        )

        with use_extensions(google_oauth.extension, config_root=tmp_path):
            client = _SampleClient.from_config()

        assert isinstance(client, _SampleClient)
        assert client._repo is repo
        assert client._client_id == "gc-id"
        assert client._client_secret == "gc-secret"
        assert client._redirect_uri == "http://x/cb"


class TestAuthHeaders:
    """認証ヘッダの組み立て。"""

    async def test_uses_the_subclass_credential_type(self, monkeypatch) -> None:
        """トークンはサブクラスの `CREDENTIAL_TYPE` で取得する。"""
        get_token = AsyncMock(return_value="tok")
        monkeypatch.setattr(client_module.google_token, "get_google_access_token", get_token)
        repo = _make_repo()

        headers = await _make_client(repo)._auth_headers()

        assert headers == {"Authorization": "Bearer tok"}
        get_token.assert_awaited_once_with(repo, "client-id", "client-secret", "google_sample")

    async def test_adds_content_type_for_json_body(self, monkeypatch) -> None:
        """`json_body=True` なら Content-Type を付ける。"""
        monkeypatch.setattr(
            client_module.google_token, "get_google_access_token", AsyncMock(return_value="tok")
        )

        headers = await _make_client()._auth_headers(json_body=True)

        assert headers["Content-Type"] == "application/json"


class TestRequestJson:
    """JSON リクエストの定型処理。"""

    async def test_sends_with_auth_headers_and_parses_json(self, monkeypatch) -> None:
        """認証ヘッダを付けて送り、本文を JSON として返す。"""
        monkeypatch.setattr(
            client_module.google_token, "get_google_access_token", AsyncMock(return_value="tok")
        )
        send = AsyncMock(return_value=json.dumps({"items": [1]}))
        monkeypatch.setattr(client_module, "send_http_request", send)

        result = await _make_client()._request_json(
            "https://example.com/api", method="POST", data={"a": 1}, json_body=True
        )

        assert result == {"items": [1]}
        kwargs = send.await_args.kwargs
        assert kwargs["method"] == "POST"
        assert kwargs["data"] == {"a": 1}
        assert kwargs["headers"]["Authorization"] == "Bearer tok"
        assert kwargs["headers"]["Content-Type"] == "application/json"

    async def test_reuses_given_headers_without_fetching_token(self, monkeypatch) -> None:
        """ヘッダを渡せばトークンを取り直さない。"""
        get_token = AsyncMock()
        monkeypatch.setattr(client_module.google_token, "get_google_access_token", get_token)
        send = AsyncMock(return_value="{}")
        monkeypatch.setattr(client_module, "send_http_request", send)

        await _make_client()._request_json("https://example.com", headers={"X": "1"})

        get_token.assert_not_awaited()
        assert send.await_args.kwargs["headers"] == {"X": "1"}


class TestStartAuthentication:
    """認可フローの開始。"""

    async def test_uses_the_subclass_scopes_and_credential_type(self) -> None:
        """認可 URL にサブクラスのスコープを載せ、state を種別ごとに保存する。"""
        repo = _make_repo()

        url = await _make_client(repo).start_authentication()

        query = parse_qs(urlparse(url).query)
        assert query["scope"] == ["scope-a scope-b"]
        assert query["redirect_uri"] == ["http://localhost/cb"]
        state = query["state"][0]
        assert state.startswith("google_sample:")
        repo.upsert.assert_awaited_once_with("google_sample", {"state": state})
