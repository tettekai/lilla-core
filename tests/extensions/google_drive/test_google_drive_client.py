"""`lilla_core.extensions.google_drive.client` の `GoogleDriveClient` のテスト。"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from lilla_core.core.exceptions import ReauthenticationRequiredError
from lilla_core.extensions import google_drive, google_oauth
from lilla_core.extensions.google_drive import client as client_module
from lilla_core.extensions.google_drive.client import (
    SCOPE_DRIVE,
    SCOPE_DRIVE_FILE,
    SCOPE_DRIVE_READONLY,
    SCOPE_SPREADSHEETS_READONLY,
    GoogleDriveClient,
    scopes_for,
)


def _make_repo(creds: dict | None = None) -> MagicMock:
    """get_by_type / upsert を持つ CredentialsRepository のモックを生成する。"""
    repo = MagicMock()
    repo.get_by_type = AsyncMock(return_value=creds)
    repo.upsert = AsyncMock()
    return repo


def _make_client(repo: MagicMock | None = None) -> GoogleDriveClient:
    """認証ヘッダを固定した GoogleDriveClient を生成する。"""
    client = GoogleDriveClient(repo or _make_repo(), "client-id", "secret", "http://x/cb")
    client._auth_headers = AsyncMock(return_value={"Authorization": "Bearer tok"})
    return client


@pytest.fixture
def send_http(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """クライアントモジュールが使う `send_http_request` を差し替える。"""
    mock = AsyncMock(return_value=json.dumps({"id": "f1", "name": "a.txt"}))
    monkeypatch.setattr(client_module, "send_http_request", mock)
    from lilla_core.extensions.google_oauth import client as oauth_client_module

    monkeypatch.setattr(oauth_client_module, "send_http_request", mock)
    return mock


class TestScopesFor:
    """設定から要求するスコープを決める。"""

    def test_read_only_without_writable_folders(self) -> None:
        """書き込み可能フォルダが無ければ、`write_scope` によらず読み取りのスコープだけ。"""
        for write_scope in ("file", "full"):
            cfg = google_drive.GoogleDriveConfig(
                read_only_folders=["a"], write_scope=write_scope
            )
            assert scopes_for(cfg) == [SCOPE_DRIVE_READONLY, SCOPE_SPREADSHEETS_READONLY]

    def test_drive_file_by_default(self) -> None:
        """書き込み可能フォルダがあり `write_scope: file` なら `drive.file` を足す。"""
        cfg = google_drive.GoogleDriveConfig(writable_folders=["a"])

        assert scopes_for(cfg) == [
            SCOPE_DRIVE_READONLY,
            SCOPE_DRIVE_FILE,
            SCOPE_SPREADSHEETS_READONLY,
        ]

    def test_full_drive_scope(self) -> None:
        """`write_scope: full` なら `drive`（`drive.readonly` を置き換える）。"""
        cfg = google_drive.GoogleDriveConfig(writable_folders=["a"], write_scope="full")

        assert scopes_for(cfg) == [SCOPE_DRIVE, SCOPE_SPREADSHEETS_READONLY]


class TestFromConfig:
    """設定からの組み立て。"""

    def test_sets_scopes_from_the_section(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OAuth の項目は `google-oauth` から、スコープは `extensions.google_drive` から決める。"""
        from lilla_core.repository import credentials_repository
        from lilla_core.testing import use_extensions, write_minimal_lilla_yaml

        monkeypatch.setattr(credentials_repository, "get_credentials_repo", lambda: _make_repo())
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "gc-secret")
        write_minimal_lilla_yaml(
            tmp_path,
            extra={
                "extensions": {
                    "google_oauth": {"client_id": "gc-id"},
                    "google_drive": {"writable_folders": ["w"], "write_scope": "full"},
                }
            },
        )

        with use_extensions(
            google_oauth.extension, google_drive.extension, config_root=tmp_path
        ):
            client = GoogleDriveClient.from_config()

        assert isinstance(client, GoogleDriveClient)
        assert client._client_id == "gc-id"
        assert client.SCOPES == [SCOPE_DRIVE, SCOPE_SPREADSHEETS_READONLY]
        # クラス属性（既定の読み取りスコープ）は変えない。
        assert GoogleDriveClient.SCOPES == [SCOPE_DRIVE_READONLY, SCOPE_SPREADSHEETS_READONLY]


class TestGrantedScopeCheck:
    """保存済みトークンのスコープの検査。"""

    async def test_raises_when_granted_scopes_are_insufficient(self, monkeypatch) -> None:
        """認可時に許可されたスコープが今の要求に足りなければ再認証を求める。"""
        get_token = AsyncMock(return_value="tok")
        monkeypatch.setattr(
            "lilla_core.extensions.google_oauth.token.get_google_access_token", get_token
        )
        repo = _make_repo({"scopes": [SCOPE_DRIVE_READONLY, SCOPE_SPREADSHEETS_READONLY]})
        client = GoogleDriveClient(repo, "id", "secret", "http://x/cb")
        client.SCOPES = [SCOPE_DRIVE, SCOPE_SPREADSHEETS_READONLY]

        with pytest.raises(ReauthenticationRequiredError):
            await client._get_access_token()
        get_token.assert_not_awaited()

    async def test_returns_token_when_scopes_cover(self, monkeypatch) -> None:
        """許可されたスコープが要求を含んでいればトークンを取得する。"""
        get_token = AsyncMock(return_value="tok")
        monkeypatch.setattr(
            "lilla_core.extensions.google_oauth.token.get_google_access_token", get_token
        )
        repo = _make_repo(
            {"scopes": [SCOPE_DRIVE_READONLY, SCOPE_DRIVE_FILE, SCOPE_SPREADSHEETS_READONLY]}
        )
        client = GoogleDriveClient(repo, "id", "secret", "http://x/cb")

        assert await client._get_access_token() == "tok"
        assert get_token.await_args.args[3] == "google_drive"

    async def test_defers_to_token_logic_without_saved_scopes(self, monkeypatch) -> None:
        """スコープが保存されていなければ判定せず、トークン取得側に任せる。"""
        get_token = AsyncMock(side_effect=ReauthenticationRequiredError("no token"))
        monkeypatch.setattr(
            "lilla_core.extensions.google_oauth.token.get_google_access_token", get_token
        )
        client = GoogleDriveClient(_make_repo(None), "id", "secret", "http://x/cb")

        with pytest.raises(ReauthenticationRequiredError):
            await client._get_access_token()
        get_token.assert_awaited_once()


