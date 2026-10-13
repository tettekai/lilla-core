"""`!skill <スキル名> [追加指示]` コマンド。

`skills` コレクションに保存した名前付きのプロンプト（スキル）を、そのターンの最新の
user メッセージとして（ユーザーの発言ではなくシステムからの動作指示と分かる形式で）
展開し、通常の会話と同じ流れ（`handlers/message_handler.py` の
`start_conversation`）で LLM へ渡す。展開後の内容は会話履歴にもそのまま残る
（システムプロンプトには入れない）。

スキル名は大文字小文字を区別せず、小文字に正規化して照合する。存在しない・無効な
スキルは LLM を呼ばず、元のチャンネルへ固定文言で返信する（`!model` の未定義名と同じ扱い）。

引数なし（空白のみを含む）の `!skill` は、LLM を呼ばずに有効なスキルの一覧を
名前順で返信する。
"""
from __future__ import annotations

import logging

from lilla_core.commands.registry import register_command
from lilla_core.services.skills import format_skill_instruction, normalize_skill_name
from lilla_core.ui.messages import t

logger = logging.getLogger(__name__)


@register_command("skill")
async def handle_skill(message, arg: str, tools: dict, bot) -> None:
    """`!skill <スキル名> [追加指示]` の処理。

    スキル本文は `services/skills.py` の `format_skill_instruction` で「システムからの
    動作指示」のラッパーに囲み、スキル名の後ろ（最初の空白以降）は追加指示として
    ラベル付きでその後ろに続ける。

    Args:
        message: コマンドを送信した Discord メッセージ（承認済みコマンドの代理オブジェクトでもよい）。
            エラーの返信と会話の返信先に使う。
        arg: スキル名と追加指示（無い場合は空文字列）。
        tools: ツールレジストリ（未使用）。
        bot: Discord クライアント。会話処理のエラー通知に使う。
    """
    parts = arg.strip().split(maxsplit=1)
    if not parts:
        await _reply_skill_list(message)
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
    content = format_skill_instruction(name, skill.get("prompt", ""), extra)
    start_conversation(message, bot, get_llm_tools(), content)


def format_skill_list_line(skill: dict) -> str:
    """一覧の 1 行（`- `名前`: 説明`。説明が空なら名前だけ）を組み立てる。

    説明は 1 行に収めるため、改行を空白に置き換える。
    """
    name = skill.get("name", "")
    description = " ".join((skill.get("description") or "").splitlines()).strip()
    if description:
        return f"- `{name}`: {description}"
    return f"- `{name}`"


async def _reply_skill_list(message) -> None:
    """有効なスキルの一覧を元のチャンネルへ返信する（LLM は呼ばない）。

    1 通目は `message.reply`、上限を超えて分割した 2 通目以降は `message.channel.send`
    で同じチャンネルへ続けて送る。

    Args:
        message: コマンドを送信した Discord メッセージ（承認済みコマンドの代理オブジェクトでもよい）。
    """
    from lilla_core.commands.discord_util import chunk_lines
    from lilla_core.repository.skill_repository import get_skill_repo

    skills = await get_skill_repo().get_enabled()
    if not skills:
        await message.reply(t("command.skill.list_empty"))
        return

    lines = [t("command.skill.list_header")]
    lines.extend(format_skill_list_line(skill) for skill in skills)
    lines.append(t("command.skill.list_footer"))
    chunks = chunk_lines("\n".join(lines))
    await message.reply(chunks[0])
    for chunk in chunks[1:]:
        await message.channel.send(chunk)
