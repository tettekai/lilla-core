"""`!selftest [full]` コマンド。

起動後やパッケージ構成の変更後に手動で走らせ、リラの基本機能が壊れていないかを
人間が確認するための自己診断コマンド。生存確認（`GET /`）とは目的が異なり、
失敗してもコンテナを落とす必要はない（このコマンドはコンテナやプロセスに対して
一切作用せず、結果を報告するだけ）。

チェック本体は `services/system_checks.py` に置き、生存確認（`handle_root`）と
共有する（二重実装しない）。

- `!selftest` … プロセス応答・MongoDB 疎通・コマンドレジストリ件数・タスクツール件数
- `!selftest full` … 上記に加えて LLM 疎通確認（`check_llm`）も実行する
  （LLM API の課金が毎回 1 往復分だけ発生する）

他のコマンド（`!mongodata` 等）と異なり `notify_error` のみで済ませず、診断が目的の
ため結果は常に呼び出し元チャンネルへ返す。
"""
from __future__ import annotations

import io
import logging

import discord

from lilla_core.commands.registry import register_command
from lilla_core.services.system_checks import (
    CheckResult,
    check_command_registry,
    check_llm,
    check_mongodb,
    check_process_alive,
    check_task_tools,
)
from lilla_core.ui.messages import t
from lilla_core.utils.datetime_utils import local_now

logger = logging.getLogger(__name__)

#: 詳細結果を添付する際のファイル名。
RESULT_FILENAME = "selftest_result.txt"

#: `full` モード（LLM 疎通確認も実行する）を指定する引数。
FULL_MODE_ARG = "full"


def _build_summary(results: list[CheckResult], full_mode: bool) -> str:
    """Discord 本文に載せる要約（✅ / ❌ の箇条書き）を組み立てる。

    Args:
        results: 各チェックの結果。
        full_mode: `full` モード（LLM 疎通確認あり）で実行したかどうか。

    Returns:
        Discord メッセージ本文用の文字列。
    """
    ok_count = sum(1 for r in results if r.ok)
    mode = (
        t("selftest.mode.full", arg=FULL_MODE_ARG) if full_mode else t("selftest.mode.normal")
    )
    lines = [t("selftest.summary", mode=mode, ok=ok_count, total=len(results))]
    for result in results:
        lines.append(f"{'✅' if result.ok else '❌'} {result.name}")
    return "\n".join(lines)


def _build_detail(results: list[CheckResult]) -> str:
    """添付ファイルに載せる詳細（`elapsed_ms` と `detail`）を組み立てる。

    Args:
        results: 各チェックの結果。

    Returns:
        添付ファイルの中身となる文字列。
    """
    timestamp = local_now().strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = [t("selftest.detail_header", timestamp=timestamp), ""]
    for result in results:
        lines.append(
            f"[{'OK' if result.ok else 'NG'}] {result.name} ({result.elapsed_ms}ms)"
        )
        lines.append(f"    {result.detail}")
    return "\n".join(lines)


@register_command("selftest")
async def handle_selftest(message, arg: str, tools: dict, bot) -> None:
    """`!selftest [full]` の処理。

    各チェックを順に実行し、要約を本文・詳細を添付ファイル（`selftest_result.txt`）
    として呼び出し元チャンネルへ送信する。各チェック関数は内部で例外を捕捉するため、
    1 件の失敗が他のチェックの実行を妨げることはない。

    Args:
        message: コマンドを送信した Discord メッセージ。結果の送信に使用する。
        arg: コマンド名の後ろに続く引数文字列。`full` なら LLM 疎通確認も行う。
        tools: task ツールのレジストリ。件数チェックに使用する。
        bot: Discord クライアント（未使用）。
    """
    full_mode = arg.strip().lower() == FULL_MODE_ARG

    results = [
        await check_process_alive(),
        await check_mongodb(),
        await check_command_registry(),
        await check_task_tools(tools),
    ]
    if full_mode:
        results.append(await check_llm())

    for result in results:
        logger.info(
            "[SELFTEST] %s: %s (%sms) %s",
            result.name,
            "OK" if result.ok else "NG",
            result.elapsed_ms,
            result.detail,
        )

    attachment = discord.File(
        io.BytesIO(_build_detail(results).encode("utf-8")), filename=RESULT_FILENAME
    )
    await message.channel.send(content=_build_summary(results, full_mode), file=attachment)
