"""`!disable_tools` コマンド。

通常会話（Discord および拡張が増やす対話クライアント）で LLM ツールを一時的にすべて無効化する。
状態はプロセス内メモリのみで保持するため、再起動すると有効に戻る。
定期タスクや !runtask には影響しない。
"""
from __future__ import annotations

import logging

from lilla_core.commands.registry import register_command
from lilla_core.core.runtime_state import set_tools_disabled
from lilla_core.ui.messages import t

logger = logging.getLogger(__name__)


@register_command("disable_tools")
async def handle_disable_tools(message, arg: str, tools: dict, bot) -> None:
    """通常会話のツールを無効化する。"""
    set_tools_disabled(True)
    logger.info("[TOOLS] Disabled tools for normal conversation")
    await message.reply(t("command.disable_tools.done"))
