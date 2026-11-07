"""定期タスクの実効スケジュール（デフォルト + 拡張の上書き）を解決する。

内部の定期スケジューラ（`handlers/task_handler.py`）・ダッシュボードの編集画面
（`services/extension_overrides.py`）・外部スケジューラ向けの `GET /api/tasks`
（`handlers/http_server.py`）が同じ規則で `schedule` / `enabled` を決めるための
共通処理。APScheduler のジョブ登録には触れないため、スケジューラが動いていなくても使える。

規則:

- 対象はロード済みのツールのうち `schedule` を持つ定期タスク。ツール YAML で
  `enabled: false` にしたものはロードされないため対象にならない
- 上書き（`extension_overrides` の `tasks.<ツール名>`）は、そのタスクを同梱した拡張の
  ドキュメントのものだけを使う。拡張に属さないタスクには上書きを適用しない
- 型の不正な上書き項目は無視してデフォルトを使う。crontab として解釈できない
  `schedule` の上書きもデフォルトへ戻す。デフォルトも解釈できなければ実効スケジュールは無い
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from apscheduler.triggers.cron import CronTrigger

from lilla_core.utils.datetime_utils import local_now, local_timezone

logger = logging.getLogger(__name__)

#: `GET /api/tasks` が返す crontab の方言。APScheduler 3 の `CronTrigger.from_crontab()`
#: の解釈（曜日の 0 は月曜、日と曜日は AND）であることを外部へ伝える。
CRON_FORMAT = "apscheduler3"


class TaskScheduleError(Exception):
    """実効スケジュールを解決できないタスクがあるとき（`task_name` に該当のツール名）。"""

    def __init__(self, task_name: str) -> None:
        super().__init__(f"Task '{task_name}' has no valid schedule")
        self.task_name = task_name


@dataclass(frozen=True)
class TaskOverride:
    """1 タスク分の上書きのうち、型が正しく書かれていた項目。

    Attributes:
        schedule: `schedule` の上書き（文字列）。crontab としての正しさは未確認。
        enabled: `enabled` の上書き。
        invalid: 型が不正で無視した項目名。
    """

    schedule: str | None = None
    enabled: bool | None = None
    invalid: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedTask:
    """デフォルトに上書きを重ねた、1 タスクの実効値。

    Attributes:
        name: ツール名（`/api/runtask` に渡す名前）。
        extension: タスクを同梱した拡張名。拡張に属さなければ `None`。
        default_schedule: ツールが持つデフォルトの crontab。
        override: 型の正しい上書き項目。
        schedule: 実効の crontab。上書きもデフォルトも解釈できなければ `None`。
        enabled: 実効の「定期実行する」。上書きが無ければ `True`。
        schedule_override_rejected: `schedule` の上書きが crontab として不正で、
            デフォルトへ戻したか。
    """

    name: str
    extension: str | None
    default_schedule: str
    override: TaskOverride
    schedule: str | None
    enabled: bool
    schedule_override_rejected: bool = False


def scheduled_task_entries(tools: dict | None, extension: str | None = None) -> dict[str, dict]:
    """ロード済みの定期タスクを「ツール名 -> ツールのエントリ」でロード順に返す。

    Args:
        tools: `load_all_tools()` が返したツールのレジストリ。
        extension: 指定したら、その拡張が同梱したタスクだけに絞る。
    """
    result: dict[str, dict] = {}
    for tool_name, info in (tools or {}).items():
        if not info.get("scheduled"):
            continue
        if extension is not None and info.get("extension") != extension:
            continue
        if not getattr(info.get("instance"), "schedule", None):
            continue
        result[tool_name] = info
    return result


def build_cron_trigger(schedule: str) -> CronTrigger:
    """crontab 式から、`local_timezone()` のタイムゾーンの `CronTrigger` を作る。

    Raises:
        Exception: crontab として解釈できない場合（APScheduler の例外をそのまま伝える）。
    """
    return CronTrigger.from_crontab(schedule, timezone=local_timezone())


def is_valid_crontab(schedule: str) -> bool:
    """crontab 式として解釈できるかを、スケジューラと同じ規則で確かめる。"""
    try:
        build_cron_trigger(schedule)
    except Exception:
        return False
    return True


def parse_task_overrides(
    extension_name: str,
    doc: dict | None,
    known: set[str],
    *,
    only: str | None = None,
    warn: bool = True,
) -> dict[str, TaskOverride]:
    """上書きドキュメントの `tasks` の型を検証し、ツール名 -> `TaskOverride` を返す。

    crontab としての正しさは見ない（`resolve_task()` が確かめる）。`tasks` が
    オブジェクトでない・拡張が同梱していないツール名・オブジェクトでないエントリは
    無視する。`warn` が真なら、無視した理由を拡張名とツール名つきの WARNING に出す。

    Args:
        extension_name: 上書きの持ち主の拡張名。
        doc: その拡張の上書きドキュメント。無ければ `None`。
        known: その拡張が同梱した定期タスクのツール名。
        only: 指定したらこのツール名だけを見る（他のキーは警告も出さない）。
        warn: 無視した項目を WARNING に出すか。
    """

    def _warn(message: str, *args) -> None:
        if warn:
            logger.warning(message, *args)

    if not doc or "tasks" not in doc:
        return {}
    tasks = doc["tasks"]
    if not isinstance(tasks, dict):
        _warn(
            "[TASK] Ignoring task overrides of extension '%s': 'tasks' must be an object, got %s",
            extension_name, type(tasks).__name__,
        )
        return {}

    result: dict[str, TaskOverride] = {}
    for tool_name, value in tasks.items():
        if only is not None and tool_name != only:
            continue
        if tool_name not in known:
            _warn(
                "[TASK] Ignoring task override '%s' of extension '%s': "
                "not a scheduled task bundled by the extension",
                tool_name, extension_name,
            )
            continue
        if not isinstance(value, dict):
            _warn(
                "[TASK] Ignoring task override '%s' of extension '%s': "
                "expected an object, got %s",
                tool_name, extension_name, type(value).__name__,
            )
            continue
        schedule = None
        enabled = None
        invalid: list[str] = []
        if "schedule" in value:
            if isinstance(value["schedule"], str):
                schedule = value["schedule"]
            else:
                invalid.append("schedule")
                _warn(
                    "[TASK] Ignoring schedule override of task '%s' of extension '%s': "
                    "expected a string, got %s",
                    tool_name, extension_name, type(value["schedule"]).__name__,
                )
        if "enabled" in value:
            if isinstance(value["enabled"], bool):
                enabled = value["enabled"]
            else:
                invalid.append("enabled")
                _warn(
                    "[TASK] Ignoring enabled override of task '%s' of extension '%s': "
                    "expected a boolean, got %s",
                    tool_name, extension_name, type(value["enabled"]).__name__,
                )
        result[tool_name] = TaskOverride(schedule=schedule, enabled=enabled, invalid=tuple(invalid))
    return result


def resolve_task(
    name: str,
    extension: str | None,
    default_schedule: str,
    override: TaskOverride | None = None,
) -> ResolvedTask:
    """デフォルトに上書きを重ね、1 タスクの実効値を決める。

    `schedule` の上書きが crontab として不正ならデフォルトへ戻し、デフォルトも
    不正なら実効の `schedule` は `None` になる（`enabled` には影響しない）。

    Args:
        name: ツール名。
        extension: タスクを同梱した拡張名。
        default_schedule: ツールが持つデフォルトの crontab。
        override: `parse_task_overrides()` が返したそのタスクの上書き。
    """
    override = override or TaskOverride()
    rejected = False
    schedule: str | None = None
    if override.schedule is not None:
        if is_valid_crontab(override.schedule):
            schedule = override.schedule
        else:
            rejected = True
    if schedule is None and is_valid_crontab(default_schedule):
        schedule = default_schedule
    return ResolvedTask(
        name=name,
        extension=extension,
        default_schedule=default_schedule,
        override=override,
        schedule=schedule,
        enabled=override.enabled if override.enabled is not None else True,
        schedule_override_rejected=rejected,
    )


async def load_task_schedules(tools: dict | None) -> list[ResolvedTask]:
    """ロード済みの全定期タスクの実効値を、上書きの最新値から解決して返す。

    上書きは呼ぶたびに MongoDB から読み、キャッシュしない。読み込みの失敗は
    デフォルトで隠さず、そのまま例外として伝える（成功した一覧と取り違えさせないため）。

    Args:
        tools: ツールのレジストリ。

    Returns:
        ロード順の `ResolvedTask` のリスト（`enabled=False` のタスクも含む）。

    Raises:
        TaskScheduleError: 実効スケジュールを解決できないタスクがあるとき。
        Exception: 上書きの読み込みに失敗したとき（リポジトリの例外をそのまま伝える）。
    """
    from lilla_core.repository.extension_override_repository import get_extension_override_repo

    entries = scheduled_task_entries(tools)
    extension_names = list(
        dict.fromkeys(info["extension"] for info in entries.values() if info.get("extension"))
    )
    docs = await get_extension_override_repo().find_by_extensions(extension_names)

    overrides: dict[str, dict[str, TaskOverride]] = {}
    for ext_name in extension_names:
        known = set(scheduled_task_entries(tools, ext_name))
        overrides[ext_name] = parse_task_overrides(ext_name, docs.get(ext_name), known)

    result: list[ResolvedTask] = []
    for tool_name, info in entries.items():
        extension = info.get("extension")
        override = overrides.get(extension, {}).get(tool_name) if extension else None
        resolved = resolve_task(tool_name, extension, info["instance"].schedule, override)
        if resolved.schedule is None:
            raise TaskScheduleError(tool_name)
        result.append(resolved)
    return result


def timezone_label() -> str:
    """crontab を解釈するタイムゾーンを、外部のスケジューラが扱える文字列で返す。

    `ui.timezone` を書いていればその IANA 名（`Asia/Tokyo` など）。未指定なら
    OS のローカルタイムゾーンで、IANA 名を持たないため、いまの UTC オフセットを
    `+09:00` の形で返す（内部のスケジューラも同じ固定オフセットで解釈している）。
    """
    from lilla_core.core.config import get_config

    name = get_config().ui.timezone
    if name:
        return name
    offset = local_now().utcoffset()
    total_minutes = int(offset.total_seconds() // 60) if offset is not None else 0
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    return f"{sign}{hours:02d}:{minutes:02d}"
