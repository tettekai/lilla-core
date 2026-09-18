"""builtin_tools/llm_conversation_get.py のテスト。"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest


class _Channels:
    """`discord.channels` の登録リストを模したヘルパー。"""

    def __init__(self, entries: list[SimpleNamespace]) -> None:
        self._entries = entries

    def find_channel_by_name(self, name: str):
        """前後空白を除いた完全一致で登録エントリを返す（コアの実装と同じ規則）。"""
        key = name.strip()
        for entry in self._entries:
            if entry.name == key:
                return entry
        return None


@pytest.fixture
def mock_cfg() -> MagicMock:
    """`discord.channels` を 1 件だけ持つ AppConfig モック。"""
    cfg = MagicMock()
    cfg.paths.tool_root = Path("/fake/tools")
    cfg.env.config_root = Path("/fake/config")
    cfg.ui.timezone = "Asia/Tokyo"
    cfg.discord = _Channels(
        [SimpleNamespace(name="diary", channel_id="123456789012345678")]
    )
    return cfg


@pytest.fixture
def mock_repo() -> MagicMock:
    """`search()` を持つ ConversationRepository モック。"""
    repo = MagicMock()
    repo.search = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def with_mocked_modules(mock_cfg: MagicMock, mock_repo: MagicMock):
    """設定とリポジトリを patch.dict で差し替える。"""
    with patch.dict(
        sys.modules,
        {
            "lilla_core.core.config": MagicMock(get_config=lambda: mock_cfg),
            "lilla_core.repository.conversation_repository": MagicMock(
                get_conversation_repo=lambda: mock_repo
            ),
        },
    ):
        yield


@pytest.fixture
def tool(with_mocked_modules):
    """patch.dict 有効後にツールモジュールをロードする。"""
    sys.modules.pop("lilla_core.builtin_tools.llm_conversation_get", None)
    import lilla_core.builtin_tools.llm_conversation_get as loaded
    yield loaded
    sys.modules.pop("lilla_core.builtin_tools.llm_conversation_get", None)


class TestLlmConversationGet:
    def test_schema_has_channel_name(self, tool) -> None:
        """SCHEMA に任意パラメータ channel_name がある（required には入らない）。"""
        params = tool.SCHEMA["function"]["parameters"]
        assert tool.SCHEMA["function"]["name"] == "get_conversations"
        assert "channel_name" in params["properties"]
        assert params["required"] == []
        description = params["properties"]["channel_name"]["description"]
        assert "登録名" in description
        assert "Discord の現在のチャンネル名ではない" in description

    async def test_loads_via_import_path(self, with_mocked_modules, tmp_path: Path) -> None:
        """type: lilla_core.builtin_tools.llm_conversation_get の YAML からロードできる。"""
        sys.modules.pop("lilla_core.loaders.llm_tool_loader", None)
        import lilla_core.loaders.llm_tool_loader as llm_tool_loader

        config_root = tmp_path / "config_root"
        tools_dir = config_root / "tools"
        tools_dir.mkdir(parents=True)
        (tools_dir / "llm_conversation_get.yaml").write_text(
            "type: lilla_core.builtin_tools.llm_conversation_get\n", encoding="utf-8"
        )
        try:
            result = llm_tool_loader.load_llm_tools(tool_roots=[], config_root=config_root)
        finally:
            sys.modules.pop("lilla_core.loaders.llm_tool_loader", None)

        # ローダーは LLM へ見せる名前を YAML の stem で上書きする。
        assert "llm_conversation_get" in result
        assert callable(result["llm_conversation_get"]["execute"])

    async def test_without_channel_name_searches_all_channels(
        self, tool, mock_repo: MagicMock
    ) -> None:
        """channel_name 省略時は discord_channel_id で絞らない。"""
        result = await tool.execute({}, {})

        assert result["success"] is True
        assert mock_repo.search.await_args.kwargs["discord_channel_id"] is None
        assert result["data"]["channel_name"] is None

    async def test_registered_channel_name_narrows_search(
        self, tool, mock_repo: MagicMock
    ) -> None:
        """登録済みチャンネル名を指定すると該当 discord_channel_id で絞る。"""
        result = await tool.execute({"channel_name": " diary "}, {})

        assert result["success"] is True
        assert mock_repo.search.await_args.kwargs["discord_channel_id"] == 123456789012345678

    async def test_unknown_channel_name_returns_error(
        self, tool, mock_repo: MagicMock
    ) -> None:
        """未登録のチャンネル名はエラーを返し、全件検索へ落とさない。"""
        result = await tool.execute({"channel_name": "unknown"}, {})

        assert result["success"] is False
        assert "unknown" in result["error"]
        mock_repo.search.assert_not_awaited()

    async def test_case_sensitive_channel_name_returns_error(
        self, tool, mock_repo: MagicMock
    ) -> None:
        """名前解決は大文字小文字を区別する。"""
        result = await tool.execute({"channel_name": "Diary"}, {})

        assert result["success"] is False
        mock_repo.search.assert_not_awaited()

    async def test_passes_range_keywords_role_and_limit(
        self, tool, mock_repo: MagicMock
    ) -> None:
        """期間・キーワード・role・limit をリポジトリへ渡す。"""
        result = await tool.execute(
            {
                "datetime_range": "2026-04-20",
                "query": "体調 睡眠",
                "role": "user",
                "limit": 5,
            },
            {},
        )

        assert result["success"] is True
        kwargs = mock_repo.search.await_args.kwargs
        assert kwargs["keywords"] == ["体調", "睡眠"]
        assert kwargs["role"] == "user"
        assert kwargs["limit"] == 5
        # 期間の境界は解決済みタイムゾーン（Asia/Tokyo）の暦日で aware になる。
        assert kwargs["start"].utcoffset().total_seconds() == 9 * 3600
        assert kwargs["start"].strftime("%Y-%m-%d %H:%M") == "2026-04-20 00:00"
        assert kwargs["end"].strftime("%Y-%m-%d %H:%M") == "2026-04-20 23:59"

    async def test_role_all_is_not_passed_to_repository(
        self, tool, mock_repo: MagicMock
    ) -> None:
        """role="all" は発言者で絞らない。"""
        await tool.execute({"role": "all"}, {})

        assert mock_repo.search.await_args.kwargs["role"] is None

    async def test_limit_is_capped(self, tool, mock_repo: MagicMock) -> None:
        """limit は MAX_LIMIT で頭打ちにする。"""
        await tool.execute({"limit": 1000}, {})

        assert mock_repo.search.await_args.kwargs["limit"] == tool.MAX_LIMIT

    @pytest.mark.parametrize("raw", [0, -1, -100])
    async def test_non_positive_limit_is_normalized_to_one(
        self, tool, mock_repo: MagicMock, raw: int
    ) -> None:
        """0 や負数の limit は 1 へ丸める（MongoDB の limit(0) は制限なしのため）。"""
        await tool.execute({"limit": raw}, {})

        assert mock_repo.search.await_args.kwargs["limit"] == 1

    @pytest.mark.parametrize("raw", ["abc", None, [5]])
    async def test_non_integer_limit_falls_back_to_default(
        self, tool, mock_repo: MagicMock, raw
    ) -> None:
        """整数として解釈できない limit は既定値へ落とす。"""
        await tool.execute({"limit": raw}, {})

        assert mock_repo.search.await_args.kwargs["limit"] == tool.MAX_LIMIT

    async def test_formats_results_in_local_timezone(
        self, tool, mock_repo: MagicMock
    ) -> None:
        """結果の time は解決済みタイムゾーンへ変換して返す。"""
        mock_repo.search.return_value = [
            {
                "time": datetime(2026, 4, 20, 0, 30, tzinfo=timezone.utc),
                "message": {"role": "user", "content": "おはよう"},
            }
        ]

        result = await tool.execute({}, {})

        assert result["data"]["total"] == 1
        assert result["data"]["results"][0] == {
            "time": "2026-04-20 09:30:00",
            "role": "user",
            "content": "おはよう",
        }

    async def test_extracts_text_blocks_from_content_list(
        self, tool, mock_repo: MagicMock
    ) -> None:
        """画像添付つきのブロックリストからは text ブロックだけを取り出す。"""
        mock_repo.search.return_value = [
            {
                "time": datetime(2026, 4, 20, 0, 0, tzinfo=ZoneInfo("Asia/Tokyo")),
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "これ見て"},
                        {"type": "image_url", "image_url": {"url": "data:..."}},
                    ],
                },
            }
        ]

        result = await tool.execute({}, {})

        assert result["data"]["results"][0]["content"] == "これ見て"

    async def test_invalid_datetime_range_returns_error(
        self, tool, mock_repo: MagicMock
    ) -> None:
        """解釈できない datetime_range はエラーを返す。"""
        result = await tool.execute({"datetime_range": "not_a_range"}, {})

        assert result["success"] is False
        mock_repo.search.assert_not_awaited()

    async def test_repository_failure_returns_error(
        self, tool, mock_repo: MagicMock
    ) -> None:
        """リポジトリの例外はエラー結果へ変換し、外へ投げない。"""
        mock_repo.search.side_effect = RuntimeError("mongo is down")

        result = await tool.execute({}, {})

        assert result["success"] is False
        assert "mongo is down" in result["error"]
