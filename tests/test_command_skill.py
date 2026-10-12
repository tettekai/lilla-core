"""commands/skill.py のテスト。"""
from __future__ import annotations

import importlib
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

skill_command = importlib.import_module("lilla_core.commands.skill")


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """スキルリポジトリ・会話の開始・LLM ツールを差し替える。"""
    repo = MagicMock()
    repo.find_by_name = AsyncMock(return_value=None)

    repo_module = MagicMock()
    repo_module.get_skill_repo = MagicMock(return_value=repo)
    monkeypatch.setitem(sys.modules, "lilla_core.repository.skill_repository", repo_module)

    handler_module = MagicMock()
    handler_module.start_conversation = MagicMock()
    monkeypatch.setitem(sys.modules, "lilla_core.handlers.message_handler", handler_module)

    loader_module = MagicMock()
    loader_module.get_llm_tools = MagicMock(return_value={"tool": {}})
    monkeypatch.setitem(sys.modules, "lilla_core.loaders.llm_tool_loader", loader_module)

    repo.start_conversation = handler_module.start_conversation
    return repo


def _make_message() -> MagicMock:
    msg = MagicMock()
    msg.reply = AsyncMock()
    return msg


class TestHandleSkill:
    async def test_expands_skill_and_starts_conversation(self, repo: MagicMock) -> None:
        """有効なスキルは展開して通常の会話の流れへ渡す。"""
        repo.find_by_name.return_value = {"name": "review", "prompt": "レビューして", "enabled": True}
        msg = _make_message()
        bot = MagicMock()

        await skill_command.handle_skill(msg, "review  この PR を見て", {}, bot)

        repo.find_by_name.assert_awaited_once_with("review")
        repo.start_conversation.assert_called_once_with(
            msg,
            bot,
            {"tool": {}},
            "---SKILL: review---\nレビューして\n---END_SKILL---\n\nこの PR を見て",
        )
        msg.reply.assert_not_awaited()

    async def test_name_is_case_insensitive(self, repo: MagicMock) -> None:
        """大文字小文字の違う名前でも小文字に正規化して照合する。"""
        repo.find_by_name.return_value = {"name": "review", "prompt": "p", "enabled": True}

        await skill_command.handle_skill(_make_message(), "Review", {}, MagicMock())

        repo.find_by_name.assert_awaited_once_with("review")
        content = repo.start_conversation.call_args.args[3]
        assert content == "---SKILL: review---\np\n---END_SKILL---"

    async def test_not_found_replies_without_llm(self, repo: MagicMock) -> None:
        """存在しないスキルは元チャンネルへエラーを返し、会話を始めない。"""
        msg = _make_message()

        await skill_command.handle_skill(msg, "missing", {}, MagicMock())

        msg.reply.assert_awaited_once_with("スキル 'missing' は見つかりません。")
        repo.start_conversation.assert_not_called()

    async def test_disabled_replies_without_llm(self, repo: MagicMock) -> None:
        """無効なスキルは元チャンネルへエラーを返し、会話を始めない。"""
        repo.find_by_name.return_value = {"name": "review", "prompt": "p", "enabled": False}
        msg = _make_message()

        await skill_command.handle_skill(msg, "review", {}, MagicMock())

        msg.reply.assert_awaited_once_with("スキル 'review' は無効になっています。")
        repo.start_conversation.assert_not_called()

    async def test_invalid_name_is_not_found_without_lookup(self, repo: MagicMock) -> None:
        """規則に合わない名前は DB を引かずに見つからない扱いにする。"""
        msg = _make_message()

        await skill_command.handle_skill(msg, "日本語", {}, MagicMock())

        repo.find_by_name.assert_not_awaited()
        msg.reply.assert_awaited_once_with("スキル '日本語' は見つかりません。")
        repo.start_conversation.assert_not_called()

    @pytest.mark.parametrize("arg", ["", "   "])
    async def test_missing_name_replies_usage(self, repo: MagicMock, arg: str) -> None:
        """スキル名が無ければ使い方を返す。"""
        msg = _make_message()

        await skill_command.handle_skill(msg, arg, {}, MagicMock())

        assert "!skill" in msg.reply.await_args.args[0]
        repo.start_conversation.assert_not_called()

    async def test_registered_as_command(self) -> None:
        """`skill` がコマンドレジストリに登録されている。"""
        from lilla_core.commands.registry import get_command_handler

        assert get_command_handler("skill") is skill_command.handle_skill
