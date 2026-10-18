"""`lilla_core.extensions.google_drive` 同梱の `tools/llm_google_drive_search_text.py` のテスト。"""
from __future__ import annotations

import pytest

from lilla_core.core.exceptions import ReauthenticationRequiredError


@pytest.fixture
def tool(load_tool):
    """ツール本体をロードする。"""
    return load_tool("llm_google_drive_search_text")


def _meta(fake_drive, *ids: str) -> list[dict]:
    """偽の Drive のメタデータを検索結果の形で並べる。"""
    return [dict(fake_drive.files[i]) for i in ids]


class TestSchema:
    """SCHEMA の構造。"""

    def test_function_name_and_parameters(self, tool) -> None:
        """関数名は `google_drive_search_text`、`query` が必須で `limit` は任意。"""
        function = tool.SCHEMA["function"]
        assert function["name"] == "google_drive_search_text"
        assert function["parameters"]["required"] == ["query"]
        assert set(function["parameters"]["properties"]) == {"query", "limit"}


class TestValidation:
    """入力の検証。"""

    @pytest.mark.parametrize("query", [None, "", "   ", 1])
    async def test_rejects_missing_query(self, tool, fake_drive, query) -> None:
        """`query` が無い・空なら API を呼ばない。"""
        result = await tool.execute({"query": query}, {})

        assert result["success"] is False
        fake_drive.search_files.assert_not_awaited()

    @pytest.mark.parametrize("limit", [0, -1, "5", 1.5, True])
    async def test_rejects_invalid_limit(self, tool, fake_drive, limit) -> None:
        """`limit` は 1 以上の整数だけ。"""
        result = await tool.execute({"query": "x", "limit": limit}, {})

        assert result["success"] is False
        fake_drive.search_files.assert_not_awaited()


class TestSearch:
    """検索と絞り込み。"""

    async def test_builds_escaped_text_query(self, tool, fake_drive) -> None:
        """全文検索・`text/*`・ゴミ箱除外の条件で、キーワードはエスケープして渡す。"""
        await tool.execute({"query": " it's \\ "}, {})

        q = fake_drive.search_files.await_args.args[0]
        assert q == (
            "fullText contains 'it\\'s \\\\' and mimeType contains 'text/' and trashed = false"
        )

    async def test_returns_only_whitelisted_text_files(self, tool, fake_drive) -> None:
        """ホワイトリスト外・`text/*` 以外・ゴミ箱は除き、書き込めるかを添える。"""
        fake_drive.search_pages = [
            {
                "files": _meta(
                    fake_drive, "out-note", "ro-note", "rw-json", "rw-trashed", "rw-note"
                )
            }
        ]

        result = await tool.execute({"query": "本文"}, {})

        assert result["success"] is True
        files = result["data"]["files"]
        assert [(f["id"], f["writable"]) for f in files] == [
            ("ro-note", False),
            ("rw-note", True),
        ]
        assert "parents" not in files[0]
        assert result["data"]["more_results_may_exist"] is False

    async def test_follows_pages_until_limit(self, tool, fake_drive) -> None:
        """続きのページを辿り、`limit` 件で止めて続きの有無を返す。"""
        fake_drive.search_pages = [
            {"files": _meta(fake_drive, "out-note", "ro-note"), "nextPageToken": "1"},
            {"files": _meta(fake_drive, "rw-note", "ro-note"), "nextPageToken": "2"},
            {"files": _meta(fake_drive, "rw-note")},
        ]

        result = await tool.execute({"query": "x", "limit": 2}, {})

        assert [f["id"] for f in result["data"]["files"]] == ["ro-note", "rw-note"]
        assert result["data"]["more_results_may_exist"] is True
        assert fake_drive.search_files.await_count == 2

    async def test_stops_at_page_limit(self, tool, fake_drive) -> None:
        """ページ数の上限で打ち切ったら、続きがあるかもしれないと返す。"""
        fake_drive.search_pages = [
            {"files": _meta(fake_drive, "out-note"), "nextPageToken": str(i + 1)}
            for i in range(tool._MAX_PAGES + 1)
        ]

        result = await tool.execute({"query": "x"}, {})

        assert result["data"]["files"] == []
        assert result["data"]["more_results_may_exist"] is True
        assert fake_drive.search_files.await_count == tool._MAX_PAGES

    async def test_clamps_limit(self, tool, fake_drive) -> None:
        """上限を超える `limit` は上限に丸める。"""
        fake_drive.search_pages = [{"files": _meta(fake_drive, "ro-note") * 60}]

        result = await tool.execute({"query": "x", "limit": 1000}, {})

        assert len(result["data"]["files"]) == tool.MAX_LIMIT

    async def test_empty_whitelist_returns_nothing(self, tool, fake_drive, drive_config) -> None:
        """ホワイトリストが空なら API を呼ばずに 0 件を返す。"""
        drive_config.read_only_folders = []
        drive_config.writable_folders = []

        result = await tool.execute({"query": "x"}, {})

        assert result["success"] is True
        assert result["data"]["files"] == []
        fake_drive.search_files.assert_not_awaited()

    async def test_reauth(self, tool, fake_drive) -> None:
        """再認証が必要なら認可 URL を返す。"""
        fake_drive.search_files.side_effect = ReauthenticationRequiredError("x")

        result = await tool.execute({"query": "x"}, {})

        assert result["needs_auth"] is True
