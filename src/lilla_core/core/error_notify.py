"""エラー出力（ERROR ログ + Discord エラー通知チャンネル）を一元化するユーティリティ。

コマンド実行系・定期タスク実行系のエラーは、元のチャンネルへ `message.reply()` で
返信せず、この `notify_error` を通して「ERROR ログ」と「`discord.error_channel_id` で
設定したチャンネル」の 2 箇所にのみ出力する。bot 間メッセージのチャンネルでエラーが
発生したときに、相手側の bot が reply に反応してしまうのを防ぐため。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def notify_error(bot, context: str, error: Exception | str) -> None:
    """ERROR ログ出力 + error_channel_id への通知を1箇所で行う。

    error_channel_id が未設定/不正/見つからない場合は WARNING ログのみでスキップする。
    Discord への送信に失敗した場合も例外を投げず WARNING ログに留めるため、
    呼び出し側は例外処理を意識しなくてよい。

    Args:
        bot: Discord クライアント。None の場合は通知をスキップする。
        context: エラーの文脈を表す文言（例: `"!mongodata: DB操作エラー"`）。
        error: 例外オブジェクトまたはエラーメッセージ文字列。
            例外の場合はトレースバックもログに出力する。
    """
    exc_info = error if isinstance(error, BaseException) else False
    logger.error("%s: %s", context, error, exc_info=exc_info)

    try:
        await _send_to_error_channel(bot, f"{context}: {error}")
    except Exception as e:  # 通知の失敗で呼び出し元を巻き込まない
        logger.warning("Failed to notify error channel: %s", e)


async def notify_warning(
    bot,
    message: str,
    *,
    attachment_name: str | None = None,
    attachment_text: str | None = None,
) -> None:
    """WARNING ログ出力 + error_channel_id への通知を 1 箇所で行う（エラーではない知らせ用）。

    `notify_error` と同じく、通知先が無い・送信に失敗した場合も例外を投げない。

    Args:
        bot: Discord クライアント。None の場合は通知をスキップする。
        message: 知らせる文言（Discord にそのまま送る。2000 文字を超えた分は切り詰める）。
        attachment_name: 添付ファイルの名前（`attachment_text` と組で指定）。
        attachment_text: 添付ファイルの中身（UTF-8 のテキスト）。
    """
    logger.warning("%s", message)
    file = None
    if attachment_name and attachment_text is not None:
        import io

        import discord

        file = discord.File(io.BytesIO(attachment_text.encode("utf-8")), filename=attachment_name)
    try:
        await _send_to_error_channel(bot, message[:_DISCORD_MESSAGE_LIMIT], file=file)
    except Exception as e:  # 通知の失敗で呼び出し元を巻き込まない
        logger.warning("Failed to notify error channel: %s", e)


#: Discord の 1 メッセージの文字数の上限。
_DISCORD_MESSAGE_LIMIT = 2000


async def _send_to_error_channel(bot, error_message: str, file=None) -> None:
    """設定済みのエラーチャンネルにエラーメッセージを送信する。

    チャンネルが設定されていない、ID が不正、または見つからない場合はログに
    警告を出力してスキップする。

    Args:
        bot: Discord クライアント。
        error_message: 送信するメッセージ本文。
        file: 添付する `discord.File`（任意）。
    """
    from lilla_core.core.config import get_config

    channel_id = get_config().discord.error_channel_id
    if not channel_id:
        logger.warning("Cannot notify error because error channel is not configured")
        return
    if bot is None:
        logger.warning("Cannot notify error because Discord client is unavailable")
        return
    try:
        channel = bot.get_channel(int(channel_id))
    except (TypeError, ValueError):
        logger.warning("Error channel ID is invalid: %s", channel_id)
        return
    if channel is None:
        logger.warning("Error channel not found: %s", channel_id)
        return
    if file is None:
        await channel.send(error_message)
    else:
        await channel.send(error_message, file=file)
