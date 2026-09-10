"""`!cleardirty` コマンド。

`!toolresult` の配送で保存された「要確認」の返信を、会話履歴と Discord の
両方から取り消す。
"""
from __future__ import annotations

import logging

from lilla_core.commands.discord_util import resolve_discord_channel
from lilla_core.commands.registry import register_command
from lilla_core.ui.messages import t

logger = logging.getLogger(__name__)


@register_command("cleardirty")
async def handle_cleardirty(message, arg: str, tools: dict, bot) -> None:
    """tags に "dirty" を含む直近 1 件の会話履歴と、対応する Discord メッセージを削除する。

    `!toolresult` の配送で保存された返信は、外部エージェント由来のテキストを LLM に
    読ませて生成したものであり、プロンプトインジェクションの残存リスクがある。汚染が
    疑われる場合に、その返信を会話履歴からも Discord からも速やかに取り消すためのコマンド。

    連続して実行すれば、新しいものから順に 1 件ずつ取り消せる。Discord メッセージ情報が
    残っていない場合（Discord 以外のクライアント経由の配送など）は会話履歴の削除のみを行う。

    Args:
        message: コマンドを送信した Discord メッセージ。結果の返信に使用する。
        arg: コマンド名の後ろに続く引数文字列（このコマンドでは使用しない）。
        tools: ツールレジストリ（未使用）。
        bot: Discord クライアント。削除対象メッセージの取得に使用する。
    """
    from lilla_core.repository.conversation_repository import get_conversation_repo

    repo = get_conversation_repo()
    record = await repo.find_latest_by_tag("dirty")
    if record is None:
        await message.reply(t("command.cleardirty.not_found"))
        return

    deleted_discord = 0
    channel_id = record.get("discord_channel_id")
    if channel_id:
        channel = await resolve_discord_channel(channel_id, bot)
        if channel is None:
            logger.warning("!cleardirty: Cannot resolve channel: %s", channel_id)
        else:
            for message_id in record.get("discord_message_ids") or []:
                try:
                    target = await channel.fetch_message(int(message_id))
                    await target.delete()
                    deleted_discord += 1
                except Exception as e:
                    logger.warning("!cleardirty: Failed to delete Discord message: %s", e)

    await repo.delete(str(record["_id"]))
    await message.reply(t("command.cleardirty.deleted", discord_count=deleted_discord))
