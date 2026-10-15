"""`google-drive` 拡張のツールのテストで共通の fixture。

Drive の階層（ID → メタデータ）を持つ偽のクライアントと、ツールが読む設定を差し替え、
ツール本体をファイルパスからロードする。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from lilla_core.extensions import google_drive

_TOOLS_DIR = Path(google_drive.__file__).resolve().parent / "tools"


def http_error(status: int, message: str = "") -> aiohttp.ClientResponseError:
    """指定ステータスの ClientResponseError を作る。"""
    return aiohttp.ClientResponseError(MagicMock(), (), status=status, message=message)


class FakeDrive:
    """Drive / Sheets API の偽物（`GoogleDriveClient` の代わり）。

    次の階層を初期状態として持つ:

    - `ro`（読み取り専用フォルダ）/ `ro-note`（text/plain）/ `ro-sheet`（スプレッドシート）
    - `rw`（書き込み可能フォルダ）/ `rw-sub`（フォルダ）/ `rw-note`（text/markdown）
    - `rw` / `rw-json`（application/json）・`rw-trashed`（ゴミ箱の text/plain）
    - `outside`（ホワイトリスト外のフォルダ）/ `out-note`（text/plain）/ `out-sheet`
    """

    def __init__(self) -> None:
        """初期状態の階層と本文を用意する。"""
        folder = "application/vnd.google-apps.folder"
        sheet = "application/vnd.google-apps.spreadsheet"
        self.files: dict[str, dict[str, Any]] = {}
        self.contents: dict[str, str] = {}
        for file_id, name, mime, parent in [
            ("ro", "読み取り専用", folder, "root"),
            ("ro-note", "note.txt", "text/plain", "ro"),
            ("ro-sheet", "家計簿", sheet, "ro"),
            ("rw", "書き込み可", folder, "root"),
            ("rw-sub", "sub", folder, "rw"),
            ("rw-note", "memo.md", "text/markdown", "rw-sub"),
            ("rw-json", "data.json", "application/json", "rw"),
            ("rw-trashed", "old.txt", "text/plain", "rw"),
            ("outside", "外", folder, "root"),
            ("out-note", "secret.txt", "text/plain", "outside"),
            ("out-sheet", "外の表", sheet, "outside"),
        ]:
            self.files[file_id] = {
                "id": file_id,
                "name": name,
                "mimeType": mime,
                "parents": [parent],
                "trashed": file_id == "rw-trashed",
                "modifiedTime": "2026-10-01T00:00:00Z",
            }
            self.contents[file_id] = f"{name} の本文"
        self.files["root"] = {"id": "root", "name": "マイドライブ", "mimeType": folder}

        self.get_metadata = AsyncMock(side_effect=self._get_metadata)
        self.search_files = AsyncMock(side_effect=self._search_files)
        self.download_text = AsyncMock(side_effect=lambda file_id: self.contents[file_id])
        self.update_text_file = AsyncMock(side_effect=self._update_text_file)
        self.create_text_file = AsyncMock(side_effect=self._create_text_file)
        self.get_sheet_values = AsyncMock(
            return_value={"range": "シート1!A1:B2", "values": [["a", "b"], ["c", "d"]]}
        )
        self.start_authentication = AsyncMock(return_value="https://auth.example/drive")
        #: `search_files` が返すページ（テストで差し替える）。
        self.search_pages: list[dict[str, Any]] = [{"files": []}]

    async def _get_metadata(self, file_id: str) -> dict[str, Any]:
        """メタデータを返す（無ければ 404）。"""
        if file_id not in self.files:
            raise http_error(404)
        return dict(self.files[file_id])

    async def _search_files(self, q: str, *, page_size: int, page_token: str | None = None):
        """`search_pages` を順に返す（`page_token` はページ番号の文字列）。"""
        return self.search_pages[int(page_token or 0)]

    async def _update_text_file(self, file_id: str, content: str, mime_type: str):
        """本文を置き換える。"""
        self.contents[file_id] = content
        return {**self.files[file_id], "modifiedTime": "2026-10-15T00:00:00Z"}

    async def _create_text_file(self, name: str, parent_id: str, content: str, mime_type: str):
        """新しいファイルを作る。"""
        file_id = f"new-{len(self.files)}"
        self.files[file_id] = {
            "id": file_id,
            "name": name,
            "mimeType": mime_type,
            "parents": [parent_id],
            "modifiedTime": "2026-10-15T00:00:00Z",
        }
        self.contents[file_id] = content
        return dict(self.files[file_id])


@pytest.fixture(name="http_error")
def http_error_fixture():
    """`http_error` をテストから使うための fixture（conftest を直接 import しないため）。"""
    return http_error


@pytest.fixture
def drive_config() -> google_drive.GoogleDriveConfig:
    """`ro` を読み取り専用、`rw` を書き込み可能にした設定（テストで差し替える）。"""
    return google_drive.GoogleDriveConfig(read_only_folders=["ro"], writable_folders=["rw"])


@pytest.fixture
def fake_drive(drive_config, monkeypatch: pytest.MonkeyPatch) -> FakeDrive:
    """ツールが使うクライアントと設定を、偽の Drive と `drive_config` に差し替える。"""
    from lilla_core.extensions.google_drive import client as client_module
    from lilla_core.extensions.google_drive import tool_support

    drive = FakeDrive()
    monkeypatch.setattr(client_module, "get_google_drive_client", lambda: drive)
    monkeypatch.setattr(tool_support, "load_config", lambda: drive_config)
    return drive


@pytest.fixture
def load_tool():
    """同梱のツールをファイルパスからロードする関数を返す（テスト後に `sys.modules` から外す）。"""
    loaded: list[str] = []

    def _load(stem: str):
        sys.modules.pop(stem, None)
        spec = importlib.util.spec_from_file_location(stem, str(_TOOLS_DIR / f"{stem}.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        loaded.append(stem)
        return module

    yield _load
    for stem in loaded:
        sys.modules.pop(stem, None)
