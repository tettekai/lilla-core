"""APScheduler を使用した タスクジョブスケジューラ。"""

import asyncio
import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from lilla_core.core.error_notify import notify_error
from lilla_core.ui.messages import t
from lilla_core.utils.datetime_utils import local_now

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def start_scheduler(tools: dict, bot, llm_tools: dict = None) -> None:
    """スケジューラを初期化し、スケジュール設定済みタスクをジョブとして登録する。

    on_ready が複数回呼ばれても重複登録しないようガードする。
    BackgroundScheduler を使用することで、Discord のイベントループと独立した
    スレッドでジョブを実行し、遅延を防ぐ。
    """
    global _scheduler
    if _scheduler is not None:
        return

    _scheduler = BackgroundScheduler(
        timezone='Asia/Tokyo',
        job_defaults={"misfire_grace_time": 3600, "coalesce": True},
    )

    for tool_name, tool_info in tools.items():
        if not tool_info.get("scheduled"):
            continue

        tool = tool_info["instance"]
        schedule = getattr(tool, "schedule", None)
        if not schedule:
            logger.warning("[TASK] %s has no schedule configured. Skipping", tool_name)
            continue

        trigger = CronTrigger.from_crontab(schedule)
        job_func = _make_job_func(tool_name, tool, bot, llm_tools or {})
        _scheduler.add_job(job_func, trigger, id=tool_name)
        logger.info("[TASK] Job registered: %s (schedule=%s)", tool_name, schedule)

    _scheduler.start()
    logger.info("[TASK] Scheduler started")


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
    logger.info("[TASK] Scheduler stopped")


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

    エラーは ERROR ログとエラー通知チャンネルに出力し、外部に伝播させない。
    """
    try:
        await tool.execute({"discord_client": bot, "now": now, "llm_tools": llm_tools})
    except Exception as e:
        await notify_error(bot, t("task.execution_error_title", tool=tool_name), e)
