"""`lilla_core.extensions.files` 同梱の `tools/llm_file_write.py` のテスト。"""
from __future__ import annotations

import codecs
import os

import pytest

from lilla_core.extensions.files.access import OUTSIDE_MESSAGE
from lilla_core.extensions.files.tool_support import NOT_TEXT_MESSAGE


@pytest.fixture
def tool(load_tool):
    """ツール本体をロードする。"""
    return load_tool("llm_file_write")


class TestSchema:
    """SCHEMA の構造。"""

    def test_function_name_and_parameters(self, tool) -> None:
        """関数名は `file_write` で、`mode` は 3 種類。"""
        function = tool.SCHEMA["function"]
        assert function["name"] == "file_write"
        assert function["parameters"]["required"] == ["path", "content", "mode"]
        assert function["parameters"]["properties"]["mode"]["enum"] == [
            "create",
            "overwrite",
            "append",
        ]


class TestModes:
    """`mode` ごとの書き込み。"""

    async def test_create(self, tool, use_config, dirs) -> None:
        """`create` は新しいファイルを UTF-8（BOM 無し）で作る。"""
        result = await tool.execute({"path": "work/new.md", "content": "こんにちは\n", "mode": "create"}, {})

        assert result["success"] is True
        assert result["data"]["created"] is True
        assert (dirs["work"] / "new.md").read_bytes() == "こんにちは\n".encode("utf-8")

    async def test_create_fails_when_exists(self, tool, use_config, dirs) -> None:
        """`create` は既にあれば失敗し、中身を変えない。"""
        result = await tool.execute({"path": "work/memo.txt", "content": "x", "mode": "create"}, {})

        assert result["success"] is False
        assert (dirs["work"] / "memo.txt").read_text(encoding="utf-8") == "memo\n"

    async def test_overwrite(self, tool, use_config, dirs) -> None:
        """`overwrite` は丸ごと置き換える。"""
        result = await tool.execute({"path": "work/memo.txt", "content": "new", "mode": "overwrite"}, {})

        assert result["success"] is True
        assert result["data"]["created"] is False
        assert (dirs["work"] / "memo.txt").read_text(encoding="utf-8") == "new"

    async def test_overwrite_creates_missing_file(self, tool, use_config, dirs) -> None:
        """`overwrite` は無ければ作る。"""
        result = await tool.execute({"path": "work/a.txt", "content": "a", "mode": "overwrite"}, {})

        assert result["data"]["created"] is True
        assert (dirs["work"] / "a.txt").read_text(encoding="utf-8") == "a"

    async def test_append(self, tool, use_config, dirs) -> None:
        """`append` は末尾に足す。"""
        result = await tool.execute({"path": "work/memo.txt", "content": "more\n", "mode": "append"}, {})

        assert result["success"] is True
        assert (dirs["work"] / "memo.txt").read_text(encoding="utf-8") == "memo\nmore\n"

    async def test_keeps_newlines(self, tool, use_config, dirs) -> None:
        """改行コードは変換しない。"""
        await tool.execute({"path": "work/crlf.txt", "content": "a\r\nb\n", "mode": "create"}, {})

        assert (dirs["work"] / "crlf.txt").read_bytes() == b"a\r\nb\n"

    async def test_creates_parent_directories(self, tool, use_config, dirs) -> None:
        """親ディレクトリが無ければ作る。"""
        result = await tool.execute({"path": "work/a/b/c.txt", "content": "c", "mode": "create"}, {})

        assert result["success"] is True
        assert result["data"]["path"] == "work/a/b/c.txt"
        assert (dirs["work"] / "a" / "b" / "c.txt").read_text(encoding="utf-8") == "c"

    async def test_append_to_bom_file(self, tool, use_config, dirs) -> None:
        """BOM 付きの UTF-8 ファイルには追記できる。"""
        path = dirs["work"] / "bom.txt"
        path.write_bytes(codecs.BOM_UTF8 + b"a\n")

        result = await tool.execute({"path": "work/bom.txt", "content": "b\n", "mode": "append"}, {})

        assert result["success"] is True
        assert path.read_bytes() == codecs.BOM_UTF8 + b"a\nb\n"

    @pytest.mark.parametrize(
        "raw", ["日本語".encode("shift_jis"), b"PNG\x00\x01"], ids=["shift_jis", "binary"]
    )
    async def test_append_refuses_non_text(self, tool, use_config, dirs, raw: bytes) -> None:
        """UTF-8 のテキストとして読めないファイルには追記せず、中身を変えない。"""
        path = dirs["work"] / "legacy.txt"
        path.write_bytes(raw)

        result = await tool.execute({"path": "work/legacy.txt", "content": "x", "mode": "append"}, {})

        assert result["success"] is False
        assert result["error"] == NOT_TEXT_MESSAGE
        assert path.read_bytes() == raw

    async def test_overwrite_non_text_is_allowed(self, tool, use_config, dirs) -> None:
        """丸ごと置き換える `overwrite` は既存の文字コードを問わない。"""
        path = dirs["work"] / "legacy.txt"
        path.write_bytes("日本語".encode("shift_jis"))

        result = await tool.execute({"path": "work/legacy.txt", "content": "新", "mode": "overwrite"}, {})

        assert result["success"] is True
        assert path.read_text(encoding="utf-8") == "新"

    @pytest.mark.parametrize("mode", [None, "delete", "w"])
    async def test_rejects_unknown_mode(self, tool, use_config, mode) -> None:
        """未知の `mode` は拒否する。"""
        result = await tool.execute({"path": "work/x.txt", "content": "x", "mode": mode}, {})

        assert result["success"] is False

    async def test_rejects_non_string_content(self, tool, use_config) -> None:
        """`content` が文字列でなければ拒否する。"""
        result = await tool.execute({"path": "work/x.txt", "content": 1, "mode": "create"}, {})

        assert result["success"] is False


