"""APScheduler を使用した タスクジョブスケジューラ。"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from lilla_core.core.error_notify import notify_error
from lilla_core.core.extension import build_tool_context, get_extensions
from lilla_core.core.llm_call_origin import llm_call_origin
from lilla_core.repository.extension_override_repository import get_extension_override_repo
from lilla_core.ui.messages import t
from lilla_core.utils.datetime_utils import local_now, local_timezone

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


@dataclass(frozen=True)
class _TaskJob:
    """スケジューラへ登録しうるタスク 1 つの、上書きを重ねる前のデフォルト。

    `start_scheduler()` の時点で作って保持し、上書きの反映でジョブを登録し直すときに使う。
    """

    tool_name: str
    extension: str | None
    default_schedule: str
    job_func: Callable[[], None]


#: ツール名 -> デフォルト。`start_scheduler()` が作り、`stop_scheduler()` が捨てる。
_task_jobs: dict[str, _TaskJob] = {}
#: 登録中のジョブのツール名 -> 適用中の crontab。ここに無いツールはジョブを登録していない。
_applied_schedules: dict[str, str] = {}


async def start_scheduler(tools: dict, bot, llm_tools: dict = None) -> None:
    """スケジューラを初期化し、スケジュール設定済みタスクをジョブとして登録する。

    on_ready が複数回呼ばれても重複登録しないようガードする（上書きを読む await の
    前にスケジューラを作っておくため、読み込み中に再び呼ばれても二重には作らない）。
    BackgroundScheduler を使用することで、Discord のイベントループと独立した
    スレッドでジョブを実行し、遅延を防ぐ。

    拡張が同梱したタスクには `extension_overrides` の `tasks` の上書きを重ねてから
    登録する。登録は `apply_task_overrides()` と同じ適用処理（`_sync_job`）を通る。

    crontab 式は `local_timezone()` が解決したタイムゾーン（`ui.timezone`、
    未指定なら OS のローカル）で解釈する。`CronTrigger` はインスタンスとして
    渡すとスケジューラの timezone を引き継がないため、スケジューラと同じ
    タイムゾーンを明示的に渡す。
    """
    global _scheduler
    if _scheduler is not None:
        return

    scheduler = BackgroundScheduler(
        timezone=local_timezone(),
        job_defaults={"misfire_grace_time": 3600, "coalesce": True},
    )
    _scheduler = scheduler
    _task_jobs.clear()
    _applied_schedules.clear()

    for tool_name, tool_info in tools.items():
        if not tool_info.get("scheduled"):
            continue

        tool = tool_info["instance"]
        schedule = getattr(tool, "schedule", None)
        if not schedule:
            logger.warning("[TASK] %s has no schedule configured. Skipping", tool_name)
            continue

        _task_jobs[tool_name] = _TaskJob(
            tool_name=tool_name,
            extension=tool_info.get("extension"),
            default_schedule=schedule,
            job_func=_make_job_func(tool_name, tool, bot, llm_tools or {}),
        )

    extension_names = [ext.name for ext in get_extensions()]
    overrides = await _load_overrides(extension_names)
    if _scheduler is not scheduler:
        # 読み込みの間に stop_scheduler() された
        return

    resolved: dict[str, dict] = {}
    for name in extension_names:
        resolved.update(_resolve_task_overrides(name, overrides.get(name), _extension_jobs(name)))
    for job in _task_jobs.values():
        _sync_job(job, resolved.get(job.tool_name, {}))

    scheduler.start()
    logger.info("[TASK] Scheduler started")
    for tool_name in _applied_schedules:
        _log_next_run(tool_name)


async def apply_task_overrides(extension_name: str, task_name: str | None = None) -> None:
    """1 つの拡張（または、その中の 1 タスク）の上書きを読み直し、稼働中のスケジューラへ反映する。

    `extension_overrides` のその拡張のドキュメントを読み、`tasks` の上書きを重ねた
    `schedule` / `enabled` に合わせてジョブを差し替える。スケジュールが変われば
    `reschedule_job`、`enabled: false` なら `remove_job`、ジョブが無いのに有効なら
    デフォルトのジョブ関数で `add_job` する。上書きを消して呼べばデフォルトに戻る。
    実行中のジョブは止めない（差し替えは次回以降の実行に効く）。

    再起動なしで反映するための入口で、上書きを書き込んだ側（ダッシュボードなど）が
    呼ぶ想定。変更の監視はしない。上書きはキャッシュせず、呼ぶたびに MongoDB を読む。
    読み込みに失敗したときは WARNING を出し、その拡張のタスクをデフォルトへ戻す
    （起動時と同じ扱い）。

    Args:
        extension_name: 対象の拡張の `name`。
        task_name: 対象をこのタスク（ツール名）だけに絞る。`None` ならその拡張の全タスク。
    """
    if _scheduler is None:
        logger.warning(
            "[TASK] Scheduler is not running; ignoring task overrides of extension '%s'",
            extension_name,
        )
        return
    scheduler = _scheduler
    jobs = _extension_jobs(extension_name)
    if task_name is not None:
        jobs = [job for job in jobs if job.tool_name == task_name]
        if not jobs:
            logger.warning(
                "[TASK] Ignoring task override reload: '%s' is not a scheduled task "
                "bundled by extension '%s'",
                task_name, extension_name,
            )
            return

    overrides = await _load_overrides([extension_name])
    if _scheduler is not scheduler:
        return
    resolved = _resolve_task_overrides(
        extension_name, overrides.get(extension_name), jobs, only=task_name
    )
    for job in jobs:
        _sync_job(job, resolved.get(job.tool_name, {}))


def stop_scheduler() -> None:
    """スケジューラを停止する。

    未起動（`_scheduler is None`）の場合は何もしない。停止後は次回起動時に
    `start_scheduler` が新しいインスタンスを作れるよう、モジュール変数も None に戻す。
    `wait=False` で呼び出すため、実行中のジョブの完了を待たずに即座に戻る
    （llm_call_guard のシャットダウンシーケンスのように、これ以上新しいジョブを
    走らせたくない場面での使用を想定）。
    """
    global _scheduler
    if _scheduler is None:
        return
    _scheduler.shutdown(wait=False)
    _scheduler = None
    _task_jobs.clear()
    _applied_schedules.clear()
    logger.info("[TASK] Scheduler stopped")


def _extension_jobs(extension_name: str) -> list[_TaskJob]:
    """その拡張が同梱したタスクのデフォルトを、登録順に返す。"""
    return [job for job in _task_jobs.values() if job.extension == extension_name]


async def _load_overrides(extension_names: list[str]) -> dict[str, dict]:
    """拡張の上書きドキュメントを「拡張名 -> ドキュメント」で読む。

    読み込みに失敗したときは WARNING を出して空（＝全タスクをデフォルト）を返し、
    起動も反映も止めない。`extension_names` が空なら DB を読まない。
    """
    if not extension_names:
        return {}
    try:
        return await get_extension_override_repo().find_by_extensions(extension_names)
    except Exception as e:
        logger.warning(
            "[TASK] Failed to load extension overrides for %s; using default task schedules: %s",
            ", ".join(extension_names), e,
        )
        return {}


def _resolve_task_overrides(
    extension_name: str,
    doc: dict | None,
    jobs: list[_TaskJob],
    only: str | None = None,
) -> dict[str, dict]:
    """上書きドキュメントの `tasks` を検証し、ツール名 -> 有効な上書き項目を返す。

    返す値は `schedule`（文字列）と `enabled`（真偽値）のうち、型が正しく書かれていた
    項目だけを持つ dict。crontab としての正しさは `_sync_job` がトリガーを作るときに
    確かめる。不正な値・拡張が同梱していないツール名は、拡張名とツール名を含む
    WARNING を出して無視する（その項目はデフォルトになる）。

    Args:
        extension_name: 上書きの持ち主の拡張名。
        doc: その拡張の上書きドキュメント。無ければ `None`。
        jobs: その拡張が同梱したタスクのデフォルト。
        only: 指定したらこのツール名だけを見る（他のキーは警告も出さない）。

    Returns:
        ツール名から上書き項目への dict。
    """
    if not doc or "tasks" not in doc:
        return {}
    tasks = doc["tasks"]
    if not isinstance(tasks, dict):
        logger.warning(
            "[TASK] Ignoring task overrides of extension '%s': 'tasks' must be an object, got %s",
            extension_name, type(tasks).__name__,
        )
        return {}

    known = {job.tool_name for job in jobs}
    resolved: dict[str, dict] = {}
    for tool_name, value in tasks.items():
        if only is not None and tool_name != only:
            continue
        if tool_name not in known:
            logger.warning(
                "[TASK] Ignoring task override '%s' of extension '%s': "
                "not a scheduled task bundled by the extension",
                tool_name, extension_name,
            )
            continue
        if not isinstance(value, dict):
            logger.warning(
                "[TASK] Ignoring task override '%s' of extension '%s': "
                "expected an object, got %s",
                tool_name, extension_name, type(value).__name__,
            )
            continue
        entry: dict = {}
        if "schedule" in value:
            schedule = value["schedule"]
            if isinstance(schedule, str):
                entry["schedule"] = schedule
            else:
                logger.warning(
                    "[TASK] Ignoring schedule override of task '%s' of extension '%s': "
                    "expected a string, got %s",
                    tool_name, extension_name, type(schedule).__name__,
                )
        if "enabled" in value:
            enabled = value["enabled"]
            if isinstance(enabled, bool):
                entry["enabled"] = enabled
            else:
                logger.warning(
                    "[TASK] Ignoring enabled override of task '%s' of extension '%s': "
                    "expected a boolean, got %s",
                    tool_name, extension_name, type(enabled).__name__,
                )
        resolved[tool_name] = entry
    return resolved


def _build_trigger(schedule: str) -> CronTrigger:
    """crontab 式から、スケジューラと同じタイムゾーンの `CronTrigger` を作る。

    Raises:
        Exception: crontab として解釈できない場合（APScheduler の例外をそのまま伝える）。
    """
    return CronTrigger.from_crontab(schedule, timezone=local_timezone())


def _sync_job(job: _TaskJob, override: dict) -> None:
    """上書きを重ねた設定に合わせて、1 タスクのジョブを登録・差し替え・削除する。

    起動時の登録と `apply_task_overrides()` の反映が共有する唯一の適用処理。
    上書きの `schedule` が crontab として不正なら WARNING を出してデフォルトを使い、
    デフォルトも不正ならジョブを登録しない（登録済みなら外す）。

    Args:
        job: タスクのデフォルト。
        override: `_resolve_task_overrides()` が返した、そのタスクの上書き項目。
    """
    tool_name = job.tool_name
    if override.get("enabled", True) is False:
        if tool_name in _applied_schedules:
            _remove_job(tool_name)
        logger.info("[TASK] Job disabled by override: %s (extension=%s)", tool_name, job.extension)
        return

    schedule = override.get("schedule", job.default_schedule)
    try:
        trigger = _build_trigger(schedule)
    except Exception as e:
        if schedule == job.default_schedule:
            logger.warning("[TASK] %s has invalid cron %r. Skipping: %s", tool_name, schedule, e)
            if tool_name in _applied_schedules:
                _remove_job(tool_name)
            return
        logger.warning(
            "[TASK] Ignoring schedule override of task '%s' of extension '%s': "
            "invalid cron %r (%s). Using default %r",
            tool_name, job.extension, schedule, e, job.default_schedule,
        )
        _sync_job(job, {key: value for key, value in override.items() if key != "schedule"})
        return

    if tool_name not in _applied_schedules:
        _scheduler.add_job(job.job_func, trigger, id=tool_name)
        logger.info("[TASK] Job registered: %s (schedule=%s)", tool_name, schedule)
    elif _applied_schedules[tool_name] != schedule:
        try:
            _scheduler.reschedule_job(tool_name, trigger=trigger)
        except JobLookupError:
            # トリガーが終わってスケジューラ側で消えていた場合は登録し直す
            _scheduler.add_job(job.job_func, trigger, id=tool_name)
        logger.info("[TASK] Job rescheduled: %s (schedule=%s)", tool_name, schedule)
    else:
        return
    _applied_schedules[tool_name] = schedule
    if _scheduler.running:
        _log_next_run(tool_name)


def _remove_job(tool_name: str) -> None:
    """登録中のジョブを外す（スケジューラ側で既に消えていても構わない）。"""
    try:
        _scheduler.remove_job(tool_name)
    except JobLookupError:
        pass
    _applied_schedules.pop(tool_name, None)
    logger.info("[TASK] Job removed: %s", tool_name)


def _log_next_run(tool_name: str) -> None:
    """ジョブの次回実行時刻を INFO ログに出す（上書きが効いたかを確かめられるようにする）。"""
    job = _scheduler.get_job(tool_name)
    if job is not None:
        logger.info("[TASK] Next run of %s: %s", tool_name, getattr(job, "next_run_time", None))


def _make_job_func(tool_name: str, tool, bot, llm_tools: dict):
    """ツール実行用の関数を生成する。

    BackgroundScheduler から呼び出される同期関数を返す。
    ジョブは asyncio.run_coroutine_threadsafe で Discord の bot.loop に投げる。
    """

    def _job():
        """APScheduler から呼び出されるジョブ関数。"""
        now = local_now()
        asyncio.run_coroutine_threadsafe(
            _run_tool(tool_name, tool, bot, now, llm_tools),
            bot.loop,
        )

    return _job


async def _run_tool(tool_name: str, tool, bot, now: datetime, llm_tools: dict) -> None:
    """ツールを非同期で実行する。

    実行 context は、拡張の `tool_context_providers()` を評価した `build_tool_context()`
    の結果に、コア確定の `discord_client` / `now` / `llm_tools` を重ねたもの
    （LLM ツールと同じ注入モデル）。エラーは ERROR ログとエラー通知チャンネルに
    出力し、外部に伝播させない。
    """
    try:
        context = build_tool_context()
        context.update({"discord_client": bot, "now": now, "llm_tools": llm_tools})
        # タスク内の LLM 呼び出しを使用量の記録でこのタスクに数えるため、入口を積む
        with llm_call_origin(task_name=tool_name):
            await tool.execute(context)
    except Exception as e:
        await notify_error(bot, t("task.execution_error_title", tool=tool_name), e)
