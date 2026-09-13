"""task_handler.py のテスト。"""
from __future__ import annotations

import sys
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from lilla_core.handlers import task_handler
from lilla_core.utils.datetime_utils import local_timezone


sys.path.insert(0, "src")


@pytest.fixture
def app_timezone(monkeypatch):
    """`ui.timezone` を差し替える関数を返す（テスト終了時に元の値へ戻る）。"""
    from lilla_core.core.config import get_config

    def _set(name: str | None) -> None:
        monkeypatch.setattr(get_config().ui, "timezone", name)

    return _set


@pytest.fixture(autouse=True)
def _reset_scheduler():
    """各テストの前後でグローバルスケジューラをリセットする。"""
    task_handler._scheduler = None
    yield
    task_handler._scheduler = None


# ---------------------------------------------------------------------------
# TestStartScheduler
# ---------------------------------------------------------------------------


class TestStartScheduler:
    def _make_tools(self, schedule="0 7 * * *", scheduled=True):
        """テスト用のツール辞書を生成する。"""
        tool = MagicMock()
        tool.schedule = schedule
        return {
            "my_tool": {"instance": tool, "trigger": "task", "scheduled": scheduled}
        }

    @patch("lilla_core.handlers.task_handler.BackgroundScheduler")
    def test_registers_scheduled_jobs(self, mock_scheduler_cls) -> None:
        """スケジュール設定済みタスクが add_job で登録されること。"""
        mock_scheduler = MagicMock()
        mock_scheduler_cls.return_value = mock_scheduler
        tools = self._make_tools()
        bot = MagicMock()

        task_handler.start_scheduler(tools, bot)

        mock_scheduler_cls.assert_called_once_with(
            timezone=local_timezone(),
            job_defaults={"misfire_grace_time": 3600, "coalesce": True},
        )
        mock_scheduler.add_job.assert_called_once()
        call_kwargs = mock_scheduler.add_job.call_args
        assert call_kwargs[1]["id"] == "my_tool"
        mock_scheduler.start.assert_called_once()

    @patch("lilla_core.handlers.task_handler.BackgroundScheduler")
    def test_scheduler_uses_configured_timezone(self, mock_scheduler_cls, app_timezone) -> None:
        """スケジューラのタイムゾーンが `ui.timezone` の解決結果になること。"""
        app_timezone("UTC")
        mock_scheduler_cls.return_value = MagicMock()

        task_handler.start_scheduler(self._make_tools(), MagicMock())

        assert mock_scheduler_cls.call_args[1]["timezone"] == ZoneInfo("UTC")

    @patch("lilla_core.handlers.task_handler.BackgroundScheduler")
    def test_cron_trigger_uses_configured_timezone(self, mock_scheduler_cls, app_timezone) -> None:
        """crontab 式も同じタイムゾーンで解釈されること。

        `CronTrigger` はインスタンスとして渡すとスケジューラの timezone を
        引き継がないため、明示的に渡していることを固定する。
        """
        app_timezone("Asia/Tokyo")
        mock_scheduler = MagicMock()
        mock_scheduler_cls.return_value = mock_scheduler

        task_handler.start_scheduler(self._make_tools(), MagicMock())

        trigger = mock_scheduler.add_job.call_args[0][1]
        assert trigger.timezone == ZoneInfo("Asia/Tokyo")

    @patch("lilla_core.handlers.task_handler.BackgroundScheduler")
    def test_skips_unscheduled_tools(self, mock_scheduler_cls) -> None:
        """scheduled=False のツールはスキップされること。"""
        mock_scheduler = MagicMock()
        mock_scheduler_cls.return_value = mock_scheduler
        tools = self._make_tools(scheduled=False)
        bot = MagicMock()

        task_handler.start_scheduler(tools, bot)

        mock_scheduler.add_job.assert_not_called()
        mock_scheduler.start.assert_called_once()

    @patch("lilla_core.handlers.task_handler.BackgroundScheduler")
    def test_idempotent(self, mock_scheduler_cls) -> None:
        """2回呼んでもスケジューラは1つしか作られないこと。"""
        mock_scheduler = MagicMock()
        mock_scheduler_cls.return_value = mock_scheduler
        tools = self._make_tools()
        bot = MagicMock()

        task_handler.start_scheduler(tools, bot)
        task_handler.start_scheduler(tools, bot)

        mock_scheduler_cls.assert_called_once()

    @patch("lilla_core.handlers.task_handler.BackgroundScheduler")
    def test_skips_tool_without_schedule(self, mock_scheduler_cls) -> None:
        """scheduleが空のツールはスキップされること。"""
        mock_scheduler = MagicMock()
        mock_scheduler_cls.return_value = mock_scheduler
        tools = self._make_tools(schedule="")
        bot = MagicMock()

        task_handler.start_scheduler(tools, bot)

        mock_scheduler.add_job.assert_not_called()

    @patch("lilla_core.handlers.task_handler.BackgroundScheduler")
    def test_registers_multiple_scheduled_tools(self, mock_scheduler_cls) -> None:
        """複数のスケジュール済みタスクが全て登録されること。"""
        mock_scheduler = MagicMock()
        mock_scheduler_cls.return_value = mock_scheduler

        tool_a = MagicMock()
        tool_a.schedule = "0 7 * * *"
        tool_b = MagicMock()
        tool_b.schedule = "30 9 * * 1-5"
        tools = {
            "tool_a": {"instance": tool_a, "trigger": "task", "scheduled": True},
            "tool_b": {"instance": tool_b, "trigger": "task", "scheduled": True},
        }
        bot = MagicMock()

        task_handler.start_scheduler(tools, bot)

        assert mock_scheduler.add_job.call_count == 2

    @patch("lilla_core.handlers.task_handler.CronTrigger.from_crontab")
    @patch("lilla_core.handlers.task_handler.BackgroundScheduler")
    def test_invalid_cron_is_skipped_and_scheduler_still_starts(
        self, mock_scheduler_cls, mock_from_crontab, caplog: pytest.LogCaptureFixture
    ) -> None:
        """不正な cron は WARNING でスキップし、それでもスケジューラは start する。"""
        mock_scheduler = MagicMock()
        mock_scheduler_cls.return_value = mock_scheduler
        mock_from_crontab.side_effect = ValueError("bad cron")
        tools = self._make_tools(schedule="not a cron")
        bot = MagicMock()

        with caplog.at_level("WARNING"):
            task_handler.start_scheduler(tools, bot)

        mock_scheduler.add_job.assert_not_called()
        mock_scheduler.start.assert_called_once()
        assert any("invalid cron" in record.message for record in caplog.records)

    @patch("lilla_core.handlers.task_handler.CronTrigger.from_crontab")
    @patch("lilla_core.handlers.task_handler.BackgroundScheduler")
    def test_valid_cron_registered_when_mixed_with_invalid(
        self, mock_scheduler_cls, mock_from_crontab
    ) -> None:
        """不正な cron と正当な cron が混在するとき、正当な方だけ登録されること。"""
        mock_scheduler = MagicMock()
        mock_scheduler_cls.return_value = mock_scheduler
        valid_trigger = MagicMock()
        mock_from_crontab.side_effect = [ValueError("bad cron"), valid_trigger]

        tool_bad = MagicMock()
        tool_bad.schedule = "not a cron"
        tool_good = MagicMock()
        tool_good.schedule = "0 7 * * *"
        tools = {
            "bad_tool": {"instance": tool_bad, "trigger": "task", "scheduled": True},
            "good_tool": {"instance": tool_good, "trigger": "task", "scheduled": True},
        }
        bot = MagicMock()

        task_handler.start_scheduler(tools, bot)

        mock_scheduler.add_job.assert_called_once()
        assert mock_scheduler.add_job.call_args[1]["id"] == "good_tool"
        mock_scheduler.start.assert_called_once()


