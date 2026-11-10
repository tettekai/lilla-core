"""Discord のリアクション追加・削除イベントのディスパッチ。

キャッシュに無い古いメッセージや DM のリアクションも拾えるよう、raw 系のイベント
（`on_raw_reaction_add` / `on_raw_reaction_remove`）の `RawReactionActionEvent` を受け取る。
対象は保存済みの `discord_message_ids` に一致する Bot の発言だけで、保存は経路に依存しない
`services/reactions.py` の共通処理へ渡す。
"""
from __future__ import annotations

import logging

from lilla_core.core.config import get_config
from lilla_core.services import reactions

logger = logging.getLogger(__name__)


def _is_accepted_user(user_id: int, bot) -> bool:
    """リアクションを受け付けるユーザーかを返す。

    Bot 自身は除く。受け付けるのはメッセージの受付（通常会話）と同じくオーナー
    （`discord.my_user_id`）だけ。
    """
    bot_user = getattr(bot, "user", None)
    if bot_user is not None and user_id == bot_user.id:
        return False
    return str(user_id) == get_config().discord.my_user_id


async def handle_raw_reaction(payload, bot, added: bool) -> None:
    """リアクションの追加・削除イベントを会話履歴へ反映する。

    受付対象外のユーザー・変換できない絵文字・該当する会話履歴が無いメッセージは
    何もしない。保存の失敗は WARNING ログに留め、イベント処理の外へ例外を出さない。

    Args:
        payload: discord.py の `RawReactionActionEvent`。
        bot: Discord ボットインスタンス。
        added: 追加イベントなら True、削除イベントなら False。
    """
    if not _is_accepted_user(payload.user_id, bot):
        return
    emoji = reactions.emoji_from_discord(payload.emoji)
    if emoji is None:
        return
    try:
        from lilla_core.repository.conversation_repository import get_conversation_repo

        conversation_id = await get_conversation_repo().find_id_by_discord_message_id(
            payload.message_id
        )
        if conversation_id is None:
            return
        if added:
            await reactions.add_reaction(
                conversation_id, emoji, reactions.REACTION_SOURCE_DISCORD
            )
        else:
            await reactions.remove_reaction(
                conversation_id, emoji, reactions.REACTION_SOURCE_DISCORD
            )
    except reactions.ConversationNotFoundError:
        # 引いた直後に履歴が消えた場合。付け外しの対象が無いだけなので何もしない
        logger.debug(
            "Conversation for Discord message %s disappeared before applying reaction",
            payload.message_id,
        )
    except Exception as e:
        logger.warning(
            "Failed to apply Discord reaction (added=%s, message_id=%s): %s: %s",
            added, payload.message_id, type(e).__name__, e,
        )
