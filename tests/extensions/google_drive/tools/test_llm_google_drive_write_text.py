"""`lilla_core.extensions.google_drive` 同梱の `tools/llm_google_drive_write_text.py` のテスト。"""
from __future__ import annotations

import pytest

from lilla_core.extensions.google_drive.tool_support import NOT_FOUND_MESSAGE


@pytest.fixture
def tool(load_tool):
    """ツール本体をロードする。"""
    return load_tool("llm_google_drive_write_text")


class TestSchema:
    """SCHEMA の構造。"""

    def test_function_name_and_parameters(self, tool) -> None:
        """関数名は `google_drive_write_text` で、必須は `content` だけ。"""
        function = tool.SCHEMA["function"]
        assert function["name"] == "google_drive_write_text"
        assert function["parameters"]["required"] == ["content"]
        assert set(function["parameters"]["properties"]) == {
            "content",
            "file_id",
            "name",
            "parent_folder_id",
        }


class TestValidation:
    """入力の検証（API を呼ばずに拒否する）。"""

    @pytest.mark.parametrize(
        "input_",
        [
            {"file_id": "rw-note"},
            {"content": 1, "file_id": "rw-note"},
            {"content": "x", "file_id": "a/b"},
            {"content": "x", "file_id": "rw-note", "name": "other.txt"},
            {"content": "x", "file_id": "rw-note", "parent_folder_id": "rw"},
            {"content": "x", "parent_folder_id": "rw"},
            {"content": "x", "name": "  ", "parent_folder_id": "rw"},
            {"content": "x", "name": "a.txt"},
            {"content": "x", "name": "a.txt", "parent_folder_id": "../rw"},
        ],
    )
    async def test_rejects(self, tool, fake_drive, input_: dict) -> None:
        """content が無い・ID の形式違い・上書きと作成の引数の混在・作成時の必須漏れ。"""
        result = await tool.execute(input_, {})

        assert result["success"] is False
        fake_drive.get_metadata.assert_not_awaited()


class TestOverwrite:
    """既存ファイルの上書き。"""

    async def test_overwrites_text_file_in_writable_folder(self, tool, fake_drive) -> None:
        """書き込み可能フォルダの配下なら、元の MIME タイプのまま本文を置き換える。"""
        result = await tool.execute({"content": "新しい本文", "file_id": "rw-note"}, {})

        assert result["success"] is True
        assert result["data"]["created"] is False
        assert result["data"]["id"] == "rw-note"
        fake_drive.update_text_file.assert_awaited_once_with(
            "rw-note", "新しい本文", "text/markdown"
        )

    async def test_allows_empty_content(self, tool, fake_drive) -> None:
        """空の本文での上書きもできる。"""
        result = await tool.execute({"content": "", "file_id": "rw-note"}, {})

        assert result["success"] is True

    async def test_rejects_read_only_folder(self, tool, fake_drive) -> None:
        """読み取り専用フォルダのファイルは上書きしない。"""
        result = await tool.execute({"content": "x", "file_id": "ro-note"}, {})

        assert result["success"] is False
        assert "書き込みが許可されていません" in result["error"]
        fake_drive.update_text_file.assert_not_awaited()

    @pytest.mark.parametrize("file_id", ["out-note", "rw-json", "rw-trashed", "missing"])
    async def test_hidden_files_look_missing(self, tool, fake_drive, file_id: str) -> None:
        """ホワイトリスト外・`text/*` 以外・ゴミ箱・不在は同じエラーで、書き込まない。"""
        result = await tool.execute({"content": "x", "file_id": file_id}, {})

        assert result["error"] == NOT_FOUND_MESSAGE
        fake_drive.update_text_file.assert_not_awaited()

    @pytest.mark.parametrize("status", [403, 404])
    async def test_explains_drive_file_scope(
        self, tool, fake_drive, http_error, status: int
    ) -> None:
        """`write_scope: file` で API に拒否されたら、作成したファイルしか上書きできない旨を返す。"""
        fake_drive.update_text_file.side_effect = http_error(status)

        result = await tool.execute({"content": "x", "file_id": "rw-note"}, {})

        assert result["success"] is False
        assert "write_scope" in result["error"]

    async def test_full_scope_reports_api_error(
        self, tool, fake_drive, drive_config, http_error
    ) -> None:
        """`write_scope: full` での拒否は通常の API エラーとして返す。"""
        drive_config.write_scope = "full"
        fake_drive.update_text_file.side_effect = http_error(403)

        result = await tool.execute({"content": "x", "file_id": "rw-note"}, {})

        assert result["success"] is False
        assert "write_scope" not in result["error"]


class TestCreate:
    """新規作成。"""

    async def test_creates_in_writable_subfolder(self, tool, fake_drive) -> None:
        """書き込み可能フォルダの配下のフォルダに、名前から決めた MIME タイプで作る。"""
        result = await tool.execute(
            {"content": "# 見出し", "name": " today.md ", "parent_folder_id": "rw-sub"}, {}
        )

        assert result["success"] is True
        assert result["data"]["created"] is True
        assert result["data"]["name"] == "today.md"
        fake_drive.create_text_file.assert_awaited_once_with(
            "today.md", "rw-sub", "# 見出し", "text/markdown"
        )

    async def test_rejects_read_only_folder(self, tool, fake_drive) -> None:
        """読み取り専用フォルダには作らない。"""
        result = await tool.execute(
            {"content": "x", "name": "a.txt", "parent_folder_id": "ro"}, {}
        )

        assert result["success"] is False
        assert "書き込みが許可されていません" in result["error"]
        fake_drive.create_text_file.assert_not_awaited()

    @pytest.mark.parametrize("parent", ["outside", "rw-note", "missing"])
    async def test_rejects_non_writable_targets(self, tool, fake_drive, parent: str) -> None:
        """ホワイトリスト外・フォルダでないもの・不在のフォルダには作らない。"""
        result = await tool.execute(
            {"content": "x", "name": "a.txt", "parent_folder_id": parent}, {}
        )

        assert result["success"] is False
        assert "フォルダが見つからない" in result["error"]
        fake_drive.create_text_file.assert_not_awaited()

    async def test_empty_whitelist_denies(self, tool, fake_drive, drive_config) -> None:
        """ホワイトリストが空なら API を呼ばずに拒否する。"""
        drive_config.read_only_folders = []
        drive_config.writable_folders = []

        result = await tool.execute(
            {"content": "x", "name": "a.txt", "parent_folder_id": "rw"}, {}
        )

        assert result["success"] is False
        fake_drive.get_metadata.assert_not_awaited()