# ---------------------------------------------------------------------------
# TestStopScheduler
# ---------------------------------------------------------------------------


class TestStopScheduler:
    @patch("lilla_core.handlers.task_handler.BackgroundScheduler")
    def test_shuts_down_running_scheduler(self, mock_scheduler_cls) -> None:
        """起動中のスケジューラを shutdown(wait=False) で停止すること。"""
        mock_scheduler = MagicMock()
        mock_scheduler_cls.return_value = mock_scheduler
        task_handler.start_scheduler(self._make_tools(), MagicMock())

        task_handler.stop_scheduler()

        mock_scheduler.shutdown.assert_called_once_with(wait=False)
        assert task_handler._scheduler is None

    def test_noop_when_not_started(self) -> None:
        """未起動の場合は何もせず例外にもならないこと。"""
        task_handler.stop_scheduler()  # 例外にならないことを確認
        assert task_handler._scheduler is None

    @patch("lilla_core.handlers.task_handler.BackgroundScheduler")
    def test_allows_restart_after_stop(self, mock_scheduler_cls) -> None:
        """停止後は start_scheduler で再度スケジューラを作れること。"""
        mock_scheduler_cls.side_effect = [MagicMock(), MagicMock()]
        task_handler.start_scheduler(self._make_tools(), MagicMock())
        task_handler.stop_scheduler()

        task_handler.start_scheduler(self._make_tools(), MagicMock())

        assert mock_scheduler_cls.call_count == 2

    def _make_tools(self, schedule="0 7 * * *", scheduled=True):
        """テスト用のツール辞書を生成する。"""
        tool = MagicMock()
        tool.schedule = schedule
        return {
            "my_tool": {"instance": tool, "trigger": "task", "scheduled": scheduled}
        }


