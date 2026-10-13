"""`!skill <スキル名> [追加指示]` コマンド。

`skills` コレクションに保存した名前付きのプロンプト（スキル）を、そのターンの最新の
user メッセージとして（ユーザーの発言ではなくシステムからの動作指示と分かる形式で）
展開し、呼び出し元の通常の会話と同じ流れ（`ctx.start_conversation`。Discord では
`handlers/message_handler.py` の `start_conversation`）で LLM へ渡す。展開後の内容は
会話履歴にもそのまま残る（システムプロンプトには入れない）。

スキル名は大文字小文字を区別せず、小文字に正規化して照合する。存在しない・無効な
スキルは LLM を呼ばず、入力ミスとして固定文言を返す（`ctx.reply_error`。`!model` の
未定義名と同じ扱い）。

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
async def handle_skill(ctx, arg: str, tools: dict, bot) -> None:
    """`!skill <スキル名> [追加指示]` の処理。

    スキル本文は `services/skills.py` の `format_skill_instruction` で「システムからの
    動作指示」のラッパーに囲み、スキル名の後ろ（最初の空白以降）は追加指示として
    ラベル付きでその後ろに続ける。

    Args:
        ctx: コマンドコンテキスト。エラーの返信と会話の開始に使う。
        arg: スキル名と追加指示（無い場合は空文字列）。
        tools: ツールレジストリ（未使用）。
        bot: Discord クライアント（未使用）。
    """
    parts = arg.strip().split(maxsplit=1)
    if not parts:
        await _reply_skill_list(ctx)
        return
    raw_name = parts[0]
    extra = parts[1] if len(parts) > 1 else ""

    name = normalize_skill_name(raw_name)
    if name is None:
        await ctx.reply_error(t("command.skill.not_found", name=raw_name))
        return

    # import 時に設定を読み込むため、コマンドモジュールの import を軽く保つ目的で
    # 関数内 import にしている。
    from lilla_core.repository.skill_repository import get_skill_repo

    skill = await get_skill_repo().find_by_name(name)
    if skill is None:
        await ctx.reply_error(t("command.skill.not_found", name=name))
        return
    if not skill.get("enabled", True):
        await ctx.reply_error(t("command.skill.disabled", name=name))
        return

    logger.info("[SKILL] Expanding skill '%s'", name)
    content = format_skill_instruction(name, skill.get("prompt", ""), extra)
    await ctx.start_conversation(content)


def format_skill_list_line(skill: dict) -> str:
    """一覧の 1 行（`- `名前`: 説明`。説明が空なら名前だけ）を組み立てる。

    説明は 1 行に収めるため、改行を空白に置き換える。
    """
    name = skill.get("name", "")
    description = " ".join((skill.get("description") or "").splitlines()).strip()
    if description:
        return f"- `{name}`: {description}"
    return f"- `{name}`"


async def _reply_skill_list(ctx) -> None:
    """有効なスキルの一覧を呼び出し元へ返す（LLM は呼ばない）。

    一覧は 1 つの文言として `ctx.reply` に渡す。Discord の 1 メッセージ上限を超える
    場合の分割（1 通目は返信、2 通目以降は同じチャンネルへ続けて送信）は Discord 用の
    コンテキストが行う。

    Args:
        ctx: コマンドコンテキスト。
    """
    from lilla_core.repository.skill_repository import get_skill_repo

    skills = await get_skill_repo().get_enabled()
    if not skills:
        await ctx.reply(t("command.skill.list_empty"))
        return

    lines = [t("command.skill.list_header")]
    lines.extend(format_skill_list_line(skill) for skill in skills)
    lines.append(t("command.skill.list_footer"))
    await ctx.reply("\n".join(lines))
