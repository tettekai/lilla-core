"""`!enable_tools` コマンド。

`!disable_tools` で無効化した通常会話のツールを再び有効に戻す。
"""
from __future__ import annotations

import logging

from lilla_core.commands.registry import register_command
from lilla_core.core.runtime_state import set_tools_disabled
from lilla_core.ui.messages import t

logger = logging.getLogger(__name__)


@register_command("enable_tools")
async def handle_enable_tools(message, arg: str, tools: dict, bot) -> None:
    """通常会話のツールを有効に戻す。"""
    set_tools_disabled(False)
    logger.info("[TOOLS] Re-enabled tools for normal conversation")
    await message.reply(t("command.enable_tools.done"))
