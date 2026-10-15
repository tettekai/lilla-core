"""`lilla_core.extensions.google_drive` 同梱の `tools/llm_google_drive_get_text.py` のテスト。"""
from __future__ import annotations

import pytest

from lilla_core.extensions.google_drive.tool_support import NOT_FOUND_MESSAGE


@pytest.fixture
def tool(load_tool):
    """ツール本体をロードする。"""
    return load_tool("llm_google_drive_get_text")


class TestSchema:
    """SCHEMA の構造。"""

    def test_function_name_and_parameters(self, tool) -> None:
        """関数名は `google_drive_get_text` で、`file_id` だけを受け取る。"""
        function = tool.SCHEMA["function"]
        assert function["name"] == "google_drive_get_text"
        assert function["parameters"]["required"] == ["file_id"]


class TestGetText:
    """本文の取得。"""

    async def test_reads_whitelisted_text_file(self, tool, fake_drive) -> None:
        """書き込み可能フォルダの深い配下のテキストファイルを読める。"""
        result = await tool.execute({"file_id": "rw-note"}, {})

        assert result["success"] is True
        data = result["data"]
        assert data["content"] == "memo.md の本文"
        assert data["writable"] is True
        assert data["truncated"] is False
        assert data["mime_type"] == "text/markdown"

    async def test_read_only_folder(self, tool, fake_drive) -> None:
        """読み取り専用フォルダのファイルは `writable: false` で読める。"""
        result = await tool.execute({"file_id": "ro-note"}, {})

        assert result["success"] is True
        assert result["data"]["writable"] is False

    @pytest.mark.parametrize("file_id", ["out-note", "rw-json", "rw-trashed", "missing", "ro-sheet"])
    async def test_hidden_files_look_missing(self, tool, fake_drive, file_id: str) -> None:
        """ホワイトリスト外・`text/*` 以外・ゴミ箱・不在はすべて同じエラーで、本文は取らない。"""
        result = await tool.execute({"file_id": file_id}, {})

        assert result["success"] is False
        assert result["error"] == NOT_FOUND_MESSAGE
        fake_drive.download_text.assert_not_awaited()

    @pytest.mark.parametrize("file_id", [None, "", "../files", "a/b"])
    async def test_rejects_invalid_id(self, tool, fake_drive, file_id) -> None:
        """不正な形式の ID は API を呼ばずに拒否する。"""
        result = await tool.execute({"file_id": file_id}, {})

        assert result["success"] is False
        fake_drive.get_metadata.assert_not_awaited()

    async def test_truncates_long_content(self, tool, fake_drive, drive_config) -> None:
        """`max_read_chars` を超えた分は切り詰め、元の文字数を添える。"""
        drive_config.max_read_chars = 5
        fake_drive.contents["ro-note"] = "0123456789"

        result = await tool.execute({"file_id": "ro-note"}, {})

        data = result["data"]
        assert data["content"] == "01234"
        assert data["truncated"] is True
        assert data["total_chars"] == 10

    async def test_empty_whitelist_denies(self, tool, fake_drive, drive_config) -> None:
        """ホワイトリストが空なら API を呼ばずに拒否する。"""
        drive_config.read_only_folders = []
        drive_config.writable_folders = []

        result = await tool.execute({"file_id": "ro-note"}, {})

        assert result["error"] == NOT_FOUND_MESSAGE
        fake_drive.get_metadata.assert_not_awaited()
