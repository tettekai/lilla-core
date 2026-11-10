"""会話履歴のメッセージに付いたリアクションの追加・削除（経路に依存しない共通処理）。

Discord のリアクションも、拡張が持つ独自クライアント（WebSocket など）のスタンプも、
同じ `add_reaction` / `remove_reaction` を通して `conversations` の `reactions` に
保存する。変更があったときは `register_reaction_hook` で登録したフックを呼ぶので、
利用側はそこで接続中のクライアントへ通知できる（通知の送り方は利用側の実装に任せる）。

サーバーはどの絵文字も特別扱いしない。異体字の扱いや絵文字ごとの意味付けは
利用側で決める。
"""
from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Literal

from bson import ObjectId

from lilla_core.core.exceptions import ConversationNotFoundError
from lilla_core.repository.conversation_repository import ReactionEmoji, get_conversation_repo
from lilla_core.utils.datetime_utils import utc_now

logger = logging.getLogger(__name__)

__all__ = [
    "REACTION_SOURCE_DISCORD",
    "ConversationNotFoundError",
    "ReactionChange",
    "ReactionEmoji",
    "ReactionHook",
    "add_reaction",
    "clear_reaction_hooks",
    "emoji_from_discord",
    "register_reaction_hook",
    "remove_reaction",
    "unregister_reaction_hook",
]

#: Discord で付いたリアクションの経路名。
REACTION_SOURCE_DISCORD = "discord"


@dataclass(frozen=True)
class ReactionChange:
    """リアクションの変更 1 件（フックへ渡す）。

    Attributes:
        action: `"added"`（追加）か `"removed"`（削除）。
        conversation_id: 対象の会話履歴の `_id`（`list_for_client` の `_id` と同じ値）。
        emoji: 変更された絵文字。
        source: 変更した経路（`"discord"` / `"client"` など）。
        time: 変更した時刻（UTC）。追加なら保存した `reacted_at` と同じ値。
        reactions: 変更後のそのメッセージの `reactions` 全体（`list_for_client` と同じ
            要素の形）。更新と同じ操作で原子的に取得した値で、最後の 1 つを取り除いた
            ときは空リスト。差分を積み上げずにこの一覧をそのまま配れば、複数の経路から
            同時に付け外しされても食い違わない。
    """

    action: Literal["added", "removed"]
    conversation_id: str
    emoji: ReactionEmoji
    source: str
    time: datetime
    reactions: list[dict]


#: フックの型。同期関数でも非同期関数でもよい。
ReactionHook = Callable[[ReactionChange], "Awaitable[Any] | Any"]

_hooks: list[ReactionHook] = []


def register_reaction_hook(hook: ReactionHook) -> None:
    """リアクションの変更を受け取るフックを登録する。

    実際に追加・削除されたとき（重複した追加や、付いていない絵文字の削除では呼ばない）に
    登録順に呼ぶ。同じフックを 2 回登録しても 1 回だけ呼ぶ。拡張は `setup()` などで
    登録すればよい。

    Args:
        hook: `ReactionChange` を 1 つ受け取る関数（同期・非同期どちらでもよい）。
    """
    if hook not in _hooks:
        _hooks.append(hook)


def unregister_reaction_hook(hook: ReactionHook) -> None:
    """`register_reaction_hook` で登録したフックを外す（登録されていなければ何もしない）。"""
    if hook in _hooks:
        _hooks.remove(hook)


def clear_reaction_hooks() -> None:
    """登録済みのフックをすべて外す（主にテスト用）。"""
    _hooks.clear()


async def _notify(change: ReactionChange) -> None:
    """登録済みのフックへ変更を渡す。1 つのフックの失敗は WARNING ログだけで、後続も呼ぶ。"""
    for hook in list(_hooks):
        try:
            result = hook(change)
            if inspect.isawaitable(result):
                await result
        except Exception as e:
            logger.warning(
                "Reaction hook %r failed: %s: %s", hook, type(e).__name__, e
            )


async def add_reaction(conversation_id: str, emoji: ReactionEmoji, source: str) -> bool:
    """会話履歴のメッセージにリアクションを足す。

    同じメッセージに同じ絵文字は 1 つまでで、既に付いていれば何もしない（フックも
    呼ばない）。`message.content` には手を加えない。

    Args:
        conversation_id: 対象の会話履歴の `_id`（文字列）。
        emoji: 付ける絵文字。
        source: 付けた経路（`"discord"` / `"client"` など）。

    Returns:
        追加した場合は True。既に付いていた場合は False。

    Raises:
        ValueError: 絵文字の名前が空のとき、または `conversation_id` が不正な形式のとき。
        ConversationNotFoundError: 対象の会話履歴が存在しないとき。
    """
    _validate(conversation_id, emoji, source)
    now = _storage_now()
    reactions = await get_conversation_repo().add_reaction(conversation_id, emoji, source, now)
    if reactions is None:
        return False
    await _notify(ReactionChange("added", conversation_id, emoji, source, now, reactions))
    return True


async def remove_reaction(conversation_id: str, emoji: ReactionEmoji, source: str) -> bool:
    """会話履歴のメッセージからリアクションを取り除く。

    付いていなければ何もしない（フックも呼ばない）。どの経路で付いたものでも
    同じ絵文字なら取り除く。

    Args:
        conversation_id: 対象の会話履歴の `_id`（文字列）。
        emoji: 取り除く絵文字。
        source: 取り除いた経路（フックへ渡す）。

    Returns:
        取り除いた場合は True。付いていなかった場合は False。

    Raises:
        ValueError: 絵文字の名前が空のとき、または `conversation_id` が不正な形式のとき。
        ConversationNotFoundError: 対象の会話履歴が存在しないとき。
    """
    _validate(conversation_id, emoji, source)
    reactions = await get_conversation_repo().remove_reaction(conversation_id, emoji)
    if reactions is None:
        return False
    await _notify(
        ReactionChange("removed", conversation_id, emoji, source, _storage_now(), reactions)
    )
    return True


def emoji_from_discord(partial_emoji: Any) -> ReactionEmoji | None:
    """discord.py の `PartialEmoji` を `ReactionEmoji` へ変換する。

    標準の絵文字は文字そのもの、カスタム絵文字は名前と ID にする。名前の取れない
    もの（削除済みのカスタム絵文字など）は None を返す。

    Args:
        partial_emoji: `RawReactionActionEvent.emoji`。

    Returns:
        変換した絵文字。変換できなければ None。
    """
    name = getattr(partial_emoji, "name", None)
    if not isinstance(name, str) or not name:
        return None
    emoji_id = getattr(partial_emoji, "id", None)
    return ReactionEmoji(name=name, id=int(emoji_id) if emoji_id else None)


def _validate(conversation_id: str, emoji: ReactionEmoji, source: str) -> None:
    """会話履歴の `_id`・絵文字・経路の形を確かめる。"""
    if not isinstance(conversation_id, str) or not ObjectId.is_valid(conversation_id):
        raise ValueError("conversation_id is not a valid ObjectId")
    if not isinstance(emoji, ReactionEmoji) or not isinstance(emoji.name, str) or not emoji.name:
        raise ValueError("emoji must be a ReactionEmoji with a non-empty name")
    if not isinstance(source, str) or not source:
        raise ValueError("source must be a non-empty string")


def _storage_now() -> datetime:
    """保存する時刻（UTC・ミリ秒精度）を返す（読み出した値と `==` で一致させるため）。"""
    now = utc_now()
    return now.replace(microsecond=now.microsecond // 1000 * 1000)
