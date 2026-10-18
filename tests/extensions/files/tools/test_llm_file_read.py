"""`lilla_core.extensions.files` 同梱の `tools/llm_file_read.py` のテスト。"""
from __future__ import annotations

import codecs
import os

import pytest

from lilla_core.extensions import files
from lilla_core.extensions.files.access import OUTSIDE_MESSAGE
from lilla_core.extensions.files.tool_support import NOT_TEXT_MESSAGE


@pytest.fixture
def tool(load_tool):
    """ツール本体をロードする。"""
    return load_tool("llm_file_read")


class TestSchema:
    """SCHEMA の構造。"""

    def test_function_name_and_parameters(self, tool) -> None:
        """関数名は `file_read` で、`path` が必須。"""
        function = tool.SCHEMA["function"]
        assert function["name"] == "file_read"
        assert function["parameters"]["required"] == ["path"]
        assert {"offset", "limit"} <= set(function["parameters"]["properties"])


class TestRead:
    """本文の取得。"""

    async def test_reads_text(self, tool, use_config) -> None:
        """UTF-8 のテキストをそのまま返す。"""
        result = await tool.execute({"path": "docs/readme.md"}, {})

        assert result["success"] is True
        data = result["data"]
        assert data["content"] == "# Readme\nhello world\n"
        assert data["truncated"] is False
        assert data["writable"] is False
        assert "next_offset" not in data

    async def test_strips_bom(self, tool, use_config, dirs) -> None:
        """先頭の BOM は取り除いて読む。"""
        (dirs["work"] / "bom.txt").write_bytes(codecs.BOM_UTF8 + "こんにちは".encode("utf-8"))

        result = await tool.execute({"path": "work/bom.txt"}, {})

        assert result["data"]["content"] == "こんにちは"
        assert result["data"]["writable"] is True

    async def test_rejects_binary(self, tool, use_config, dirs) -> None:
        """先頭に NUL バイトがあれば中身を返さない。"""
        (dirs["work"] / "image.bin").write_bytes(b"PNG\x00\x01\x02text")

        result = await tool.execute({"path": "work/image.bin"}, {})

        assert result["success"] is False
        assert result["error"] == NOT_TEXT_MESSAGE
        assert "data" not in result or not result.get("data")

    async def test_rejects_non_utf8(self, tool, use_config, dirs) -> None:
        """Shift_JIS など UTF-8 として読めないファイルは中身を返さない。"""
        (dirs["work"] / "sjis.txt").write_bytes("日本語のテキスト".encode("shift_jis"))

        result = await tool.execute({"path": "work/sjis.txt"}, {})

        assert result["success"] is False
        assert result["error"] == NOT_TEXT_MESSAGE

    async def test_truncates_at_max_read_chars(self, tool, monkeypatch, dirs) -> None:
        """`max_read_chars` を超えたら切り詰め、続きの位置を返す。"""
        from lilla_core.extensions.files import tool_support

        config = files.FilesConfig(writable_dirs=[str(dirs["work"])], max_read_chars=4)
        monkeypatch.setattr(tool_support, "load_config", lambda: config)
        (dirs["work"] / "long.txt").write_text("abcdefghij", encoding="utf-8")

        first = await tool.execute({"path": "work/long.txt"}, {})
        assert first["data"]["content"] == "abcd"
        assert first["data"]["truncated"] is True
        assert first["data"]["next_offset"] == 4
        assert first["data"]["total_chars"] == 10

        second = await tool.execute({"path": "work/long.txt", "offset": 8, "limit": 100}, {})
        assert second["data"]["content"] == "ij"
        assert second["data"]["truncated"] is False

    async def test_offset_and_limit(self, tool, use_config) -> None:
        """`offset` / `limit` で途中から読める。"""
        result = await tool.execute({"path": "docs/readme.md", "offset": 2, "limit": 6}, {})

        assert result["data"]["content"] == "Readme"
        assert result["data"]["truncated"] is True
        assert result["data"]["next_offset"] == 8

    @pytest.mark.parametrize("args", [{"offset": -1}, {"limit": 0}, {"offset": "1"}, {"limit": True}])
    async def test_rejects_invalid_offset_limit(self, tool, use_config, args) -> None:
        """負の offset・0 以下の limit・整数以外は拒否する。"""
        result = await tool.execute({"path": "docs/readme.md", **args}, {})

        assert result["success"] is False


class TestRejects:
    """ホワイトリスト外へのアクセスの拒否。"""

    @pytest.mark.parametrize("path", ["docs/../outside/secret.txt", "outside/secret.txt", "/etc/passwd"])
    async def test_outside_paths(self, tool, use_config, path: str) -> None:
        """`..`・ホワイトリスト外・絶対パスは読めない。"""
        result = await tool.execute({"path": path}, {})

        assert result["success"] is False
        assert "secret" not in str(result)

    async def test_symlink_to_outside(self, tool, use_config, dirs) -> None:
        """外を指すシンボリックリンクは読めない。"""
        os.symlink(dirs["outside"] / "secret.txt", dirs["docs"] / "link.txt")

        result = await tool.execute({"path": "docs/link.txt"}, {})

        assert result["success"] is False
        assert result["error"] == OUTSIDE_MESSAGE

    async def test_symlink_inside_whitelist(self, tool, use_config, dirs) -> None:
        """配下を指すシンボリックリンクは読める。"""
        os.symlink(dirs["docs"] / "readme.md", dirs["docs"] / "alias.md")

        result = await tool.execute({"path": "docs/alias.md"}, {})

        assert result["success"] is True

    async def test_directory(self, tool, use_config) -> None:
        """ディレクトリは読めない。"""
        result = await tool.execute({"path": "docs/sub"}, {})

        assert result["success"] is False

    async def test_missing_file(self, tool, use_config) -> None:
        """存在しないファイルはエラー。"""
        result = await tool.execute({"path": "docs/missing.txt"}, {})

        assert result["success"] is False
