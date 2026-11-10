"""services/reactions.py のテスト。"""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

_OID = "65f0000000000000000000aa"


def _fresh_reactions_module(monkeypatch):
    """他のテストがモックの上で import した残りを避け、本物の依存で読み直したモジュールを返す。"""
    import importlib

    for name in (
        "lilla_core.services.reactions",
        "lilla_core.repository.conversation_repository",
    ):
        monkeypatch.delitem(sys.modules, name, raising=False)
    return importlib.import_module("lilla_core.services.reactions")


@pytest.fixture
def reactions_module(monkeypatch):
    """リポジトリを差し替えた services.reactions を返し、終わったらフックを外す。"""
    reactions = _fresh_reactions_module(monkeypatch)

    repo = MagicMock()
    repo.add_reaction = AsyncMock(return_value=True)
    repo.remove_reaction = AsyncMock(return_value=True)
    monkeypatch.setattr(reactions, "get_conversation_repo", lambda: repo)
    reactions.clear_reaction_hooks()
    reactions._test_repo = repo
    yield reactions
    reactions.clear_reaction_hooks()
    del reactions._test_repo


class TestAddReaction:
    async def test_adds_and_notifies_hook(self, reactions_module) -> None:
        """追加できたらリポジトリへ渡し、登録したフックへ変更を渡す。"""
        received = []
        reactions_module.register_reaction_hook(received.append)
        emoji = reactions_module.ReactionEmoji("❤️")

        assert await reactions_module.add_reaction(_OID, emoji, "client") is True

        repo = reactions_module._test_repo
        args = repo.add_reaction.await_args.args
        assert args[:3] == (_OID, emoji, "client")
        assert len(received) == 1
        change = received[0]
        assert change.action == "added"
        assert change.conversation_id == _OID
        assert change.emoji == emoji
        assert change.source == "client"
        # フックへ渡す時刻は保存した時刻と同じ
        assert change.time == args[3]
        assert change.time.tzinfo is not None
        assert change.time.microsecond % 1000 == 0

    async def test_duplicate_does_not_notify(self, reactions_module) -> None:
        """重複した追加（リポジトリが False）はフックを呼ばない。"""
        reactions_module._test_repo.add_reaction.return_value = False
        hook = MagicMock()
        reactions_module.register_reaction_hook(hook)

        result = await reactions_module.add_reaction(
            _OID, reactions_module.ReactionEmoji("👍"), "discord"
        )

        assert result is False
        hook.assert_not_called()

    @pytest.mark.parametrize("bad_id", ["", "not-an-id", None])
    async def test_invalid_conversation_id_raises(self, reactions_module, bad_id) -> None:
        """不正な会話 ID は ValueError（リポジトリは呼ばない）。"""
        with pytest.raises(ValueError):
            await reactions_module.add_reaction(
                bad_id, reactions_module.ReactionEmoji("👍"), "discord"
            )
        reactions_module._test_repo.add_reaction.assert_not_called()

    async def test_empty_emoji_or_source_raises(self, reactions_module) -> None:
        """空の絵文字・経路は ValueError。"""
        with pytest.raises(ValueError):
            await reactions_module.add_reaction(_OID, reactions_module.ReactionEmoji(""), "x")
        with pytest.raises(ValueError):
            await reactions_module.add_reaction(_OID, reactions_module.ReactionEmoji("👍"), "")


class TestRemoveReaction:
    async def test_removes_and_notifies_hook(self, reactions_module) -> None:
        """取り除けたらフックへ removed を渡す。"""
        received = []

        async def hook(change) -> None:
            received.append(change)

        reactions_module.register_reaction_hook(hook)
        emoji = reactions_module.ReactionEmoji("lilla_smile", 123)

        assert await reactions_module.remove_reaction(_OID, emoji, "discord") is True

        reactions_module._test_repo.remove_reaction.assert_awaited_once_with(_OID, emoji)
        assert [c.action for c in received] == ["removed"]
        assert received[0].emoji == emoji

    async def test_missing_does_not_notify(self, reactions_module) -> None:
        """付いていなければフックを呼ばない。"""
        reactions_module._test_repo.remove_reaction.return_value = False
        hook = MagicMock()
        reactions_module.register_reaction_hook(hook)

        assert await reactions_module.remove_reaction(
            _OID, reactions_module.ReactionEmoji("👍"), "discord"
        ) is False
        hook.assert_not_called()


class TestHooks:
    async def test_failing_hook_does_not_stop_others(self, reactions_module) -> None:
        """1 つのフックが例外を投げても後続のフックは呼ばれ、追加自体は成功する。"""
        def broken(change) -> None:
            raise RuntimeError("boom")

        received = []
        reactions_module.register_reaction_hook(broken)
        reactions_module.register_reaction_hook(received.append)

        result = await reactions_module.add_reaction(
            _OID, reactions_module.ReactionEmoji("👍"), "discord"
        )

        assert result is True
        assert len(received) == 1

    async def test_register_twice_and_unregister(self, reactions_module) -> None:
        """同じフックは 1 回だけ呼び、外したら呼ばない。"""
        hook = MagicMock()
        reactions_module.register_reaction_hook(hook)
        reactions_module.register_reaction_hook(hook)
        await reactions_module.add_reaction(_OID, reactions_module.ReactionEmoji("👍"), "x")
        assert hook.call_count == 1

        reactions_module.unregister_reaction_hook(hook)
        reactions_module.unregister_reaction_hook(hook)
        await reactions_module.add_reaction(_OID, reactions_module.ReactionEmoji("👍"), "x")
        assert hook.call_count == 1


class TestEmojiFromDiscord:
    def test_standard_emoji_is_character(self, reactions_module) -> None:
        """標準の絵文字は文字そのもので、ID を持たない。"""
        m = reactions_module
        assert m.emoji_from_discord(SimpleNamespace(name="❤️", id=None)) == m.ReactionEmoji("❤️")

    def test_custom_emoji_has_name_and_id(self, reactions_module) -> None:
        """カスタム絵文字は名前と ID。"""
        m = reactions_module
        assert m.emoji_from_discord(
            SimpleNamespace(name="lilla_smile", id=123)
        ) == m.ReactionEmoji("lilla_smile", 123)

    def test_real_partial_emoji(self, reactions_module) -> None:
        """discord.py の PartialEmoji からも変換できる。"""
        import discord

        m = reactions_module
        assert m.emoji_from_discord(discord.PartialEmoji(name="👍")) == m.ReactionEmoji("👍")
        assert m.emoji_from_discord(
            discord.PartialEmoji(name="party", id=987654321098765432)
        ) == m.ReactionEmoji("party", 987654321098765432)

    def test_nameless_emoji_returns_none(self, reactions_module) -> None:
        """名前の取れない絵文字は None。"""
        m = reactions_module
        assert m.emoji_from_discord(SimpleNamespace(name=None, id=5)) is None
        assert m.emoji_from_discord(SimpleNamespace(name="", id=None)) is None
