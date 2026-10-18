"""`lilla_core.extensions.files` 同梱の `tools/llm_file_list.py` のテスト。"""
from __future__ import annotations

import os

import pytest

from lilla_core.extensions import files
from lilla_core.extensions.files.access import NOT_FOUND_MESSAGE, OUTSIDE_MESSAGE


@pytest.fixture
def tool(load_tool):
    """ツール本体をロードする。"""
    return load_tool("llm_file_list")


class TestSchema:
    """SCHEMA の構造。"""

    def test_function_name_and_parameters(self, tool) -> None:
        """関数名は `file_list` で、`path` が必須・`pattern` が任意。"""
        function = tool.SCHEMA["function"]
        assert function["name"] == "file_list"
        assert function["parameters"]["required"] == ["path"]
        assert "pattern" in function["parameters"]["properties"]


class TestList:
    """一覧。"""

    async def test_empty_path_lists_roots(self, tool, use_config) -> None:
        """空文字はホワイトリストのディレクトリの一覧を返す。"""
        result = await tool.execute({"path": ""}, {})

        assert result["success"] is True
        entries = {e["path"]: e for e in result["data"]["entries"]}
        assert set(entries) == {"docs", "archive", "work"}
        assert entries["work"]["writable"] is True
        assert entries["docs"]["writable"] is False
        assert all(e["type"] == "dir" for e in entries.values())

    async def test_lists_direct_children(self, tool, use_config) -> None:
        """直下だけを名前順に返し、ファイルにはサイズと更新日時が付く。"""
        result = await tool.execute({"path": "docs"}, {})

        assert result["success"] is True
        entries = result["data"]["entries"]
        assert [e["path"] for e in entries] == ["docs/readme.md", "docs/sub"]
        readme, sub = entries
        assert readme["type"] == "file"
        assert readme["size"] == len("# Readme\nhello world\n")
        assert readme["modified_time"].endswith("Z")
        assert sub["type"] == "dir"
        assert "size" not in sub

    async def test_dir_path_can_be_listed_again(self, tool, use_config) -> None:
        """`dir` のエントリの `path` をそのまま渡せば中を一覧できる。"""
        first = await tool.execute({"path": "docs"}, {})
        sub = next(e for e in first["data"]["entries"] if e["type"] == "dir")

        result = await tool.execute({"path": sub["path"]}, {})

        assert [e["path"] for e in result["data"]["entries"]] == ["docs/sub/deep.txt"]

    async def test_pattern_filters_names(self, tool, use_config) -> None:
        """`pattern` のグロブで名前を絞り込む。"""
        result = await tool.execute({"path": "docs", "pattern": "*.md"}, {})

        assert [e["name"] for e in result["data"]["entries"]] == ["readme.md"]

    async def test_nested_read_only_dir_is_not_writable(self, tool, use_config) -> None:
        """書き込み可能ディレクトリの中の読み取り専用ディレクトリは `writable: false`。"""
        result = await tool.execute({"path": "work"}, {})

        entries = {e["name"]: e for e in result["data"]["entries"]}
        assert entries["memo.txt"]["writable"] is True
        assert entries["archive"]["writable"] is False
        assert result["data"]["writable"] is True

    async def test_backslash_separator(self, tool, use_config) -> None:
        """区切りは `\\` でもよい。"""
        result = await tool.execute({"path": "docs\\sub"}, {})

        assert result["success"] is True
        assert result["data"]["path"] == "docs/sub"


class TestRejects:
    """ホワイトリスト外へのアクセスの拒否。"""

    async def test_unknown_directory(self, tool, use_config) -> None:
        """名前の分からないディレクトリは見つからない扱い。"""
        result = await tool.execute({"path": "outside"}, {})

        assert result["success"] is False
        assert result["error"] == NOT_FOUND_MESSAGE

    @pytest.mark.parametrize("path", ["docs/..", "docs/../outside", "../outside", "/etc", "C:\\x"])
    async def test_parent_and_absolute_paths(self, tool, use_config, path: str) -> None:
        """`..`・絶対パスは拒否する。"""
        result = await tool.execute({"path": path}, {})

        assert result["success"] is False

    async def test_symlinked_dir_to_outside(self, tool, use_config, dirs) -> None:
        """外へのシンボリックリンクを辿った一覧は拒否する（実体パスで判定）。"""
        os.symlink(dirs["outside"], dirs["docs"] / "link")

        result = await tool.execute({"path": "docs/link"}, {})

        assert result["success"] is False
        assert result["error"] == OUTSIDE_MESSAGE

    async def test_hides_entries_linking_outside(self, tool, use_config, dirs) -> None:
        """外を指すリンクは一覧に出さない。"""
        os.symlink(dirs["outside"] / "secret.txt", dirs["docs"] / "secret-link.txt")

        result = await tool.execute({"path": "docs"}, {})

        assert "secret-link.txt" not in [e["name"] for e in result["data"]["entries"]]

    async def test_empty_whitelist_denies_everything(self, tool, monkeypatch) -> None:
        """ホワイトリストが両方とも空ならすべて拒否する。"""
        from lilla_core.extensions.files import tool_support

        monkeypatch.setattr(tool_support, "load_config", lambda: files.FilesConfig())

        result = await tool.execute({"path": ""}, {})

        assert result["success"] is False

    async def test_file_is_not_a_directory(self, tool, use_config) -> None:
        """ファイルを一覧しようとしたらエラー。"""
        result = await tool.execute({"path": "docs/readme.md"}, {})

        assert result["success"] is False

    async def test_missing_directory(self, tool, use_config) -> None:
        """存在しないディレクトリはエラー。"""
        result = await tool.execute({"path": "docs/nope"}, {})

        assert result["success"] is False
