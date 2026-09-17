"""handlers/message_handler.py のテスト。

通常会話を始める条件（メンション / DM / `discord.channels` の
`mention_optional: true`）と、ユーザー発言の会話履歴保存に
`discord_channel_id` が付くことを検証する。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import discord

from lilla_core.core.config import DiscordChannelConfig, DiscordConfig
from lilla_core.handlers import message_handler


def _make_bot(mentioned: bool) -> MagicMock:
    """メンション有無だけを制御できる bot モックを返す。"""
    bot = MagicMock()
    bot.user = MagicMock()
    bot.user.mentioned_in.return_value = mentioned
    return bot


def _make_message(channel_id: int, *, author_id: str = "1", is_dm: bool = False) -> MagicMock:
    """通常会話の分岐まで到達するメッセージモックを返す。"""
    message = MagicMock()
    message.content = "こんにちは"
    message.attachments = []
    message.author = MagicMock()
    message.author.id = author_id
    message.channel = MagicMock(spec=discord.DMChannel) if is_dm else MagicMock()
    message.channel.id = channel_id
    return message


def _discord_config(channels: list[DiscordChannelConfig]) -> DiscordConfig:
    """テスト用の `discord:` セクションを組み立てる。"""
    return DiscordConfig(my_user_id="1", channels=channels)


@pytest.fixture
def created_tasks(monkeypatch: pytest.MonkeyPatch) -> list:
    """`asyncio.create_task` を捕捉し、会話処理へ進んだかどうかを観測できるようにする。

    実際にタスクを走らせると LLM や MongoDB へ出てしまうため、コルーチンは
    閉じるだけにする。
    """
    captured: list = []

    def fake_create_task(coro):
        captured.append(coro)
        coro.close()
        return MagicMock()

    monkeypatch.setattr(message_handler.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(message_handler, "_discord_active_tasks", {})
    return captured


@pytest.fixture(autouse=True)
def stub_command_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    """コマンド判定を「コマンドではない」に固定する。"""
    monkeypatch.setattr(
        message_handler.command_handler, "extract_command_content", lambda content: None
    )


async def _handle(message: MagicMock, bot: MagicMock) -> None:
    """メッセージフックを使わない形で `handle_message` を呼ぶ。"""
    await message_handler.handle_message(
        message, bot, tools={}, llm_tools={}, message_hook=AsyncMock(return_value=False)
    )


class TestConversationTrigger:
    """通常会話を始める条件を検証する。"""

    @pytest.fixture(autouse=True)
    def mock_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """登録チャンネル 2 件（mention_optional true / false）を設定する。"""
        cfg = MagicMock()
        cfg.discord = _discord_config([
            DiscordChannelConfig(name="dev", channel_id="100", mention_optional=True),
            DiscordChannelConfig(name="lounge", channel_id="200"),
        ])
        monkeypatch.setattr(message_handler, "_config", cfg)

    async def test_mention_optional_channel_starts_conversation_without_mention(
        self, created_tasks: list
    ) -> None:
        await _handle(_make_message(100), _make_bot(mentioned=False))
        assert len(created_tasks) == 1

    async def test_registered_channel_without_mention_optional_is_ignored(
        self, created_tasks: list
    ) -> None:
        await _handle(_make_message(200), _make_bot(mentioned=False))
        assert created_tasks == []

    async def test_unregistered_channel_without_mention_is_ignored(
        self, created_tasks: list
    ) -> None:
        await _handle(_make_message(999), _make_bot(mentioned=False))
        assert created_tasks == []

    async def test_mention_still_starts_conversation_in_unregistered_channel(
        self, created_tasks: list
    ) -> None:
        await _handle(_make_message(999), _make_bot(mentioned=True))
        assert len(created_tasks) == 1

    async def test_dm_still_starts_conversation_without_mention(
        self, created_tasks: list
    ) -> None:
        await _handle(_make_message(999, is_dm=True), _make_bot(mentioned=False))
        assert len(created_tasks) == 1


class TestNoRegisteredChannels:
    """`channels` 未設定なら受信動作が現行のままであることを検証する。"""

    @pytest.fixture(autouse=True)
    def mock_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = MagicMock()
        cfg.discord = _discord_config([])
        monkeypatch.setattr(message_handler, "_config", cfg)

    async def test_without_mention_is_ignored(self, created_tasks: list) -> None:
        await _handle(_make_message(100), _make_bot(mentioned=False))
        assert created_tasks == []

    async def test_with_mention_starts_conversation(self, created_tasks: list) -> None:
        await _handle(_make_message(100), _make_bot(mentioned=True))
        assert len(created_tasks) == 1


class TestUserMessagePersistence:
    """ユーザー発言の会話履歴保存に `discord_channel_id` が付くことを検証する。"""

    @pytest.fixture(autouse=True)
    def mock_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = MagicMock()
        cfg.discord = _discord_config([
            DiscordChannelConfig(name="dev", channel_id="100", mention_optional=True),
        ])
        monkeypatch.setattr(message_handler, "_config", cfg)

    @pytest.fixture
    def mock_memory_manager(self, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
        manager = MagicMock()
        manager.add_conversation = AsyncMock()
        monkeypatch.setattr(message_handler, "_memory_manager", manager)
        return manager

    async def test_user_message_is_saved_with_channel_id(
        self, monkeypatch: pytest.MonkeyPatch, mock_memory_manager: MagicMock
    ) -> None:
        """メンションなしの登録チャンネルでも、ユーザー発言にチャンネル ID が付く。"""
        monkeypatch.setattr(message_handler, "_discord_active_tasks", {})
        monkeypatch.setattr(
            message_handler, "run_conversation", AsyncMock(return_value="返事")
        )
        monkeypatch.setattr(message_handler, "split_response", lambda reply: [reply])

        message = _make_message(100)
        message.reply = AsyncMock(return_value=MagicMock(id=555))
        await _handle(message, _make_bot(mentioned=False))
        # `asyncio.create_task` が積んだ会話タスクの完了を待つ
        await message_handler._discord_active_tasks[100]

        user_call = mock_memory_manager.add_conversation.await_args_list[0]
        assert user_call.args[0]["role"] == "user"
        assert user_call.kwargs["discord_channel_id"] == 100
