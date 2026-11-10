"""handlers/reaction_handler.py のテスト。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

_OWNER_ID = 1111
_BOT_ID = 2222
_OTHER_ID = 3333
_OID = "65f0000000000000000000aa"


@pytest.fixture
def env(monkeypatch):
    """設定・リポジトリ・共通処理を差し替えた reaction_handler を返す。

    他のテストがモックの上で import した残りを避けるため、本物の依存で読み直す。
    """
    import importlib
    import sys

    for name in (
        "lilla_core.handlers.reaction_handler",
        "lilla_core.services.reactions",
        "lilla_core.repository.conversation_repository",
    ):
        monkeypatch.delitem(sys.modules, name, raising=False)
    conversation_repository = importlib.import_module(
        "lilla_core.repository.conversation_repository"
    )
    reactions = importlib.import_module("lilla_core.services.reactions")
    reaction_handler = importlib.import_module("lilla_core.handlers.reaction_handler")

    cfg = MagicMock()
    cfg.discord.my_user_id = str(_OWNER_ID)
    monkeypatch.setattr(reaction_handler, "get_config", lambda: cfg)

    repo = MagicMock()
    repo.find_id_by_discord_message_id = AsyncMock(return_value=_OID)
    monkeypatch.setattr(conversation_repository, "get_conversation_repo", lambda: repo)

    add = AsyncMock(return_value=True)
    remove = AsyncMock(return_value=True)
    monkeypatch.setattr(reactions, "add_reaction", add)
    monkeypatch.setattr(reactions, "remove_reaction", remove)

    bot = SimpleNamespace(user=SimpleNamespace(id=_BOT_ID))
    return SimpleNamespace(
        handler=reaction_handler, repo=repo, add=add, remove=remove, bot=bot,
        ReactionEmoji=reactions.ReactionEmoji,
    )


def _payload(user_id: int = _OWNER_ID, name: str = "❤️", emoji_id=None, guild_id=None):
    """RawReactionActionEvent の代わり（guild_id=None は DM）。"""
    return SimpleNamespace(
        user_id=user_id,
        message_id=42,
        channel_id=7,
        guild_id=guild_id,
        emoji=SimpleNamespace(name=name, id=emoji_id),
    )


class TestHandleRawReaction:
    async def test_add_from_owner_in_dm(self, env) -> None:
        """オーナーが DM（キャッシュ外を含む）で付けたリアクションを保存する。"""
        await env.handler.handle_raw_reaction(_payload(), env.bot, added=True)

        env.repo.find_id_by_discord_message_id.assert_awaited_once_with(42)
        env.add.assert_awaited_once_with(_OID, env.ReactionEmoji("❤️"), "discord")
        env.remove.assert_not_called()

    async def test_remove_custom_emoji(self, env) -> None:
        """削除イベントはカスタム絵文字の名前と ID で取り除く。"""
        payload = _payload(name="lilla_smile", emoji_id=123, guild_id=99)
        await env.handler.handle_raw_reaction(payload, env.bot, added=False)

        env.remove.assert_awaited_once_with(
            _OID, env.ReactionEmoji("lilla_smile", 123), "discord"
        )
        env.add.assert_not_called()

    async def test_ignores_bot_itself(self, env) -> None:
        """Bot 自身のリアクションは無視する（オーナーと同じ ID でも）。"""
        env.bot.user.id = _OWNER_ID
        await env.handler.handle_raw_reaction(_payload(), env.bot, added=True)
        env.repo.find_id_by_discord_message_id.assert_not_called()
        env.add.assert_not_called()

    async def test_ignores_other_users(self, env) -> None:
        """メッセージの受付対象外（オーナー以外）のユーザーは無視する。"""
        await env.handler.handle_raw_reaction(_payload(user_id=_OTHER_ID), env.bot, added=True)
        env.repo.find_id_by_discord_message_id.assert_not_called()
        env.add.assert_not_called()

    async def test_no_matching_history(self, env) -> None:
        """該当する会話履歴が無ければ何もしない。"""
        env.repo.find_id_by_discord_message_id.return_value = None
        await env.handler.handle_raw_reaction(_payload(), env.bot, added=True)
        env.add.assert_not_called()

    async def test_nameless_emoji_is_ignored(self, env) -> None:
        """名前の取れない絵文字は無視する。"""
        await env.handler.handle_raw_reaction(_payload(name=None, emoji_id=5), env.bot, added=True)
        env.repo.find_id_by_discord_message_id.assert_not_called()

    async def test_failure_is_swallowed(self, env, caplog) -> None:
        """保存の失敗はイベント処理の外へ出さず WARNING ログに留める。"""
        env.add.side_effect = RuntimeError("mongo down")
        with caplog.at_level("WARNING"):
            await env.handler.handle_raw_reaction(_payload(), env.bot, added=True)
        assert "Failed to apply Discord reaction" in caplog.text