class TestRejects:
    """書き込みの拒否。"""

    async def test_read_only_dir(self, tool, use_config, dirs) -> None:
        """読み取り専用ディレクトリには書けない。"""
        result = await tool.execute({"path": "docs/new.txt", "content": "x", "mode": "create"}, {})

        assert result["success"] is False
        assert not (dirs["docs"] / "new.txt").exists()

    async def test_nested_read_only_dir(self, tool, use_config, dirs) -> None:
        """書き込み可能ディレクトリの中の読み取り専用ディレクトリには書けない。"""
        result = await tool.execute(
            {"path": "work/archive/old.txt", "content": "x", "mode": "overwrite"}, {}
        )

        assert result["success"] is False
        assert (dirs["archive"] / "old.txt").read_text(encoding="utf-8") == "old\n"

    async def test_parent_traversal(self, tool, use_config, dirs) -> None:
        """`..` で外へ出る書き込みは拒否する。"""
        result = await tool.execute(
            {"path": "work/../outside/x.txt", "content": "x", "mode": "create"}, {}
        )

        assert result["success"] is False
        assert not (dirs["outside"] / "x.txt").exists()

    async def test_symlinked_dir_to_outside(self, tool, use_config, dirs) -> None:
        """外を指すシンボリックリンクのディレクトリ経由の書き込みは拒否する。"""
        os.symlink(dirs["outside"], dirs["work"] / "link")

        result = await tool.execute({"path": "work/link/x.txt", "content": "x", "mode": "create"}, {})

        assert result["success"] is False
        assert result["error"] == OUTSIDE_MESSAGE
        assert not (dirs["outside"] / "x.txt").exists()

    async def test_symlinked_file_to_outside(self, tool, use_config, dirs) -> None:
        """外を指すシンボリックリンクのファイルは上書きできない。"""
        os.symlink(dirs["outside"] / "secret.txt", dirs["work"] / "secret.txt")

        result = await tool.execute(
            {"path": "work/secret.txt", "content": "x", "mode": "overwrite"}, {}
        )

        assert result["success"] is False
        assert (dirs["outside"] / "secret.txt").read_text(encoding="utf-8") == "secret hello\n"

    async def test_directory_itself(self, tool, use_config) -> None:
        """ディレクトリそのものには書けない。"""
        for path in ("work", "work/archive"):
            result = await tool.execute({"path": path, "content": "x", "mode": "overwrite"}, {})
            assert result["success"] is False

    async def test_parent_is_a_file(self, tool, use_config) -> None:
        """親のパスがファイルならエラー。"""
        result = await tool.execute(
            {"path": "work/memo.txt/child.txt", "content": "x", "mode": "create"}, {}
        )

        assert result["success"] is False
