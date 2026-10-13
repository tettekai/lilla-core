"""commands/discord_util.py のテスト。"""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from lilla_core.commands.discord_util import (  # noqa: E402
    DISCORD_CHUNK_SIZE,
    chunk_lines,
    resolve_discord_channel,
)


# 他のテストが discord をモックした状態でこのモジュールが読み込まれていると、
# モジュール内の discord が MagicMock のままになり実例外を捕捉できない。
# 実 discord を掴み直させるため、import 前に読み込み済みの状態を捨てる。
sys.modules.pop("lilla_core.commands.discord_util", None)


def _make_http_error(error_class) -> Exception:
    """discord の HTTP 系例外インスタンスを生成する（response は最小限のモック）。"""
    return error_class(MagicMock(status=404, reason="Not Found"), "エラー")


class TestResolveDiscordChannel:
    async def test_returns_cached_channel_without_api_call(self) -> None:
        """キャッシュにヒットした場合は fetch_channel を呼ばない。"""
        channel = MagicMock()
        bot = MagicMock()
        bot.get_channel = MagicMock(return_value=channel)
        bot.fetch_channel = AsyncMock()

        assert await resolve_discord_channel(555, bot) is channel
        bot.get_channel.assert_called_once_with(555)
        bot.fetch_channel.assert_not_called()

    async def test_fetches_channel_on_cache_miss(self) -> None:
        """キャッシュミス時のみ fetch_channel で問い合わせ、その結果を返す。"""
        fetched = MagicMock()
        bot = MagicMock()
        bot.get_channel = MagicMock(return_value=None)
        bot.fetch_channel = AsyncMock(return_value=fetched)

        assert await resolve_discord_channel(555, bot) is fetched
        bot.fetch_channel.assert_called_once_with(555)

    async def test_accepts_channel_id_as_string(self) -> None:
        """文字列のチャンネル ID も int に変換して解決する。"""
        channel = MagicMock()
        bot = MagicMock()
        bot.get_channel = MagicMock(return_value=channel)

        assert await resolve_discord_channel("555", bot) is channel
        bot.get_channel.assert_called_once_with(555)

    @pytest.mark.parametrize("error_name", ["NotFound", "Forbidden", "HTTPException"])
    async def test_returns_none_on_fetch_error(self, error_name: str) -> None:
        """fetch_channel が Discord の HTTP 系例外を投げた場合は None を返す。"""
        import discord

        bot = MagicMock()
        bot.get_channel = MagicMock(return_value=None)
        bot.fetch_channel = AsyncMock(
            side_effect=_make_http_error(getattr(discord, error_name))
        )

        assert await resolve_discord_channel(555, bot) is None


class TestChunkLines:
    def test_short_text_is_single_chunk(self) -> None:
        """上限以内のテキストはそのまま 1 チャンクになる。"""
        assert chunk_lines("a\nb\nc", size=10) == ["a\nb\nc"]

    def test_empty_text(self) -> None:
        """空文字列は空のチャンク 1 つを返す。"""
        assert chunk_lines("") == [""]

    def test_splits_on_line_boundaries(self) -> None:
        """次の行を足すと上限を超えるところで、行の境界で区切る。"""
        assert chunk_lines("aaa\nbbb\nccc", size=7) == ["aaa\nbbb", "ccc"]

    def test_exact_size_fits(self) -> None:
        """改行を含めてちょうど上限の長さなら同じチャンクに入れる。"""
        assert chunk_lines("abc\ndef", size=7) == ["abc\ndef"]

    def test_long_line_is_split_by_characters(self) -> None:
        """1 行だけで上限を超える行は文字数で分割する。"""
        assert chunk_lines("x\n" + "y" * 12 + "\nz", size=5) == [
            "x", "yyyyy", "yyyyy", "yy\nz",
        ]

    def test_all_chunks_within_default_size(self) -> None:
        """既定の分割幅（1900）を超えるチャンクを作らず、改行でつなぐと元に戻る。"""
        text = "\n".join("行" * (i % 300) for i in range(200))
        chunks = chunk_lines(text)

        assert DISCORD_CHUNK_SIZE == 1900
        assert len(chunks) > 1
        assert all(len(c) <= DISCORD_CHUNK_SIZE for c in chunks)
        assert "\n".join(chunks) == text

    def test_rejects_non_positive_size(self) -> None:
        """size が 1 未満なら ValueError。"""
        with pytest.raises(ValueError):
            chunk_lines("abc", size=0)
