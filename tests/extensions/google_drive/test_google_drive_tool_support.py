"""`lilla_core.extensions.google_drive.tool_support`（ツール共通の処理）のテスト。"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from lilla_core.core.exceptions import ReauthenticationRequiredError
from lilla_core.extensions import google_drive
from lilla_core.extensions.google_drive import tool_support
from lilla_core.extensions.google_drive.tool_support import (
    NOT_FOUND_MESSAGE,
    api_error_message,
    guess_text_mime,
    is_visible_text_file,
    summarize_file,
    truncate_text,
)


def _http_error(status: int, message: str = "") -> aiohttp.ClientResponseError:
    """指定ステータスの ClientResponseError を作る。"""
    return aiohttp.ClientResponseError(MagicMock(), (), status=status, message=message)


class TestIsVisibleTextFile:
    """扱うファイル（ゴミ箱に無い `text/*`）の判定。"""

    @pytest.mark.parametrize("mime", ["text/plain", "text/markdown", "text/csv"])
    def test_text_files(self, mime: str) -> None:
        """`text/*` は扱う。"""
        assert is_visible_text_file({"mimeType": mime, "trashed": False})

    @pytest.mark.parametrize(
        "mime",
        [
            "application/json",
            "application/octet-stream",
            "application/vnd.google-apps.document",
            None,
        ],
    )
    def test_other_files(self, mime) -> None:
        """`text/*` 以外は存在しないものとして扱う。"""
        assert not is_visible_text_file({"mimeType": mime})

    def test_trashed_files(self) -> None:
        """ゴミ箱のファイルは扱わない。"""
        assert not is_visible_text_file({"mimeType": "text/plain", "trashed": True})


class TestGuessTextMime:
    """新規作成するファイルの MIME タイプ。"""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("memo.md", "text/markdown"),
            ("README.MARKDOWN", "text/markdown"),
            ("notes.txt", "text/plain"),
            ("data.csv", "text/csv"),
            ("noext", "text/plain"),
            ("data.json", "text/plain"),
            ("image.png", "text/plain"),
        ],
    )
    def test_always_text(self, name: str, expected: str) -> None:
        """名前から推測し、`text/*` にならなければ `text/plain` にする。"""
        assert guess_text_mime(name) == expected


class TestTruncateText:
    """本文の切り詰め。"""

    def test_keeps_short_text(self) -> None:
        """上限以下ならそのまま。"""
        assert truncate_text("abc", 3) == ("abc", False)

    def test_truncates_long_text(self) -> None:
        """上限を超えた分は切り捨てる。"""
        assert truncate_text("abcdef", 4) == ("abcd", True)


class TestSummarizeFile:
    """LLM へ返すファイル情報の整形。"""

    def test_omits_parents(self) -> None:
        """親フォルダの ID は返さず、書き込めるかどうかを添える。"""
        summary = summarize_file(
            {
                "id": "f1",
                "name": "a.txt",
                "mimeType": "text/plain",
                "modifiedTime": "2026-10-01T00:00:00Z",
                "parents": ["secret-folder"],
                "size": "12",
            },
            writable=True,
        )

        assert summary == {
            "id": "f1",
            "name": "a.txt",
            "mime_type": "text/plain",
            "modified_time": "2026-10-01T00:00:00Z",
            "writable": True,
            "size": "12",
        }


class TestApiErrorMessage:
    """Google API のエラーの文言。"""

    def test_not_found(self) -> None:
        """404 は存在しない・許可されていないと同じ文言。"""
        assert api_error_message(_http_error(404)) == NOT_FOUND_MESSAGE

    def test_rate_limit(self) -> None:
        """429 はレート制限。"""
        assert "レート制限" in api_error_message(_http_error(429))

    def test_bad_request_includes_google_message(self) -> None:
        """400 は Google の `error.message`（範囲の書き間違いなど）を添える。"""
        body = json.dumps({"error": {"message": "Unable to parse range: X"}})

        assert "Unable to parse range: X" in api_error_message(_http_error(400, body))

    def test_bad_request_without_json(self) -> None:
        """本文が JSON でなくても落ちない。"""
        assert "400" in api_error_message(_http_error(400, "<html>"))

    def test_other_status(self) -> None:
        """それ以外はステータスだけを伝える。"""
        assert "500" in api_error_message(_http_error(500, "internal details"))


class TestRunDriveOperation:
    """設定・クライアントの用意と例外の変換。"""

    @pytest.fixture
    def client(self, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
        """`get_google_drive_client()` と設定の読み込みを差し替える。"""
        from lilla_core.extensions.google_drive import client as client_module

        mock = MagicMock()
        mock.start_authentication = AsyncMock(return_value="https://auth.example/drive")
        monkeypatch.setattr(client_module, "get_google_drive_client", lambda: mock)
        monkeypatch.setattr(
            tool_support, "load_config", lambda: google_drive.GoogleDriveConfig()
        )
        return mock

    async def test_passes_client_and_config(self, client: MagicMock) -> None:
        """操作にはクライアントと設定を渡し、戻り値をそのまま返す。"""
        operation = AsyncMock(return_value={"success": True})

        assert await tool_support.run_drive_operation("t", operation) == {"success": True}
        passed_client, passed_config = operation.await_args.args
        assert passed_client is client
        assert isinstance(passed_config, google_drive.GoogleDriveConfig)

    async def test_reauth(self, client: MagicMock) -> None:
        """再認証が必要なら認可フローを開始して `needs_auth` を返す。"""
        operation = AsyncMock(side_effect=ReauthenticationRequiredError("x"))

        result = await tool_support.run_drive_operation("t", operation)

        assert result["needs_auth"] is True
        assert result["needs_auth_list"][0]["auth_url"] == "https://auth.example/drive"

    async def test_api_error(self, client: MagicMock) -> None:
        """Google API のエラーは文言に変換した失敗結果にする。"""
        operation = AsyncMock(side_effect=_http_error(404))

        result = await tool_support.run_drive_operation("t", operation)

        assert result["success"] is False
        assert result["error"] == NOT_FOUND_MESSAGE

    async def test_unexpected_error(self, client: MagicMock) -> None:
        """それ以外の例外も失敗結果にして外へ漏らさない。"""
        operation = AsyncMock(side_effect=RuntimeError("boom"))

        result = await tool_support.run_drive_operation("t", operation)

        assert result["success"] is False
        assert result["error"] == "boom"

    async def test_setup_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """設定の読み込みに失敗したら操作を呼ばずに失敗結果にする。"""

        def broken() -> None:
            raise ValueError("no section")

        monkeypatch.setattr(tool_support, "load_config", broken)
        operation = AsyncMock()

        result = await tool_support.run_drive_operation("t", operation)

        assert result["success"] is False
        operation.assert_not_awaited()