# ---------------------------------------------------------------------------
# TestMakeJobFunc
# ---------------------------------------------------------------------------


class TestMakeJobFunc:
    def test_calls_run_coroutine_threadsafe(self) -> None:
        """生成されたジョブ関数が asyncio.run_coroutine_threadsafe を呼ぶこと。"""
        tool = MagicMock()
        bot = MagicMock()

        with patch("lilla_core.handlers.task_handler.asyncio.run_coroutine_threadsafe") as mock_run:
            job_func = task_handler._make_job_func("test_tool", tool, bot, {})
            job_func()

            mock_run.assert_called_once()
            coro, loop_arg = mock_run.call_args[0]
            coro.close()  # 未 await コルーチンを明示的に閉じて RuntimeWarning を抑制
            assert loop_arg is bot.loop

    def test_passes_bot_loop(self) -> None:
        """生成されたジョブ関数が bot.loop を渡すこと。"""
        tool = MagicMock()
        bot = MagicMock()

        with patch("lilla_core.handlers.task_handler.asyncio.run_coroutine_threadsafe") as mock_run:
            job_func = task_handler._make_job_func("test_tool", tool, bot, {})
            job_func()

            coro, loop_arg = mock_run.call_args[0]
            coro.close()  # 未 await コルーチンを明示的に閉じて RuntimeWarning を抑制
            assert loop_arg is bot.loop


# ---------------------------------------------------------------------------
# TestRunTool
# ---------------------------------------------------------------------------


class TestRunTool:
    async def test_calls_execute(self) -> None:
        """_run_tool が tool.execute を正しく呼ぶこと。"""
        tool = MagicMock()
        tool.execute = AsyncMock(return_value="ok")
        bot = MagicMock()
        now = datetime.now()
        llm_tools = {"dummy": {}}

        await task_handler._run_tool("test_tool", tool, bot, now, llm_tools)

        tool.execute.assert_called_once()
        call_args = tool.execute.call_args[0][0]
        assert call_args["discord_client"] is bot
        assert call_args["now"] is now
        assert call_args["llm_tools"] is llm_tools

    async def test_extension_context_providers_are_merged(
        self, make_extension, use_extensions
    ) -> None:
        """拡張の tool_context_providers() の値が、コア確定キーと一緒に context へ入る。"""
        client = MagicMock()
        use_extensions(make_extension("pack", tool_context_providers={"google_client": lambda: client}))
        tool = MagicMock()
        tool.execute = AsyncMock(return_value="ok")
        bot = MagicMock()
        now = datetime.now()

        await task_handler._run_tool("test_tool", tool, bot, now, {})

        context = tool.execute.call_args[0][0]
        assert context["google_client"] is client
        assert context["discord_client"] is bot
        assert context["now"] is now

    async def test_failing_provider_does_not_block_execution(
        self, make_extension, use_extensions
    ) -> None:
        """プロバイダが失敗してもそのキーが欠けるだけで、ツール自体は実行される。"""

        def _raise():
            raise RuntimeError("not configured")

        use_extensions(make_extension("pack", tool_context_providers={"google_client": _raise}))
        tool = MagicMock()
        tool.execute = AsyncMock(return_value="ok")

        await task_handler._run_tool("test_tool", tool, MagicMock(), datetime.now(), {})

        context = tool.execute.call_args[0][0]
        assert "google_client" not in context
        assert "discord_client" in context

    async def test_handles_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ツール実行で例外が起きても外に漏れず、エラー通知が行われること。"""
        tool = MagicMock()
        error = Exception("crash")
        tool.execute = AsyncMock(side_effect=error)
        bot = MagicMock()
        now = datetime.now()

        mock_notify_error = AsyncMock()
        monkeypatch.setattr(task_handler, "notify_error", mock_notify_error)

        # 例外が raise されないことを確認
        await task_handler._run_tool("crash_tool", tool, bot, now, {})

        mock_notify_error.assert_called_once_with(
            bot, "[TASK] crash_tool の実行中にエラーが発生しました", error
        )
