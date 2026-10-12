"""`!skill <スキル名> [追加指示]` コマンド。

`skills` コレクションに保存した名前付きのプロンプト（スキル）を、そのターンの最新の
user メッセージとして展開し、通常の会話と同じ流れ（`handlers/message_handler.py` の
`start_conversation`）で LLM へ渡す。展開後の内容は会話履歴にもそのまま残る
（システムプロンプトには入れない）。

スキル名は大文字小文字を区別せず、小文字に正規化して照合する。存在しない・無効な
スキルは LLM を呼ばず、元のチャンネルへ固定文言で返信する（`!model` の未定義名と同じ扱い）。
"""
from __future__ import annotations

import logging

from lilla_core.commands.registry import register_command
from lilla_core.services.message_util import format_skill_block
from lilla_core.services.skills import normalize_skill_name
from lilla_core.ui.messages import t

logger = logging.getLogger(__name__)


@register_command("skill")
async def handle_skill(message, arg: str, tools: dict, bot) -> None:
    """`!skill <スキル名> [追加指示]` の処理。

    スキル名の後ろ（最初の空白以降）は追加指示として、展開したスキルブロックの後ろに
    そのまま続ける。

    Args:
        message: コマンドを送信した Discord メッセージ（承認済みコマンドの代理オブジェクトでもよい）。
            エラーの返信と会話の返信先に使う。
        arg: スキル名と追加指示（無い場合は空文字列）。
        tools: ツールレジストリ（未使用）。
        bot: Discord クライアント。会話処理のエラー通知に使う。
    """
    parts = arg.strip().split(maxsplit=1)
    if not parts:
        await message.reply(t("command.skill.usage"))
        return
    raw_name = parts[0]
    extra = parts[1] if len(parts) > 1 else ""

    name = normalize_skill_name(raw_name)
    if name is None:
        await message.reply(t("command.skill.not_found", name=raw_name))
        return

    # 以下はいずれも import 時に設定を読み込むため、コマンドモジュールの import を
    # 軽く保つ目的で関数内 import にしている。
    from lilla_core.handlers.message_handler import start_conversation
    from lilla_core.loaders.llm_tool_loader import get_llm_tools
    from lilla_core.repository.skill_repository import get_skill_repo

    skill = await get_skill_repo().find_by_name(name)
    if skill is None:
        await message.reply(t("command.skill.not_found", name=name))
        return
    if not skill.get("enabled", True):
        await message.reply(t("command.skill.disabled", name=name))
        return

    logger.info("[SKILL] Expanding skill '%s'", name)
    content = format_skill_block(name, skill.get("prompt", ""), extra)
    start_conversation(message, bot, get_llm_tools(), content)
