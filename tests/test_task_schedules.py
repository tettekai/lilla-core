"""`lilla_core.services.task_schedules` のテスト（#309）。

内部スケジューラ・ダッシュボード・`GET /api/tasks` が共有する、定期タスクの実効値の解決。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from lilla_core.services import task_schedules as svc
from lilla_core.services.task_schedules import TaskOverride, TaskScheduleError


def _tool(schedule: str | None, extension: str | None, scheduled: bool = True) -> dict:
    """task ツールのレジストリのエントリを作る。"""
    return {
        "instance": SimpleNamespace(schedule=schedule),
        "scheduled": scheduled,
        "extension": extension,
    }


@pytest.fixture
def tools() -> dict:
    """拡張のタスク・コアのタスク・非定期のタスクを含むレジストリ。"""
    return {
        "task_a": _tool("0 22 * * *", "diary"),
        "task_b": _tool("30 3 * * *", "diary"),
        "task_manual": _tool(None, "diary", scheduled=False),
        "task_core": _tool("0 2 * * *", None),
        "task_other": _tool("0 1 * * *", "habits"),
    }


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """上書きのリポジトリを差し替える（`docs` に「拡張名 -> ドキュメント」を置く）。"""
    repo = MagicMock()
    repo.docs = {}

    async def _find(names):
        return {name: doc for name, doc in repo.docs.items() if name in names}

    repo.find_by_extensions = AsyncMock(side_effect=_find)
    monkeypatch.setattr(
        "lilla_core.repository.extension_override_repository.get_extension_override_repo",
        lambda: repo,
    )
    return repo


@pytest.fixture
def app_timezone(monkeypatch: pytest.MonkeyPatch):
    """`ui.timezone` を差し替える関数を返す。"""
    from lilla_core.core.config import get_config

    def _set(name: str | None) -> None:
        monkeypatch.setattr(get_config().ui, "timezone", name)

    return _set


class TestScheduledTaskEntries:
    def test_lists_only_scheduled_tasks(self, tools) -> None:
        assert list(svc.scheduled_task_entries(tools)) == [
            "task_a", "task_b", "task_core", "task_other",
        ]

    def test_filters_by_extension(self, tools) -> None:
        assert list(svc.scheduled_task_entries(tools, "diary")) == ["task_a", "task_b"]
        assert svc.scheduled_task_entries(None) == {}


class TestParseTaskOverrides:
    def test_keeps_valid_types_and_reports_invalid(self, caplog) -> None:
        doc = {
            "tasks": {
                "task_a": {"schedule": "0 7 * * *", "enabled": "no"},
                "task_b": {"schedule": 5, "enabled": False},
                "task_x": {"enabled": False},
            }
        }
        with caplog.at_level("WARNING"):
            result = svc.parse_task_overrides("diary", doc, {"task_a", "task_b"})

        assert result == {
            "task_a": TaskOverride(schedule="0 7 * * *", invalid=("enabled",)),
            "task_b": TaskOverride(enabled=False, invalid=("schedule",)),
        }
        assert any("task_x" in r.message for r in caplog.records)

    def test_warn_false_is_silent(self, caplog) -> None:
        with caplog.at_level("WARNING"):
            svc.parse_task_overrides("diary", {"tasks": "bad"}, {"task_a"}, warn=False)
        assert caplog.records == []

    def test_only_limits_to_one_task(self) -> None:
        doc = {"tasks": {"task_a": {"enabled": False}, "task_b": {"enabled": False}}}
        result = svc.parse_task_overrides("diary", doc, {"task_a", "task_b"}, only="task_b")
        assert list(result) == ["task_b"]


class TestResolveTask:
    def test_defaults_without_override(self) -> None:
        resolved = svc.resolve_task("t", None, "0 2 * * *")
        assert (resolved.schedule, resolved.enabled) == ("0 2 * * *", True)

    def test_override_wins(self) -> None:
        resolved = svc.resolve_task(
            "t", "diary", "0 2 * * *", TaskOverride(schedule="0 9 * * mon", enabled=False)
        )
        assert (resolved.schedule, resolved.enabled) == ("0 9 * * mon", False)
        assert resolved.schedule_override_rejected is False

    def test_invalid_override_falls_back_to_default(self) -> None:
        resolved = svc.resolve_task("t", "diary", "0 2 * * *", TaskOverride(schedule="bad"))
        assert resolved.schedule == "0 2 * * *"
        assert resolved.schedule_override_rejected is True

    def test_invalid_default_has_no_schedule(self) -> None:
        resolved = svc.resolve_task("t", None, "not a cron")
        assert resolved.schedule is None
        assert resolved.enabled is True


class TestLoadTaskSchedules:
    async def test_applies_overrides_of_the_owning_extension_only(self, tools, repo) -> None:
        repo.docs = {
            "diary": {"tasks": {"task_a": {"schedule": "15 21 * * *"}, "task_b": {"enabled": False}}},
        }

        resolved = {t.name: t for t in await svc.load_task_schedules(tools)}

        assert list(resolved) == ["task_a", "task_b", "task_core", "task_other"]
        assert (resolved["task_a"].schedule, resolved["task_a"].enabled) == ("15 21 * * *", True)
        # 個別に無効化したタスクも一覧に残る
        assert (resolved["task_b"].schedule, resolved["task_b"].enabled) == ("30 3 * * *", False)
        assert (resolved["task_core"].schedule, resolved["task_core"].enabled) == ("0 2 * * *", True)
        repo.find_by_extensions.assert_awaited_once_with(["diary", "habits"])

    async def test_reads_latest_overrides_every_call(self, tools, repo) -> None:
        repo.docs = {"diary": {"tasks": {"task_a": {"enabled": False}}}}
        first = {t.name: t.enabled for t in await svc.load_task_schedules(tools)}
        repo.docs = {}
        second = {t.name: t.enabled for t in await svc.load_task_schedules(tools)}

        assert first["task_a"] is False
        assert second["task_a"] is True

    async def test_db_failure_is_not_hidden(self, tools, repo) -> None:
        repo.find_by_extensions = AsyncMock(side_effect=RuntimeError("db down"))
        with pytest.raises(RuntimeError):
            await svc.load_task_schedules(tools)

    async def test_unresolvable_schedule_raises(self, repo) -> None:
        tools = {"task_ok": _tool("0 1 * * *", None), "task_bad": _tool("not a cron", None)}
        with pytest.raises(TaskScheduleError) as exc:
            await svc.load_task_schedules(tools)
        assert exc.value.task_name == "task_bad"

    async def test_without_extension_tasks_skips_db(self, repo) -> None:
        resolved = await svc.load_task_schedules({"task_core": _tool("0 2 * * *", None)})
        assert [t.name for t in resolved] == ["task_core"]
        repo.find_by_extensions.assert_awaited_once_with([])


class TestCronSemantics:
    """ドキュメントに書く APScheduler 3 の解釈を固定する。"""

    def test_day_of_week_zero_is_monday(self, app_timezone) -> None:
        app_timezone("Asia/Tokyo")
        tz = ZoneInfo("Asia/Tokyo")
        trigger = svc.build_cron_trigger("0 9 * * 0")
        nxt = trigger.get_next_fire_time(None, datetime(2026, 11, 7, tzinfo=tz))  # 土曜
        assert nxt.strftime("%a") == "Mon"
        assert nxt.tzinfo is not None and nxt.utcoffset() == timedelta(hours=9)

    def test_day_and_weekday_are_anded(self, app_timezone) -> None:
        app_timezone("Asia/Tokyo")
        tz = ZoneInfo("Asia/Tokyo")
        trigger = svc.build_cron_trigger("0 9 1 * mon")
        nxt = trigger.get_next_fire_time(None, datetime(2026, 11, 7, tzinfo=tz))
        assert (nxt.year, nxt.month, nxt.day) == (2027, 2, 1)


class TestTimezoneLabel:
    def test_returns_configured_iana_name(self, app_timezone) -> None:
        app_timezone("Asia/Tokyo")
        assert svc.timezone_label() == "Asia/Tokyo"

    @pytest.mark.parametrize(
        ("offset", "expected"),
        [(timedelta(hours=9), "+09:00"), (timedelta(0), "+00:00"), (timedelta(hours=-3, minutes=-30), "-03:30")],
    )
    def test_os_local_is_utc_offset(self, app_timezone, monkeypatch, offset, expected) -> None:
        app_timezone(None)
        monkeypatch.setattr(svc, "local_now", lambda: datetime(2026, 11, 7, tzinfo=timezone(offset)))
        assert svc.timezone_label() == expected
