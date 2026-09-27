"""`lilla_core.extensions.google_oauth.callback.handle_google_callback` のテスト。"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from lilla_core.extensions import google_oauth


def _make_request(query_params: dict) -> MagicMock:
    """テスト用 aiohttp Request モックを生成する。"""
    req = MagicMock()
    req.rel_url.query = query_params
    return req


def _make_repo(creds=None) -> MagicMock:
    """CredentialsRepository のモックを生成する。"""
    repo = MagicMock()
    repo.get_by_type = AsyncMock(return_value=creds)
    repo.upsert = AsyncMock()
    return repo


_REDIRECT_URI = "http://localhost:8765/oauth/google-oauth/callback"


def _write_config_root(directory: Path, client_id: str | None = "gc-id") -> Path:
    """`extensions.google_oauth` だけを持つ最小構成の `lilla.yaml` を書く。"""
    from lilla_core.testing import write_minimal_lilla_yaml

    section = {"redirect_uri": _REDIRECT_URI}
    if client_id is not None:
        section["client_id"] = client_id
    write_minimal_lilla_yaml(directory, extra={"extensions": {"google_oauth": section}})
    return directory


@pytest.fixture
def repo() -> MagicMock:
    """`state` 検証を通る認証情報を持つリポジトリモック。"""
    return _make_repo({"state": "google_calendar:token"})


@pytest.fixture
def callback(tmp_path: Path, repo: MagicMock, monkeypatch: pytest.MonkeyPatch):
    """`google-oauth` を登録して設定を合成し、リポジトリを差し替えたコールバックモジュールを返す。

    `handle_google_callback` は設定とリポジトリを関数内で遅延 import するため、
    合成済みの本物の設定と、差し替えた `get_credentials_repo` がそのまま使われる。
    """
    from lilla_core.extensions.google_oauth import callback as loaded
    from lilla_core.repository import credentials_repository
    from lilla_core.testing import use_extensions

    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "gc-secret")
    monkeypatch.setattr(credentials_repository, "get_credentials_repo", lambda: repo)
    with use_extensions(google_oauth.extension, config_root=_write_config_root(tmp_path)):
        yield loaded


class TestHandleGoogleCallbackValidation:
    """トークン交換の前に弾く入力（クエリ・state）。"""

    async def test_returns_400_when_missing_code(self, callback) -> None:
        """code が無い場合 HTTP 400 を返す。"""
        resp = await callback.handle_google_callback(
            _make_request({"state": "google_calendar:token"})
        )

        assert resp.status == 400

    async def test_returns_400_when_missing_state(self, callback) -> None:
        """state が無い場合 HTTP 400 を返す。"""
        resp = await callback.handle_google_callback(_make_request({"code": "mycode"}))

        assert resp.status == 400

    async def test_returns_400_when_state_has_no_credential_type(self, callback) -> None:
        """state が `種別:乱数` の形でない場合 HTTP 400 を返す。"""
        resp = await callback.handle_google_callback(
            _make_request({"code": "mycode", "state": "no-colon"})
        )

        assert resp.status == 400

    async def test_returns_400_when_credential_type_is_empty(self, callback) -> None:
        """state の種別部分が空の場合 HTTP 400 を返す。"""
        resp = await callback.handle_google_callback(
            _make_request({"code": "mycode", "state": ":token"})
        )

        assert resp.status == 400

    async def test_returns_400_when_no_state_was_issued_for_the_type(
        self, callback, repo
    ) -> None:
        """発行していない種別の state は、保存済みの値が無いので HTTP 400 を返す。"""
        repo.get_by_type = AsyncMock(return_value=None)

        resp = await callback.handle_google_callback(
            _make_request({"code": "mycode", "state": "google_drive:token"})
        )

        assert resp.status == 400
        repo.get_by_type.assert_awaited_once_with("google_drive")
        repo.upsert.assert_not_awaited()

    async def test_rejects_state_stored_by_another_flow(self, callback, repo) -> None:
        """別の認可フロー（コロンを含まない乱数の state）を流用した要求は通さない。"""
        repo.get_by_type = AsyncMock(return_value={"state": "random-withings-state"})

        resp = await callback.handle_google_callback(
            _make_request({"code": "mycode", "state": "withings:random-withings-state"})
        )

        assert resp.status == 400
        repo.upsert.assert_not_awaited()

    async def test_returns_400_when_state_mismatch(self, callback, repo) -> None:
        """保存済み state と一致しない場合 HTTP 400 を返す。"""
        repo.get_by_type = AsyncMock(return_value={"state": "google_calendar:other"})

        resp = await callback.handle_google_callback(
            _make_request({"code": "mycode", "state": "google_calendar:token"})
        )

        assert resp.status == 400
        repo.upsert.assert_not_awaited()

    async def test_returns_500_when_client_not_configured(
        self, tmp_path: Path, repo: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """client_id が未設定なら HTTP 500 を返す。"""
        from lilla_core.extensions.google_oauth import callback
        from lilla_core.repository import credentials_repository
        from lilla_core.testing import use_extensions

        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "gc-secret")
        monkeypatch.setattr(credentials_repository, "get_credentials_repo", lambda: repo)
        config_root = _write_config_root(tmp_path, client_id=None)
        with use_extensions(google_oauth.extension, config_root=config_root):
            resp = await callback.handle_google_callback(
                _make_request({"code": "mycode", "state": "google_calendar:token"})
            )

        assert resp.status == 500


class TestHandleGoogleCallbackExchange:
    """認可コードとトークンの交換。"""

    async def test_accepts_any_credential_type_it_issued(
        self, callback, repo: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """種別を固定の一覧で絞らない（利用者側の Google API 拡張の種別も通る）。"""
        repo.get_by_type = AsyncMock(return_value={"state": "google_tasks:token"})
        monkeypatch.setattr(
            callback,
            "send_http_request",
            AsyncMock(return_value=json.dumps({"access_token": "t", "expires_in": 1})),
        )

        resp = await callback.handle_google_callback(
            _make_request({"code": "mycode", "state": "google_tasks:token"})
        )

        assert resp.status == 200
        assert repo.upsert.await_args.args[0] == "google_tasks"

    async def test_success_saves_tokens_and_scopes(
        self, callback, repo: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """成功時はトークンと scopes を credential_type ごとに保存する。"""
        monkeypatch.setattr(
            callback,
            "send_http_request",
            AsyncMock(return_value=json.dumps({
                "access_token": "new-token",
                "refresh_token": "new-refresh",
                "expires_in": 3600,
                "scope": "scope-a scope-b",
            })),
        )

        resp = await callback.handle_google_callback(
            _make_request({"code": "mycode", "state": "google_calendar:token"})
        )

        assert resp.status == 200
        assert resp.text == "認証が完了しました。"
        # 1 回目は state のクリア、2 回目がトークンの保存。
        saved_type, saved_doc = repo.upsert.await_args.args
        assert saved_type == "google_calendar"
        assert saved_doc["access_token"] == "new-token"
        assert saved_doc["refresh_token"] == "new-refresh"
        assert saved_doc["scopes"] == ["scope-a", "scope-b"]

    async def test_posts_authorization_code_with_configured_client(
        self, callback, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """トークンエンドポイントへ設定値と authorization_code を送る。"""
        send_mock = AsyncMock(return_value=json.dumps({"access_token": "t", "expires_in": 1}))
        monkeypatch.setattr(callback, "send_http_request", send_mock)

        await callback.handle_google_callback(
            _make_request({"code": "mycode", "state": "google_calendar:token"})
        )

        form = send_mock.await_args.kwargs["form_data"]
        assert form["grant_type"] == "authorization_code"
        assert form["code"] == "mycode"
        assert form["client_id"] == "gc-id"
        assert form["client_secret"] == "gc-secret"
        assert form["redirect_uri"] == _REDIRECT_URI

    async def test_returns_500_when_token_endpoint_returns_error(
        self, callback, repo: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """本文に error が含まれる場合はトークンを保存せず HTTP 500 を返す。"""
        monkeypatch.setattr(
            callback,
            "send_http_request",
            AsyncMock(return_value=json.dumps({"error": "invalid_grant"})),
        )

        resp = await callback.handle_google_callback(
            _make_request({"code": "mycode", "state": "google_calendar:token"})
        )

        assert resp.status == 500
        # state のクリアだけが行われ、トークンは保存されない。
        assert repo.upsert.await_count == 1
        assert repo.upsert.await_args.args[1] == {"state": None}
