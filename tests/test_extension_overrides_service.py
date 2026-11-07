"""`lilla_core.services.extension_overrides` のテスト。

ダッシュボードの編集ページが使う、編集対象の列挙・画面の内容の組み立て・
保存リクエストの検証を確かめる。
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from lilla_core.services import extension_overrides as svc
from lilla_core.services.extension_overrides import OverrideValidationError

PROMPTS = {"ja": "日本語のデフォルト", "en": "English default"}


def _tool(schedule: str | None, extension: str | None, scheduled: bool = True) -> dict:
    """task ツールのレジストリのエントリを作る。"""
    return {
        "instance": SimpleNamespace(schedule=schedule),
        "scheduled": scheduled,
        "extension": extension,
    }


@pytest.fixture
def tools() -> dict:
    """`diary` の定期タスク 2 つと、対象外のツールを含むレジストリ。"""
    return {
        "task_a": _tool("0 22 * * *", "diary"),
        "task_b": _tool("30 3 * * *", "diary"),
        "task_manual": _tool(None, "diary", scheduled=False),
        "task_core": _tool("0 2 * * *", None),
        "task_other": _tool("0 1 * * *", "habits"),
    }


@pytest.fixture
def registered(make_extension, use_extensions):
    """`diary`（プロンプトあり）・`habits`（プロンプトなし）・`plain`（何も無し）を登録する。"""
    use_extensions(
        make_extension("diary", default_prompt=PROMPTS),
        make_extension("habits"),
        make_extension("plain"),
    )


class TestTargets:
    """編集対象の列挙。"""

    def test_task_entries_are_only_scheduled_tasks_of_the_extension(self, tools) -> None:
        """その拡張の、schedule を持つ定期タスクだけを返す（コアや他拡張のものは除く）。"""
        assert list(svc.task_entries_of("diary", tools)) == ["task_a", "task_b"]
        assert list(svc.task_entries_of("habits", tools)) == ["task_other"]
        assert svc.task_entries_of("plain", tools) == {}
        assert svc.task_entries_of("diary", None) == {}

    def test_list_editable_marks_extensions(self, registered, tools) -> None:
        """プロンプトかタスクを持つ拡張だけが編集できる。"""
        items = {item["name"]: item for item in svc.list_editable(tools)}

        assert items["diary"] == {
            "name": "diary", "has_prompt": True, "task_count": 2, "has_settings": False, "editable": True,
        }
        assert items["habits"]["editable"] is True
        assert items["habits"]["has_prompt"] is False
        assert items["plain"]["editable"] is False

    def test_find_extension(self, registered) -> None:
        """ロード済みの拡張だけを見つける。"""
        assert svc.find_extension("diary").name == "diary"
        assert svc.find_extension("missing") is None


class TestBuildDetail:
    """画面に出す内容の組み立て。"""

    def _detail(self, tools, doc, locale="ja", applied=None, running=True, name="diary"):
        return svc.build_detail(name, tools, doc, locale, applied or {}, running)

    def test_reports_scheduler_enabled(self, registered, tools) -> None:
        """設定上の有効・無効を、起動済みかどうかとは別に返す。"""
        assert self._detail(tools, None, running=False)["scheduler_enabled"] is True
        detail = svc.build_detail("diary", tools, None, "ja", {}, False, False)
        assert detail["scheduler_enabled"] is False
        assert detail["scheduler_running"] is False

    def test_defaults_without_override(self, registered, tools) -> None:
        """上書きが無ければ、プロンプトもタスクもデフォルトが実効値になる。"""
        detail = self._detail(
            tools, None, applied={"task_a": "0 22 * * *", "task_b": "30 3 * * *"}
        )

        assert detail["prompt"] == {
            "default": PROMPTS["ja"],
            "default_locale": "ja",
            "overridden": False,
            "override": None,
            "invalid": False,
            "effective": PROMPTS["ja"],
        }
        task_a = detail["tasks"][0]
        assert task_a["name"] == "task_a"
        assert task_a["default"] == {"schedule": "0 22 * * *", "enabled": True}
        assert task_a["override"] == {}
        assert task_a["effective"] == {"schedule": "0 22 * * *", "enabled": True}
        assert task_a["in_sync"] is True
        assert detail["updated_at"] is None
        assert detail["scheduler_running"] is True
        assert detail["timezone"]

    def test_default_prompt_follows_locale(self, registered, tools) -> None:
        """デフォルトは `ui.locale` に合う言語を選び、無ければ ja に落ちる。"""
        assert self._detail(tools, None, locale="en")["prompt"]["default_locale"] == "en"
        assert self._detail(tools, None, locale="fr")["prompt"]["default_locale"] == "ja"

    def test_empty_prompt_override_is_effective(self, registered, tools) -> None:
        """空文字の上書きは「注入しない」という有効な値として扱う。"""
        detail = self._detail(tools, {"prompt": ""})

        assert detail["prompt"]["overridden"] is True
        assert detail["prompt"]["override"] == ""
        assert detail["prompt"]["effective"] == ""

    def test_invalid_prompt_override_falls_back(self, registered, tools) -> None:
        """文字列でない上書きは不正として示し、デフォルトを実効値にする。"""
        detail = self._detail(tools, {"prompt": 42})

        assert detail["prompt"]["invalid"] is True
        assert detail["prompt"]["effective"] == PROMPTS["ja"]

    def test_task_overrides_and_sync_state(self, registered, tools) -> None:
        """タスクの上書きを実効値へ重ね、適用中の値とのずれを示す。"""
        updated = datetime(2026, 11, 7, 1, 2, 3, tzinfo=timezone.utc)
        doc = {
            "tasks": {
                "task_a": {"schedule": "15 21 * * *"},
                "task_b": {"enabled": False},
                "task_core": {"enabled": False},
            },
            "updated_at": updated,
        }
        detail = self._detail(tools, doc, applied={"task_a": "0 22 * * *"})

        task_a, task_b = detail["tasks"]
        assert task_a["effective"] == {"schedule": "15 21 * * *", "enabled": True}
        assert task_a["applied_schedule"] == "0 22 * * *"
        assert task_a["in_sync"] is False
        assert task_b["effective"] == {"schedule": "30 3 * * *", "enabled": False}
        assert task_b["applied_schedule"] is None
        assert task_b["in_sync"] is True
        assert [task["name"] for task in detail["tasks"]] == ["task_a", "task_b"]
        assert detail["updated_at"] == updated.isoformat()

    def test_invalid_task_overrides_are_listed(self, registered, tools) -> None:
        """型の不正・不正な crontab の上書きは `invalid` に挙げ、デフォルトを使う。"""
        doc = {"tasks": {"task_a": {"schedule": "not a cron", "enabled": "no"}}}

        task_a = self._detail(tools, doc)["tasks"][0]

        assert task_a["invalid"] == ["schedule", "enabled"]
        assert task_a["override"] == {}
        assert task_a["effective"] == {"schedule": "0 22 * * *", "enabled": True}

    def test_without_scheduler_is_always_in_sync(self, registered, tools) -> None:
        """スケジューラが動いていなければ、ずれは示さない（起動時に効く）。"""
        detail = self._detail(tools, {"tasks": {"task_a": {"schedule": "1 1 * * *"}}}, running=False)

        assert detail["tasks"][0]["in_sync"] is True

    def test_extension_without_prompt(self, registered, tools) -> None:
        """プロンプトを持たない拡張はプロンプトの節を持たない。"""
        detail = self._detail(tools, None, name="habits")

        assert detail["prompt"] is None
        assert [task["name"] for task in detail["tasks"]] == ["task_other"]


class TestParseUpdate:
    """保存リクエストの検証。"""

    def test_prompt_set_and_unset(self, registered, tools) -> None:
        """文字列は保存（空文字も有効）、null は上書きを消す。"""
        update = svc.parse_update("diary", {"prompt": ""}, tools)
        assert update.set_fields == {"prompt": ""}
        assert update.unset_fields == []
        assert update.task_names == []

        update = svc.parse_update("diary", {"prompt": None}, tools)
        assert update.set_fields == {}
        assert update.unset_fields == ["prompt"]

    def test_tasks_paths(self, registered, tools) -> None:
        """タスクの項目はドット区切りのパスにし、crontab の空白を詰める。"""
        update = svc.parse_update(
            "diary",
            {"tasks": {
                "task_a": {"schedule": "  15   21 * * * ", "enabled": None},
                "task_b": {"enabled": False, "schedule": None},
            }},
            tools,
        )

        assert update.set_fields == {
            "tasks.task_a.schedule": "15 21 * * *",
            "tasks.task_b.enabled": False,
        }
        assert sorted(update.unset_fields) == ["tasks.task_a.enabled", "tasks.task_b.schedule"]
        assert update.task_names == ["task_a", "task_b"]

    @pytest.mark.parametrize(
        "body",
        [
            [],
            "text",
            {},
            {"settings": {}},
            {"prompt": 1},
            {"tasks": []},
            {"tasks": {"task_core": {"enabled": False}}},
            {"tasks": {"task_other": {"enabled": False}}},
            {"tasks": {"task_manual": {"enabled": False}}},
            {"tasks": {"task_missing": {"enabled": False}}},
            {"tasks": {"task_a": "0 1 * * *"}},
            {"tasks": {"task_a": {"time": "0 1 * * *"}}},
            {"tasks": {"task_a": {"schedule": "61 25 * * *"}}},
            {"tasks": {"task_a": {"schedule": ""}}},
            {"tasks": {"task_a": {"schedule": 5}}},
            {"tasks": {"task_a": {"enabled": "false"}}},
            {"tasks": {"task_a": {}}},
            {"prompt": "ok", "tasks": {"task_a": {"schedule": "bad"}}},
        ],
    )
    def test_rejects_invalid_requests(self, registered, tools, body) -> None:
        """形・型・未知のキー・対象外のタスク・不正な crontab・空の更新は全体を拒否する。"""
        with pytest.raises(OverrideValidationError):
            svc.parse_update("diary", body, tools)

    def test_prompt_rejected_for_extension_without_default_prompt(self, registered, tools) -> None:
        """デフォルトのプロンプトを持たない拡張のプロンプトは編集できない。"""
        with pytest.raises(OverrideValidationError, match="no default prompt"):
            svc.parse_update("habits", {"prompt": "x"}, tools)
