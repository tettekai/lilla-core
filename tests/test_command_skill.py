"""commands/skill.py のテスト。"""
from __future__ import annotations

import importlib
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from lilla_core.services.skills import format_skill_instruction

skill_command = importlib.import_module("lilla_core.commands.skill")
command_handler = importlib.import_module("lilla_core.handlers.command_handler")


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """スキルリポジトリ・会話の開始・LLM ツールを差し替える。"""
    repo = MagicMock()
    repo.find_by_name = AsyncMock(return_value=None)
    repo.get_enabled = AsyncMock(return_value=[])

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
    msg.channel.send = AsyncMock()
    return msg


def _ctx(msg: MagicMock, bot: MagicMock | None = None):
    """Discord 用のコマンドコンテキストで包む（会話の開始は `bot` を引き継ぐ）。"""
    return command_handler.DiscordCommandContext(msg, bot if bot is not None else MagicMock())


class TestHandleSkill:
    async def test_expands_skill_and_starts_conversation(self, repo: MagicMock) -> None:
        """有効なスキルは展開して通常の会話の流れへ渡す。"""
        repo.find_by_name.return_value = {"name": "review", "prompt": "レビューして", "enabled": True}
        msg = _make_message()
        bot = MagicMock()

        await skill_command.handle_skill(_ctx(msg, bot), "review  この PR を見て", {}, bot)

        repo.find_by_name.assert_awaited_once_with("review")
        repo.start_conversation.assert_called_once_with(
            msg,
            bot,
            {"tool": {}},
            format_skill_instruction("review", "レビューして", "この PR を見て"),
        )
        msg.reply.assert_not_awaited()
        content = repo.start_conversation.call_args.args[3]
        assert content.startswith("[システム指示: スキル「review」]\n")
        assert "\nレビューして\n[/システム指示]\n\n追加指示: この PR を見て" in content

    async def test_name_is_case_insensitive(self, repo: MagicMock) -> None:
        """大文字小文字の違う名前でも小文字に正規化して照合する。"""
        repo.find_by_name.return_value = {"name": "review", "prompt": "p", "enabled": True}

        await skill_command.handle_skill(_ctx(_make_message()), "Review", {}, MagicMock())

        repo.find_by_name.assert_awaited_once_with("review")
        content = repo.start_conversation.call_args.args[3]
        assert content == format_skill_instruction("review", "p")
        assert "追加指示" not in content

    async def test_not_found_replies_without_llm(self, repo: MagicMock) -> None:
        """存在しないスキルは元チャンネルへエラーを返し、会話を始めない。"""
        msg = _make_message()

        await skill_command.handle_skill(_ctx(msg), "missing", {}, MagicMock())

        msg.reply.assert_awaited_once_with("スキル 'missing' は見つかりません。")
        repo.start_conversation.assert_not_called()

    async def test_disabled_replies_without_llm(self, repo: MagicMock) -> None:
        """無効なスキルは元チャンネルへエラーを返し、会話を始めない。"""
        repo.find_by_name.return_value = {"name": "review", "prompt": "p", "enabled": False}
        msg = _make_message()

        await skill_command.handle_skill(_ctx(msg), "review", {}, MagicMock())

        msg.reply.assert_awaited_once_with("スキル 'review' は無効になっています。")
        repo.start_conversation.assert_not_called()

    async def test_invalid_name_is_not_found_without_lookup(self, repo: MagicMock) -> None:
        """規則に合わない名前は DB を引かずに見つからない扱いにする。"""
        msg = _make_message()

        await skill_command.handle_skill(_ctx(msg), "日本語", {}, MagicMock())

        repo.find_by_name.assert_not_awaited()
        msg.reply.assert_awaited_once_with("スキル '日本語' は見つかりません。")
        repo.start_conversation.assert_not_called()

    async def test_registered_as_command(self) -> None:
        """`skill` がコマンドレジストリに登録されている。"""
        from lilla_core.commands.registry import get_command_handler

        assert get_command_handler("skill") is skill_command.handle_skill


class TestSkillList:
    @pytest.mark.parametrize("arg", ["", "   "])
    async def test_lists_enabled_skills_without_llm(self, repo: MagicMock, arg: str) -> None:
        """引数なしなら有効なスキルの一覧をリポジトリの順で返し、会話を始めない。"""
        repo.get_enabled.return_value = [
            {"name": "alpha", "description": "最初のスキル"},
            {"name": "beta", "description": ""},
            {"name": "gamma"},
        ]
        msg = _make_message()

        await skill_command.handle_skill(_ctx(msg), arg, {}, MagicMock())

        repo.get_enabled.assert_awaited_once_with()
        repo.find_by_name.assert_not_awaited()
        repo.start_conversation.assert_not_called()
        msg.reply.assert_awaited_once_with(
            "利用できるスキル:\n"
            "- `alpha`: 最初のスキル\n"
            "- `beta`\n"
            "- `gamma`\n"
            "呼び出し方: `!skill <スキル名> [追加指示]`"
        )
        msg.channel.send.assert_not_awaited()

    async def test_description_newlines_become_spaces(self, repo: MagicMock) -> None:
        """説明内の改行は空白に置き換えて 1 行にする。"""
        repo.get_enabled.return_value = [
            {"name": "review", "description": "1 行目\n2 行目\r\n3 行目"},
        ]
        msg = _make_message()

        await skill_command.handle_skill(_ctx(msg), "", {}, MagicMock())

        assert "- `review`: 1 行目 2 行目 3 行目\n" in msg.reply.await_args.args[0]

    async def test_empty_list_replies_list_empty(self, repo: MagicMock) -> None:
        """有効なスキルが 0 件なら list_empty の文言を返す。"""
        msg = _make_message()

        await skill_command.handle_skill(_ctx(msg), "", {}, MagicMock())

        msg.reply.assert_awaited_once_with(
            "利用できるスキルはありません。ダッシュボードのスキルページから登録できます。"
        )
        repo.start_conversation.assert_not_called()

    async def test_long_list_is_split_on_line_boundaries(self, repo: MagicMock) -> None:
        """2000 文字を超える一覧は行の境界で複数メッセージに分けて同じチャンネルへ送る。"""
        repo.get_enabled.return_value = [
            {"name": f"skill-{i:03d}", "description": "説" * 80} for i in range(40)
        ]
        msg = _make_message()

        await skill_command.handle_skill(_ctx(msg), "", {}, MagicMock())

        sent = [msg.reply.await_args.args[0]] + [
            call.args[0] for call in msg.channel.send.await_args_list
        ]
        assert len(sent) > 1
        assert all(len(chunk) <= 2000 for chunk in sent)
        expected = (
            ["利用できるスキル:"]
            + [f"- `skill-{i:03d}`: {'説' * 80}" for i in range(40)]
            + ["呼び出し方: `!skill <スキル名> [追加指示]`"]
        )
        # 各メッセージを行に戻して並べると、元の一覧と行単位で一致する（行の途中で切れていない）
        assert [line for chunk in sent for line in chunk.split("\n")] == expected
