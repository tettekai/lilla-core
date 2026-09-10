"""commands/discord_util.py のテスト。"""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from lilla_core.commands.discord_util import resolve_discord_channel  # noqa: E402


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
