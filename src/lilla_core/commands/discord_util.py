"""コマンド共通の Discord ユーティリティ。

複数のコマンド（`toolresult` / `cleardirty`）が共有するチャンネル解決処理と、
Discord の 1 メッセージ上限に収めるための行単位の分割処理（Discord 用のコマンド
コンテキスト `handlers/command_handler.DiscordCommandContext` の返信）を置く。
コマンドを登録しないモジュールなので `load_all_commands()` から import されても
レジストリには影響しない。
"""
from __future__ import annotations

import logging

import discord

logger = logging.getLogger(__name__)

# Discord の 1 メッセージ上限（2000 文字）に余裕を持たせた分割幅
DISCORD_CHUNK_SIZE = 1900


def chunk_lines(text: str, size: int = DISCORD_CHUNK_SIZE) -> list[str]:
    """テキストを行の境界で `size` 文字以下のチャンクに分割する。

    行（改行区切り）を順に詰め、次の行を足すと `size` を超えるところで区切る。
    区切りに使った改行はチャンクに含めない。1 行だけで `size` を超える場合は、
    その行を文字数で分割する。

    Args:
        text: 分割するテキスト。
        size: 1 チャンクの最大文字数（1 以上）。

    Returns:
        チャンクのリスト。`text` が空なら `[""]`。
    """
    if size < 1:
        raise ValueError("size must be at least 1")
    chunks: list[str] = []
    current: str | None = None
    for line in text.split("\n"):
        pieces = [line[i:i + size] for i in range(0, len(line), size)] or [""]
        for piece in pieces:
            if current is None:
                current = piece
            elif len(current) + 1 + len(piece) <= size:
                current = f"{current}\n{piece}"
            else:
                chunks.append(current)
                current = piece
    chunks.append(current if current is not None else "")
    return chunks


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
