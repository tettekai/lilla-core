"""commands/runtask.py のテスト。"""
from __future__ import annotations

import logging
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lilla_core.commands import runtask


def _make_message() -> MagicMock:
    msg = MagicMock()
    msg.reply = AsyncMock()
    return msg


_FAKE_LLM_TOOLS = {"llm_dummy": {"schema": {}, "execute": None}}


@pytest.fixture(autouse=True)
def stub_llm_tools() -> dict:
    """run_task が関数内で import する get_llm_tools を差し替え、実ロードを避ける。"""
    mock_loader = MagicMock()
    mock_loader.get_llm_tools = lambda: _FAKE_LLM_TOOLS
    with patch.dict(sys.modules, {"lilla_core.loaders.llm_tool_loader": mock_loader}):
        yield _FAKE_LLM_TOOLS


@pytest.fixture(autouse=True)
def mock_notify_error(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """エラー通知（ERROR ログ + error_channel_id）をモックに差し替える。"""
    mock = AsyncMock()
    monkeypatch.setattr(runtask, "notify_error", mock)
    return mock


def _notified_text(mock_notify_error: AsyncMock) -> str:
    """notify_error に渡された「コンテキスト: エラー内容」を連結して返す。"""
    context, error = mock_notify_error.call_args[0][1:3]
    return f"{context}: {error}"


def _make_tools() -> dict:
    task_tool = MagicMock()
    task_tool.execute = AsyncMock(return_value="実行結果")
    return {
        "task_tool": {"instance": task_tool, "trigger": "task"},
        "other_tool": {"instance": MagicMock(), "trigger": "message"},
    }


class TestHandleRuntask:
    async def test_missing_tool_name_notifies_error(
        self, mock_notify_error: AsyncMock
    ) -> None:
        """ツール名が指定されていない場合はエラー通知のみ行う。"""
        msg = _make_message()
        bot = MagicMock()
        await runtask.handle_runtask(msg, "", {}, bot)
        mock_notify_error.assert_called_once()
        assert mock_notify_error.call_args[0][0] is bot
        assert "ツール名" in _notified_text(mock_notify_error)
        msg.reply.assert_not_called()

    async def test_unknown_tool_notifies_not_found(self, mock_notify_error: AsyncMock) -> None:
        msg = _make_message()
        await runtask.handle_runtask(msg, "no_such_tool", {}, MagicMock())
        mock_notify_error.assert_called_once()
        assert "見つかりません" in _notified_text(mock_notify_error)
        msg.reply.assert_not_called()

    async def test_task_tool_executes_without_reply(
        self, mock_notify_error: AsyncMock
    ) -> None:
        msg = _make_message()
        tools = _make_tools()
        bot = MagicMock()
        await runtask.handle_runtask(msg, "task_tool", tools, bot)
        tools["task_tool"]["instance"].execute.assert_called_once()
        call_args = tools["task_tool"]["instance"].execute.call_args[0][0]
        assert call_args["discord_client"] is bot
        msg.reply.assert_not_called()
        mock_notify_error.assert_not_called()

    async def test_strips_surrounding_spaces_from_tool_name(self) -> None:
        """引数の前後の空白は無視される。"""
        msg = _make_message()
        tools = _make_tools()
        await runtask.handle_runtask(msg, "  task_tool  ", tools, MagicMock())
        tools["task_tool"]["instance"].execute.assert_called_once()

    async def test_non_task_tool_notifies_unsupported(
        self, mock_notify_error: AsyncMock
    ) -> None:
        msg = _make_message()
        tools = _make_tools()
        await runtask.handle_runtask(msg, "other_tool", tools, MagicMock())
        mock_notify_error.assert_called_once()
        assert "対応していません" in _notified_text(mock_notify_error)
        msg.reply.assert_not_called()

    async def test_tool_execute_error_notifies_error(
        self, mock_notify_error: AsyncMock
    ) -> None:
        """ツール実行の例外は run_task 側で通知され、元チャンネルには返信しない。"""
        msg = _make_message()
        tools = _make_tools()
        tools["task_tool"]["instance"].execute = AsyncMock(side_effect=Exception("tool error"))
        await runtask.handle_runtask(msg, "task_tool", tools, MagicMock())
        msg.reply.assert_not_called()
        mock_notify_error.assert_called_once()
        assert "エラーが発生しました" in _notified_text(mock_notify_error)


class TestRunTask:
    async def test_unknown_tool_logs_warning(self, caplog) -> None:
        """存在しないツール名を渡すと警告ログを出して終了する。"""
        with caplog.at_level(logging.WARNING, logger="lilla_core.commands.runtask"):
            await runtask.run_task("no_such_tool", {}, MagicMock())
        assert any("not found" in r.message for r in caplog.records)

    async def test_non_task_tool_logs_warning(self, caplog) -> None:
        """trigger が task でないツールは警告ログを出して終了する。"""
        tools = _make_tools()
        with caplog.at_level(logging.WARNING, logger="lilla_core.commands.runtask"):
            await runtask.run_task("other_tool", tools, MagicMock())
        assert any("does not support manual execution" in r.message for r in caplog.records)

    async def test_task_tool_executes(self) -> None:
        """task ツールが正常に実行される。"""
        tools = _make_tools()
        bot = MagicMock()
        await runtask.run_task("task_tool", tools, bot)
        tools["task_tool"]["instance"].execute.assert_called_once()
        call_args = tools["task_tool"]["instance"].execute.call_args[0][0]
        assert call_args["discord_client"] is bot

    async def test_default_params_is_empty_dict(self) -> None:
        """params 未指定時は空 dict が渡される。"""
        tools = _make_tools()
        await runtask.run_task("task_tool", tools, MagicMock())
        call_args = tools["task_tool"]["instance"].execute.call_args[0][0]
        assert call_args["params"] == {}

    async def test_llm_tools_passed_to_execute(self, stub_llm_tools: dict) -> None:
        """get_llm_tools() の結果が tool.execute の args に渡される。"""
        tools = _make_tools()
        await runtask.run_task("task_tool", tools, MagicMock())
        call_args = tools["task_tool"]["instance"].execute.call_args[0][0]
        assert call_args["llm_tools"] is stub_llm_tools

    async def test_params_passed_through(self) -> None:
        """params が tool.execute の args に転送される。"""
        tools = _make_tools()
        await runtask.run_task(
            "task_tool", tools, MagicMock(), params={"date": "2026-05-15"}
        )
        call_args = tools["task_tool"]["instance"].execute.call_args[0][0]
        assert call_args["params"] == {"date": "2026-05-15"}

    async def test_tool_execute_error_notifies_error(
        self, mock_notify_error: AsyncMock
    ) -> None:
        """ツール実行中の例外は notify_error で通知される（再 raise しない）。"""
        tools = _make_tools()
        error = Exception("boom")
        tools["task_tool"]["instance"].execute = AsyncMock(side_effect=error)
        bot = MagicMock()

        await runtask.run_task("task_tool", tools, bot)

        mock_notify_error.assert_called_once_with(
            bot, "[RUNTASK] task_tool の実行中にエラーが発生しました", error
        )