class TestDriveRequests:
    """Drive API の呼び出し。"""

    async def test_get_metadata(self, send_http: AsyncMock) -> None:
        """ファイル ID をパスに入れ、共有ドライブ対応の付いた GET を送る。"""
        result = await _make_client().get_metadata("f1")

        assert result == {"id": "f1", "name": "a.txt"}
        args, kwargs = send_http.await_args
        assert args[0] == "https://www.googleapis.com/drive/v3/files/f1"
        assert kwargs["method"] == "GET"
        assert kwargs["params"]["supportsAllDrives"] == "true"
        assert "parents" in kwargs["params"]["fields"]

    async def test_search_files(self, send_http: AsyncMock) -> None:
        """全ドライブを対象に検索し、続きのページのトークンを渡せる。"""
        await _make_client().search_files("q-text", page_size=100, page_token="next")

        params = send_http.await_args.kwargs["params"]
        assert params["q"] == "q-text"
        assert params["pageSize"] == "100"
        assert params["pageToken"] == "next"
        assert params["corpora"] == "allDrives"
        assert params["includeItemsFromAllDrives"] == "true"

    async def test_search_files_omits_empty_page_token(self, send_http: AsyncMock) -> None:
        """最初のページでは `pageToken` を送らない。"""
        await _make_client().search_files("q", page_size=10)

        assert "pageToken" not in send_http.await_args.kwargs["params"]

    async def test_download_text(self, send_http: AsyncMock) -> None:
        """`alt=media` で本文をそのまま返す。"""
        send_http.return_value = "本文"

        assert await _make_client().download_text("f1") == "本文"
        kwargs = send_http.await_args.kwargs
        assert kwargs["params"]["alt"] == "media"
        assert kwargs["headers"] == {"Authorization": "Bearer tok"}

    async def test_create_text_file_sends_multipart(self, send_http: AsyncMock) -> None:
        """メタデータと本文を 1 回の multipart で送る。"""
        await _make_client().create_text_file("memo.md", "folder1", "# 見出し", "text/markdown")

        args, kwargs = send_http.await_args
        assert args[0] == "https://www.googleapis.com/upload/drive/v3/files"
        assert kwargs["method"] == "POST"
        assert kwargs["params"]["uploadType"] == "multipart"
        content_type = kwargs["headers"]["Content-Type"]
        assert content_type.startswith("multipart/related; boundary=")
        boundary = content_type.split("boundary=", 1)[1]
        body = kwargs["body"].decode("utf-8")
        parts = body.split(f"--{boundary}")
        assert json.loads(parts[1].split("\r\n\r\n", 1)[1].strip()) == {
            "name": "memo.md",
            "parents": ["folder1"],
            "mimeType": "text/markdown",
        }
        assert "Content-Type: text/markdown; charset=UTF-8" in parts[2]
        assert parts[2].split("\r\n\r\n", 1)[1] == "# 見出し\r\n"
        assert parts[3] == "--\r\n"
        assert kwargs["headers"]["Authorization"] == "Bearer tok"

    async def test_update_text_file_sends_raw_body(self, send_http: AsyncMock) -> None:
        """既存ファイルは `uploadType=media` の PATCH で本文だけを置き換える。"""
        await _make_client().update_text_file("f1", "新しい本文", "text/plain")

        args, kwargs = send_http.await_args
        assert args[0] == "https://www.googleapis.com/upload/drive/v3/files/f1"
        assert kwargs["method"] == "PATCH"
        assert kwargs["params"]["uploadType"] == "media"
        assert kwargs["headers"]["Content-Type"] == "text/plain; charset=UTF-8"
        assert kwargs["body"] == "新しい本文".encode("utf-8")


class TestSheetsRequests:
    """Sheets API の呼び出し。"""

    async def test_get_sheet_values_encodes_range(self, send_http: AsyncMock) -> None:
        """範囲（シート名・`!`・`:` を含む）は URL のパス用にエンコードする。"""
        await _make_client().get_sheet_values("sheet1", "シート 1!A1:B2")

        url = send_http.await_args.args[0]
        assert url.startswith("https://sheets.googleapis.com/v4/spreadsheets/sheet1/values/")
        encoded = url.rsplit("/", 1)[1]
        assert "/" not in encoded and "!" not in encoded and " " not in encoded


class TestBoundary:
    """multipart の境界文字列。"""

    def test_boundary_does_not_appear_in_content(self, monkeypatch) -> None:
        """本文に同じ文字列が含まれていたら作り直す。"""
        tokens = iter(["aaaa", "bbbb"])
        monkeypatch.setattr(client_module.secrets, "token_hex", lambda n: next(tokens))

        assert client_module._new_boundary("x lilla-aaaa y") == "lilla-bbbb"
