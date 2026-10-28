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
    task_handler._task_jobs.clear()
    task_handler._applied_schedules.clear()
    yield
    task_handler._scheduler = None
    task_handler._task_jobs.clear()
    task_handler._applied_schedules.clear()


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
    async def test_registers_scheduled_jobs(self, mock_scheduler_cls) -> None:
        """スケジュール設定済みタスクが add_job で登録されること。"""
        mock_scheduler = MagicMock()
        mock_scheduler_cls.return_value = mock_scheduler
        tools = self._make_tools()
        bot = MagicMock()

        await task_handler.start_scheduler(tools, bot)

        mock_scheduler_cls.assert_called_once_with(
            timezone=local_timezone(),
            job_defaults={"misfire_grace_time": 3600, "coalesce": True},
        )
        mock_scheduler.add_job.assert_called_once()
        call_kwargs = mock_scheduler.add_job.call_args
        assert call_kwargs[1]["id"] == "my_tool"
        mock_scheduler.start.assert_called_once()

    @patch("lilla_core.handlers.task_handler.BackgroundScheduler")
    async def test_scheduler_uses_configured_timezone(self, mock_scheduler_cls, app_timezone) -> None:
        """スケジューラのタイムゾーンが `ui.timezone` の解決結果になること。"""
        app_timezone("UTC")
        mock_scheduler_cls.return_value = MagicMock()

        await task_handler.start_scheduler(self._make_tools(), MagicMock())

        assert mock_scheduler_cls.call_args[1]["timezone"] == ZoneInfo("UTC")

    @patch("lilla_core.handlers.task_handler.BackgroundScheduler")
    async def test_cron_trigger_uses_configured_timezone(self, mock_scheduler_cls, app_timezone) -> None:
        """crontab 式も同じタイムゾーンで解釈されること。

        `CronTrigger` はインスタンスとして渡すとスケジューラの timezone を
        引き継がないため、明示的に渡していることを固定する。
        """
        app_timezone("Asia/Tokyo")
        mock_scheduler = MagicMock()
        mock_scheduler_cls.return_value = mock_scheduler

        await task_handler.start_scheduler(self._make_tools(), MagicMock())

        trigger = mock_scheduler.add_job.call_args[0][1]
        assert trigger.timezone == ZoneInfo("Asia/Tokyo")

    @patch("lilla_core.handlers.task_handler.BackgroundScheduler")
    async def test_skips_unscheduled_tools(self, mock_scheduler_cls) -> None:
        """scheduled=False のツールはスキップされること。"""
        mock_scheduler = MagicMock()
        mock_scheduler_cls.return_value = mock_scheduler
        tools = self._make_tools(scheduled=False)
        bot = MagicMock()

        await task_handler.start_scheduler(tools, bot)

        mock_scheduler.add_job.assert_not_called()
        mock_scheduler.start.assert_called_once()

    @patch("lilla_core.handlers.task_handler.BackgroundScheduler")
    async def test_idempotent(self, mock_scheduler_cls) -> None:
        """2回呼んでもスケジューラは1つしか作られないこと。"""
        mock_scheduler = MagicMock()
        mock_scheduler_cls.return_value = mock_scheduler
        tools = self._make_tools()
        bot = MagicMock()

        await task_handler.start_scheduler(tools, bot)
        await task_handler.start_scheduler(tools, bot)

        mock_scheduler_cls.assert_called_once()

    @patch("lilla_core.handlers.task_handler.BackgroundScheduler")
    async def test_skips_tool_without_schedule(self, mock_scheduler_cls) -> None:
        """scheduleが空のツールはスキップされること。"""
        mock_scheduler = MagicMock()
        mock_scheduler_cls.return_value = mock_scheduler
        tools = self._make_tools(schedule="")
        bot = MagicMock()

        await task_handler.start_scheduler(tools, bot)

        mock_scheduler.add_job.assert_not_called()

    @patch("lilla_core.handlers.task_handler.BackgroundScheduler")
    async def test_registers_multiple_scheduled_tools(self, mock_scheduler_cls) -> None:
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

        await task_handler.start_scheduler(tools, bot)

        assert mock_scheduler.add_job.call_count == 2

    @patch("lilla_core.handlers.task_handler.CronTrigger.from_crontab")
    @patch("lilla_core.handlers.task_handler.BackgroundScheduler")
    async def test_invalid_cron_is_skipped_and_scheduler_still_starts(
        self, mock_scheduler_cls, mock_from_crontab, caplog: pytest.LogCaptureFixture
    ) -> None:
        """不正な cron は WARNING でスキップし、それでもスケジューラは start する。"""
        mock_scheduler = MagicMock()
        mock_scheduler_cls.return_value = mock_scheduler
        mock_from_crontab.side_effect = ValueError("bad cron")
        tools = self._make_tools(schedule="not a cron")
        bot = MagicMock()

        with caplog.at_level("WARNING"):
            await task_handler.start_scheduler(tools, bot)

        mock_scheduler.add_job.assert_not_called()
        mock_scheduler.start.assert_called_once()
        assert any("invalid cron" in record.message for record in caplog.records)

    @patch("lilla_core.handlers.task_handler.CronTrigger.from_crontab")
    @patch("lilla_core.handlers.task_handler.BackgroundScheduler")
    async def test_valid_cron_registered_when_mixed_with_invalid(
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

        await task_handler.start_scheduler(tools, bot)

        mock_scheduler.add_job.assert_called_once()
        assert mock_scheduler.add_job.call_args[1]["id"] == "good_tool"
        mock_scheduler.start.assert_called_once()


# ---------------------------------------------------------------------------
# TestStopScheduler
# ---------------------------------------------------------------------------


class TestStopScheduler:
    @patch("lilla_core.handlers.task_handler.BackgroundScheduler")
    async def test_shuts_down_running_scheduler(self, mock_scheduler_cls) -> None:
        """起動中のスケジューラを shutdown(wait=False) で停止すること。"""
        mock_scheduler = MagicMock()
        mock_scheduler_cls.return_value = mock_scheduler
        await task_handler.start_scheduler(self._make_tools(), MagicMock())

        task_handler.stop_scheduler()

        mock_scheduler.shutdown.assert_called_once_with(wait=False)
        assert task_handler._scheduler is None

    def test_noop_when_not_started(self) -> None:
        """未起動の場合は何もせず例外にもならないこと。"""
        task_handler.stop_scheduler()  # 例外にならないことを確認
        assert task_handler._scheduler is None

    @patch("lilla_core.handlers.task_handler.BackgroundScheduler")
    async def test_allows_restart_after_stop(self, mock_scheduler_cls) -> None:
        """停止後は start_scheduler で再度スケジューラを作れること。"""
        mock_scheduler_cls.side_effect = [MagicMock(), MagicMock()]
        await task_handler.start_scheduler(self._make_tools(), MagicMock())
        task_handler.stop_scheduler()

        await task_handler.start_scheduler(self._make_tools(), MagicMock())

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

    async def test_sets_task_name_as_llm_call_origin(self) -> None:
        """タスクの実行中は、LLM 使用量の記録の入口としてタスク名が積まれる。"""
        from lilla_core.core.llm_call_origin import get_llm_call_origin

        seen = []
        tool = MagicMock()
        tool.execute = AsyncMock(side_effect=lambda ctx: seen.append(get_llm_call_origin()))

        await task_handler._run_tool("task_daily", tool, MagicMock(), datetime.now(), {})

        assert seen[0].task_name == "task_daily"
        assert get_llm_call_origin().task_name is None

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


# ---------------------------------------------------------------------------
# TestTaskOverrides
# ---------------------------------------------------------------------------


class TestTaskOverrides:
    """`extension_overrides` の `tasks` による上書きと、稼働中のスケジューラへの反映。

    実物の `BackgroundScheduler` を起動し、ジョブのトリガーと次回実行時刻を見る
    （cron は遠い時刻にしてあり、テスト中にジョブは走らない）。
    """

    DEFAULT = "0 7 * * *"

    @pytest.fixture
    def repo(self, monkeypatch):
        """上書きのリポジトリをモックへ差し替え、返すドキュメントを `docs` で変えられるようにする。"""
        repo = MagicMock()
        repo.docs = {}

        async def _find(names):
            return {name: doc for name, doc in repo.docs.items() if name in names}

        repo.find_by_extensions = AsyncMock(side_effect=_find)
        monkeypatch.setattr(task_handler, "get_extension_override_repo", lambda: repo)
        return repo

    @pytest.fixture
    def diary(self, make_extension, use_extensions):
        """拡張 `diary` を登録する。"""
        use_extensions(make_extension("diary"))

    @pytest.fixture(autouse=True)
    def _stop(self):
        """テスト後に実物のスケジューラを止める。"""
        yield
        task_handler.stop_scheduler()

    def _tools(self, **schedules):
        """`diary` が同梱したタスクと、拡張に属さないタスクを含むツール辞書を作る。"""
        tools = {}
        for name, schedule in schedules.items():
            tool = MagicMock()
            tool.schedule = schedule
            tools[name] = {
                "instance": tool, "trigger": "task", "scheduled": True,
                "extension": None if name.startswith("task_core") else "diary",
            }
        return tools

    def _job(self, name):
        return task_handler._scheduler.get_job(name)

    def _fields(self, name):
        """ジョブのトリガーの cron フィールドを文字列で返す。"""
        return {f.name: str(f) for f in self._job(name).trigger.fields}

    async def test_startup_applies_schedule_override(self, repo, diary) -> None:
        """起動時に上書きの schedule でジョブが登録される。上書きの無いタスクはデフォルト。"""
        repo.docs = {"diary": {"extension": "diary", "tasks": {"task_a": {"schedule": "30 21 * * *"}}}}

        await task_handler.start_scheduler(
            self._tools(task_a=self.DEFAULT, task_b=self.DEFAULT, task_core=self.DEFAULT), MagicMock()
        )

        assert self._fields("task_a")["hour"] == "21"
        assert self._fields("task_b")["hour"] == "7"
        assert self._fields("task_core")["hour"] == "7"
        repo.find_by_extensions.assert_awaited_once_with(["diary"])

    async def test_reapply_changes_trigger_and_next_run(self, repo, diary) -> None:
        """上書きを入れて反映すると、ジョブのトリガーと次回実行時刻が変わる。"""
        await task_handler.start_scheduler(self._tools(task_a=self.DEFAULT), MagicMock())
        before = self._job("task_a").next_run_time

        repo.docs = {"diary": {"extension": "diary", "tasks": {"task_a": {"schedule": "15 22 * * *"}}}}
        await task_handler.apply_task_overrides("diary")

        after = self._job("task_a").next_run_time
        assert self._fields("task_a")["hour"] == "22"
        assert self._fields("task_a")["minute"] == "15"
        assert after != before
        assert (after.hour, after.minute) == (22, 15)

    async def test_disable_then_clear_restores_default(self, repo, diary) -> None:
        """`enabled: false` でジョブが外れ、上書きを消して反映するとデフォルトで登録し直される。"""
        await task_handler.start_scheduler(self._tools(task_a=self.DEFAULT), MagicMock())

        repo.docs = {"diary": {"extension": "diary", "tasks": {"task_a": {"enabled": False, "schedule": "1 1 * * *"}}}}
        await task_handler.apply_task_overrides("diary", "task_a")
        assert self._job("task_a") is None

        repo.docs = {}
        await task_handler.apply_task_overrides("diary", "task_a")
        assert self._job("task_a") is not None
        assert self._fields("task_a")["hour"] == "7"
        assert self._fields("task_a")["minute"] == "0"

    async def test_disabled_at_startup_is_not_registered(self, repo, diary) -> None:
        """起動時に `enabled: false` のタスクはジョブを登録しない。"""
        repo.docs = {"diary": {"extension": "diary", "tasks": {"task_a": {"enabled": False}}}}

        await task_handler.start_scheduler(self._tools(task_a=self.DEFAULT), MagicMock())

        assert self._job("task_a") is None

    async def test_invalid_cron_override_falls_back_with_warning(self, repo, diary, caplog) -> None:
        """不正な crontab の上書きは、拡張名とツール名を含む WARNING を出してデフォルトのまま。"""
        repo.docs = {"diary": {"extension": "diary", "tasks": {"task_a": {"schedule": "not a cron"}}}}

        with caplog.at_level("WARNING"):
            await task_handler.start_scheduler(self._tools(task_a=self.DEFAULT), MagicMock())

        assert self._fields("task_a")["hour"] == "7"
        assert any(
            "task_a" in r.getMessage() and "diary" in r.getMessage() and r.levelname == "WARNING"
            for r in caplog.records
        )

    async def test_invalid_cron_on_reapply_keeps_default(self, repo, diary, caplog) -> None:
        """反映時の不正な crontab も、デフォルトのスケジュールを保つ。"""
        repo.docs = {"diary": {"extension": "diary", "tasks": {"task_a": {"schedule": "30 21 * * *"}}}}
        await task_handler.start_scheduler(self._tools(task_a=self.DEFAULT), MagicMock())

        repo.docs = {"diary": {"extension": "diary", "tasks": {"task_a": {"schedule": "99 99 * * *"}}}}
        with caplog.at_level("WARNING"):
            await task_handler.apply_task_overrides("diary")

        assert self._fields("task_a")["hour"] == "7"
        assert any("invalid cron" in r.getMessage() for r in caplog.records)

    async def test_unknown_tool_name_is_ignored_with_warning(self, repo, diary, caplog) -> None:
        """拡張が同梱していないツール名（拡張に属さないタスクを含む）は無視して WARNING。"""
        repo.docs = {"diary": {"extension": "diary", "tasks": {
            "task_core": {"enabled": False},
            "task_missing": {"schedule": "1 1 * * *"},
        }}}

        with caplog.at_level("WARNING"):
            await task_handler.start_scheduler(
                self._tools(task_a=self.DEFAULT, task_core=self.DEFAULT), MagicMock()
            )

        assert self._job("task_core") is not None
        assert self._fields("task_a")["hour"] == "7"
        messages = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("task_core" in m and "diary" in m for m in messages)
        assert any("task_missing" in m and "diary" in m for m in messages)

    @pytest.mark.parametrize(
        ("tasks", "needle"),
        [
            (["x"], "'tasks' must be an object"),
            ({"task_a": "0 1 * * *"}, "expected an object"),
            ({"task_a": {"schedule": 5}}, "expected a string"),
            ({"task_a": {"enabled": "no"}}, "expected a boolean"),
        ],
    )
    async def test_invalid_values_fall_back_to_default(self, repo, diary, caplog, tasks, needle) -> None:
        """型の不正な上書きは WARNING を出してデフォルトになる。"""
        repo.docs = {"diary": {"extension": "diary", "tasks": tasks}}

        with caplog.at_level("WARNING"):
            await task_handler.start_scheduler(self._tools(task_a=self.DEFAULT), MagicMock())

        assert self._fields("task_a")["hour"] == "7"
        assert any(needle in r.getMessage() and "diary" in r.getMessage() for r in caplog.records)

    async def test_document_without_tasks_keeps_defaults(self, repo, diary) -> None:
        """`tasks` キーの無いドキュメント（`prompt` だけ）は全タスクがデフォルト。"""
        repo.docs = {"diary": {"extension": "diary", "prompt": "上書き"}}

        await task_handler.start_scheduler(self._tools(task_a=self.DEFAULT, task_b="5 6 * * *"), MagicMock())

        assert self._fields("task_a")["hour"] == "7"
        assert self._fields("task_b")["hour"] == "6"

    async def test_load_failure_registers_defaults(self, repo, diary, caplog) -> None:
        """MongoDB の読み込みに失敗したら WARNING を出して全タスクをデフォルトで登録する。"""
        repo.find_by_extensions.side_effect = RuntimeError("down")

        with caplog.at_level("WARNING"):
            await task_handler.start_scheduler(self._tools(task_a=self.DEFAULT), MagicMock())

        assert self._fields("task_a")["hour"] == "7"
        assert task_handler._scheduler.running
        assert any("Failed to load extension overrides" in r.getMessage() for r in caplog.records)

    async def test_no_extensions_does_not_read_db(self, repo, use_extensions) -> None:
        """拡張が 1 つも無ければ DB を読まない。"""
        use_extensions()

        await task_handler.start_scheduler(self._tools(task_core=self.DEFAULT), MagicMock())

        repo.find_by_extensions.assert_not_awaited()
        assert self._job("task_core") is not None

    async def test_reapply_without_scheduler_is_noop(self, repo, caplog) -> None:
        """スケジューラが動いていなければ何もせず WARNING。"""
        with caplog.at_level("WARNING"):
            await task_handler.apply_task_overrides("diary")

        repo.find_by_extensions.assert_not_awaited()
        assert any("Scheduler is not running" in r.getMessage() for r in caplog.records)

    async def test_reapply_unknown_task_name_is_noop(self, repo, diary, caplog) -> None:
        """拡張が同梱していないタスク名を指定した反映は、DB を読まずに WARNING。"""
        await task_handler.start_scheduler(self._tools(task_a=self.DEFAULT), MagicMock())
        repo.find_by_extensions.reset_mock()

        with caplog.at_level("WARNING"):
            await task_handler.apply_task_overrides("diary", "task_missing")

        repo.find_by_extensions.assert_not_awaited()
        assert any("task_missing" in r.getMessage() for r in caplog.records)

    async def test_reapply_single_task_leaves_others(self, repo, diary) -> None:
        """1 タスクだけの反映は、同じ拡張の他のタスクに触れない。"""
        await task_handler.start_scheduler(self._tools(task_a=self.DEFAULT, task_b=self.DEFAULT), MagicMock())

        repo.docs = {"diary": {"extension": "diary", "tasks": {
            "task_a": {"schedule": "0 9 * * *"}, "task_b": {"schedule": "0 10 * * *"},
        }}}
        await task_handler.apply_task_overrides("diary", "task_a")

        assert self._fields("task_a")["hour"] == "9"
        assert self._fields("task_b")["hour"] == "7"
