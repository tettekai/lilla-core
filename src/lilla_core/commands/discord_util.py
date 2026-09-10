"""コマンド共通の Discord ユーティリティ。

複数のコマンド（`toolresult` / `cleardirty`）が共有するチャンネル解決処理を置く。
コマンドを登録しないモジュールなので `load_all_commands()` から import されても
レジストリには影響しない。
"""
from __future__ import annotations

import logging

import discord

logger = logging.getLogger(__name__)


async def resolve_discord_channel(channel_id, bot):
    """channel_id からチャンネルを解決する。

    まずキャッシュ（`get_channel`、API 呼び出しなし）を試し、ミスした場合のみ
    `fetch_channel`（API 問い合わせ）で確実に確認する。Bot 再起動直後や久しく通信の
    ない DM ではキャッシュミスが起きやすいため、キャッシュだけに頼ると解決できたはずの
    チャンネルを取りこぼす。

    Args:
        channel_id: 解決するチャンネルの ID。
        bot: Discord クライアント。

    Returns:
        解決したチャンネル。解決できない場合は None。
    """
    channel = bot.get_channel(int(channel_id))
    if channel is not None:
        return channel
    try:
        return await bot.fetch_channel(int(channel_id))
    except discord.NotFound:
        logger.warning("Channel not found: %s", channel_id)
    except discord.Forbidden:
        logger.warning("No access to channel: %s", channel_id)
    except discord.HTTPException as e:
        logger.warning("Failed to fetch channel: %s (%s)", channel_id, e)
    return None
