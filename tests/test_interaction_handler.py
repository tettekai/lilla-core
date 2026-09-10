"""interaction_handler.py の `command:` プレフィックス汎用処理のテスト。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from lilla_core.handlers import command_handler, interaction_handler


def _make_interaction(custom_id: str, user_id: str = "12345") -> MagicMock:
    interaction = MagicMock()
    interaction.type = discord.InteractionType.component
    interaction.data = {"custom_id": custom_id}
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    interaction.message = MagicMock()
    interaction.user = MagicMock()
    interaction.user.id = user_id
    return interaction


class TestHandleInteractionOwnerCheck:
    """`discord.my_user_id` によるオーナー以外の押下拒否の検証。"""

    async def test_rejects_non_owner_with_ephemeral_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """オーナー以外が押した場合、処理を実行せず ephemeral メッセージのみ返す。"""
        monkeypatch.setattr(interaction_handler._config.discord, "my_user_id", "99999")
        mock_handle_command = AsyncMock()
        monkeypatch.setattr(command_handler, "handle_command", mock_handle_command)
        interaction = _make_interaction("command:!llm_guard_cancel", user_id="12345")

        await interaction_handler.handle_interaction(interaction, bot=MagicMock(), tools={}, llm_tools={})

        mock_handle_command.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once()
        assert interaction.response.send_message.await_args.kwargs.get("ephemeral") is True

    async def test_allows_owner(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """オーナー本人が押した場合は従来通り処理される。"""
        monkeypatch.setattr(interaction_handler._config.discord, "my_user_id", "12345")
        mock_handle_command = AsyncMock()
        monkeypatch.setattr(command_handler, "handle_command", mock_handle_command)
        interaction = _make_interaction("command:!llm_guard_cancel", user_id="12345")

        await interaction_handler.handle_interaction(interaction, bot=MagicMock(), tools={}, llm_tools={})

        mock_handle_command.assert_awaited_once()
        interaction.response.send_message.assert_not_awaited()


class TestHandleInteractionCommandPrefix:
    """`command:` プレフィックスの汎用委譲の検証。"""

    async def test_defers_before_dispatching(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """先に defer() で応答してから委譲する（考え中表示は出さない）。"""
        monkeypatch.setattr(interaction_handler._config.discord, "my_user_id", "12345")
        mock_handle_command = AsyncMock()
        monkeypatch.setattr(command_handler, "handle_command", mock_handle_command)
        interaction = _make_interaction("command:!llm_guard_cancel")

        await interaction_handler.handle_interaction(interaction, bot=MagicMock(), tools={}, llm_tools={})

        interaction.response.defer.assert_awaited_once_with()
        mock_handle_command.assert_awaited_once()

    async def test_passes_content_after_prefix_and_original_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """custom_id の `command:` より後ろをそのままコマンド文字列として渡す。

        特定のコマンド名（llm_guard_cancel 等）には依存しない汎用処理であることを
        確認するため、任意のコマンド文字列で検証する。
        """
        monkeypatch.setattr(interaction_handler._config.discord, "my_user_id", "12345")
        mock_handle_command = AsyncMock()
        monkeypatch.setattr(command_handler, "handle_command", mock_handle_command)
        interaction = _make_interaction("command:!some_other_command arg1 arg2")
        tools = {"x": 1}
        bot = MagicMock()

        await interaction_handler.handle_interaction(interaction, bot=bot, tools=tools, llm_tools={})

        mock_handle_command.assert_awaited_once_with(
            interaction.message, "!some_other_command arg1 arg2", tools, bot
        )

    async def test_does_not_send_followup_on_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """成功時は followup を送らない（表示更新はコマンド側に任せ、二重表示を避ける）。"""
        monkeypatch.setattr(interaction_handler._config.discord, "my_user_id", "12345")
        monkeypatch.setattr(command_handler, "handle_command", AsyncMock())
        interaction = _make_interaction("command:!llm_guard_cancel")

        await interaction_handler.handle_interaction(interaction, bot=MagicMock(), tools={}, llm_tools={})

        interaction.followup.send.assert_not_awaited()

    async def test_dispatch_exception_sends_error_followup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """委譲先で例外が起きても followup でエラーを通知し、例外は伝播させない。"""
        monkeypatch.setattr(interaction_handler._config.discord, "my_user_id", "12345")
        monkeypatch.setattr(
            command_handler, "handle_command", AsyncMock(side_effect=RuntimeError("boom"))
        )
        interaction = _make_interaction("command:!llm_guard_cancel")

        await interaction_handler.handle_interaction(interaction, bot=MagicMock(), tools={}, llm_tools={})

        interaction.followup.send.assert_awaited_once()
        assert "エラー" in interaction.followup.send.await_args[0][0]

    async def test_does_not_match_action_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`action:` プレフィックスには従来通りの処理が使われ、command 側には来ない。"""
        monkeypatch.setattr(interaction_handler._config.discord, "my_user_id", "12345")
        mock_handle_command = AsyncMock()
        monkeypatch.setattr(command_handler, "handle_command", mock_handle_command)
        interaction = _make_interaction("action:some-uuid")

        get_repo_mock = MagicMock()
        get_repo_mock.find_one_and_delete = AsyncMock(return_value=None)
        monkeypatch.setattr(
            interaction_handler, "get_button_actions_repo", MagicMock(return_value=get_repo_mock)
        )
        interaction.response.send_message = AsyncMock()

        await interaction_handler.handle_interaction(interaction, bot=MagicMock(), tools={}, llm_tools={})

        mock_handle_command.assert_not_awaited()
