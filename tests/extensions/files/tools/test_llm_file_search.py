"""`lilla_core.extensions.files` 同梱の `tools/llm_file_search.py` のテスト。"""
from __future__ import annotations

import codecs
import os

import pytest


@pytest.fixture
def tool(load_tool):
    """ツール本体をロードする。"""
    return load_tool("llm_file_search")


class TestSchema:
    """SCHEMA の構造。"""

    def test_function_name_and_parameters(self, tool) -> None:
        """関数名は `file_search` で、`path` と `query` が必須。"""
        function = tool.SCHEMA["function"]
        assert function["name"] == "file_search"
        assert function["parameters"]["required"] == ["path", "query"]


class TestSearch:
    """検索。"""

    async def test_finds_lines_recursively(self, tool, use_config) -> None:
        """配下を再帰的に辿り、path・行番号・行を返す（大文字小文字は区別しない）。"""
        result = await tool.execute({"path": "docs", "query": "hello"}, {})

        assert result["success"] is True
        assert result["data"]["matches"] == [
            {"path": "docs/readme.md", "line": 2, "text": "hello world"},
            {"path": "docs/sub/deep.txt", "line": 2, "text": "Hello again"},
        ]
        assert result["data"]["truncated"] is False

    async def test_empty_path_searches_all_roots(self, tool, use_config) -> None:
        """空文字はホワイトリストのディレクトリすべてを探し、外は探さない。"""
        result = await tool.execute({"path": "", "query": "hello"}, {})

        paths = [m["path"] for m in result["data"]["matches"]]
        assert paths == ["docs/readme.md", "docs/sub/deep.txt"]

    async def test_single_file(self, tool, use_config) -> None:
        """ファイルを指せばそのファイルだけを探す。"""
        result = await tool.execute({"path": "docs/sub/deep.txt", "query": "line"}, {})

        assert result["data"]["matches"] == [
            {"path": "docs/sub/deep.txt", "line": 1, "text": "deep line"}
        ]

    async def test_skips_binary_and_non_utf8(self, tool, use_config, dirs) -> None:
        """バイナリと UTF-8 で読めないファイルは黙って飛ばし、BOM 付きは読む。"""
        (dirs["work"] / "bin.dat").write_bytes(b"hello\x00world")
        (dirs["work"] / "sjis.txt").write_bytes("hello 日本語".encode("shift_jis"))
        (dirs["work"] / "bom.txt").write_bytes(codecs.BOM_UTF8 + b"hello bom")

        result = await tool.execute({"path": "work", "query": "hello"}, {})

        assert result["success"] is True
        assert result["data"]["matches"] == [{"path": "work/bom.txt", "line": 1, "text": "hello bom"}]

    async def test_truncates_results(self, tool, use_config, dirs, monkeypatch) -> None:
        """上限を超えたら切り詰めて `truncated: true`。"""
        monkeypatch.setattr(tool, "MAX_SEARCH_RESULTS", 2)
        (dirs["work"] / "many.txt").write_text("x\nx\nx\n", encoding="utf-8")

        result = await tool.execute({"path": "work", "query": "x"}, {})

        assert len(result["data"]["matches"]) == 2
        assert result["data"]["truncated"] is True

    async def test_truncates_scanned_files(self, tool, use_config, dirs, monkeypatch) -> None:
        """読むファイル数の上限を超えても `truncated: true`。"""
        monkeypatch.setattr(tool, "MAX_SEARCH_FILES", 1)

        result = await tool.execute({"path": "docs", "query": "hello"}, {})

        assert len(result["data"]["matches"]) == 1
        assert result["data"]["truncated"] is True

    async def test_skips_links_to_outside(self, tool, use_config, dirs) -> None:
        """外を指すファイル・ディレクトリのリンクは辿らない。"""
        os.symlink(dirs["outside"] / "secret.txt", dirs["work"] / "secret.txt")
        os.symlink(dirs["outside"], dirs["work"] / "outdir")

        result = await tool.execute({"path": "work", "query": "secret"}, {})

        assert result["data"]["matches"] == []

    @pytest.mark.parametrize("query", [None, "", "  "])
    async def test_rejects_blank_query(self, tool, use_config, query) -> None:
        """空のキーワードは拒否する。"""
        result = await tool.execute({"path": "docs", "query": query}, {})

        assert result["success"] is False

    async def test_rejects_outside(self, tool, use_config) -> None:
        """`..` で外を探すことはできない。"""
        result = await tool.execute({"path": "docs/../outside", "query": "secret"}, {})

        assert result["success"] is False
