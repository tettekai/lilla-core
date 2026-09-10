"""`!runtask <ツール名>` コマンド。

trigger が task のツールを手動で実行する。`run_task` は関数として切り出してあり、
拡張側で HTTP 経由の手動実行エンドポイント等を用意する場合にもそのまま呼び出せる。
"""
from __future__ import annotations

import logging

from lilla_core.commands.registry import register_command
from lilla_core.core.error_notify import notify_error
from lilla_core.ui.messages import t
from lilla_core.utils.datetime_utils import local_now

logger = logging.getLogger(__name__)


async def run_task(tool_name: str, tools: dict, bot, params: dict | None = None) -> None:
    """ツールを実行する（Discord・HTTP共通）。完了通知は行わない。

    LLM ツール一覧（`llm_tools`）は呼び出し元から受け取らず、キャッシュ付きの
    `get_llm_tools()` からこの関数の中で直接取得して `tool.execute()` に渡す。
    これにより、`!runtask` と拡張側が用意する HTTP 経由の手動実行エンドポイント等の
    どちらの経路でも、APScheduler 経由の定期実行と同じ LLM ツールが使える。

    Args:
        tool_name: 実行するツール名（YAML ファイル名 stem）。
        tools: ツールレジストリ。
        bot: Discord クライアント。
        params: ツールに渡す追加パラメータ（拡張側の手動実行エンドポイント経由で指定可能）。
    """
    if tool_name not in tools:
        logger.warning("[RUNTASK] Tool '%s' not found", tool_name)
        return

    tool_info = tools[tool_name]
    tool = tool_info["instance"]

    if tool_info["trigger"] != "task":
        logger.warning("[RUNTASK] Tool '%s' does not support manual execution", tool_name)
        return

    # loaders.llm_tool_loader は import 時に設定を読み込むため、
    # コマンドモジュールの import を軽く保つ目的で関数内 import にしている。
    from lilla_core.loaders.llm_tool_loader import get_llm_tools

    now = local_now()
    try:
        await tool.execute({
            "discord_client": bot,
            "now": now,
            "llm_tools": get_llm_tools(),
            "params": params or {},
        })
    except Exception as e:
        await notify_error(bot, t("command.runtask.execution_error_title", tool=tool_name), e)


@register_command("runtask")
async def handle_runtask(message, arg: str, tools: dict, bot) -> None:
    """`!runtask <ツール名>` の処理。

    エラーは元のチャンネルへ返信せず、ERROR ログとエラー通知チャンネルにのみ出力する。

    Args:
        message: コマンドを送信した Discord メッセージ（未使用）。
        arg: 実行するツール名。
        tools: ツールレジストリ。
        bot: Discord クライアント。エラー通知にも使用する。
    """
    tool_name = arg.strip()

    if not tool_name:
        await notify_error(
            bot,
            t("command.runtask.invalid_request_title"),
            t("command.runtask.tool_name_required"),
        )
        return

    if tool_name not in tools:
        await notify_error(
            bot,
            t("command.runtask.invalid_request_title"),
            t("command.runtask.tool_not_found", tool=tool_name),
        )
        return

    if tools[tool_name]["trigger"] != "task":
        await notify_error(
            bot,
            t("command.runtask.invalid_request_title"),
            t("command.runtask.tool_not_manual", tool=tool_name),
        )
        return

    try:
        await run_task(tool_name, tools, bot)
    except Exception as e:
        await notify_error(bot, t("command.runtask.tool_error_title"), e)
