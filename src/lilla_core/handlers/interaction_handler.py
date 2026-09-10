"""Discord のインタラクション（ボタン押下）イベントのディスパッチ。

`custom_id` のプレフィックスで処理を振り分ける入口層。
承認 / 拒否ボタンは `handlers.approval_flow` へ委譲し、`command:` 形式は
`handlers.command_handler.handle_command` へ汎用的に委譲する。`action:{uuid}` 形式の
保留中アクションのみ自身で実行する。

このBotはオーナー専用の個人アシスタントという前提であり、プレフィックス分岐より
前でオーナー以外の押下を一括して拒否する（`message_handler.py` と同じ判定パターン）。
"""
import logging

import discord

from lilla_core.core.config import get_config
from lilla_core.loaders.llm_tool_loader import execute_tool_call
from lilla_core.repository.button_actions_repository import get_button_actions_repo
from lilla_core.services.conversation_service import build_tool_context
from lilla_core.handlers import command_handler, approval_flow
from lilla_core.ui.messages import t

logger = logging.getLogger(__name__)

_config = get_config()


async def handle_interaction(interaction: discord.Interaction, bot, tools, llm_tools) -> None:
    """Discord ボタン押下を処理し、紐づく保留中アクションを実行する。

    `action:{uuid}` 形式は button_actions コレクションから原子的に取得＆削除し、
    保存されたツールを `_execute_button_action` で直接実行する
    （`run_conversation` を経由せず、会話履歴にも残さない）。
    `command:{コマンド文字列}` 形式は `command_handler.handle_command` へ委譲する。
    承認 / 拒否ボタンは `approval_flow` へ委譲する。

    Args:
        interaction: 押下されたコンポーネントのインタラクション。
        bot: Discord ボットインスタンス。承認フローへ引き渡す。
        tools: task ツールのマップ。承認フローへ引き渡す。
        llm_tools: LLM ツールのマップ。保留中アクションの実行に使う。
    """
    if interaction.type != discord.InteractionType.component:
        return
    if str(interaction.user.id) != _config.discord.my_user_id:
        await interaction.response.send_message(
            t("interaction.no_permission"), ephemeral=True
        )
        return
    custom_id = (interaction.data or {}).get("custom_id", "")
    if custom_id.startswith("approve:"):
        await approval_flow.handle_approve_interaction(bot, tools, interaction, custom_id)
        return
    if custom_id.startswith("reject:"):
        await approval_flow.handle_reject_interaction(interaction, custom_id)
        return
    if custom_id.startswith("command:"):
        await _handle_command_interaction(interaction, tools, bot, custom_id)
        return
    if not custom_id.startswith("action:"):
        return

    uid = custom_id.split(":", 1)[1]
    repo = get_button_actions_repo()
    record = await repo.find_one_and_delete(uid)
    if record is None:
        await interaction.response.send_message(
            t("interaction.expired"), ephemeral=True
        )
        return

    # ツールの実行が数秒〜十数秒かかりうるので defer で「考え中」を表示してから followup で結果通知する
    await interaction.response.defer(thinking=True)

    try:
        result = await _execute_button_action(record, llm_tools)
        if result.get("success"):
            data = result.get("data") or {}
            message = data.get("message") or result.get("memory_entry")
            if message:
                msg = message
            else:
                msg = t("interaction.done", title=record["title"])
        else:
            msg = t("interaction.failed", error=result.get("error"))
        await interaction.followup.send(msg)
    except Exception as e:
        logger.error("Error in handle_interaction: %s", e, exc_info=True)
        try:
            await interaction.followup.send(t("interaction.error"))
        except Exception:
            pass


async def _handle_command_interaction(
    interaction: discord.Interaction, tools, bot, custom_id: str
) -> None:
    """`command:{コマンド文字列}` 形式のボタン押下を、コマンドディスパッチへ委譲する。

    `custom_id` の後ろの文字列をそのまま `command_handler.handle_command` に渡すだけの
    汎用処理であり、特定のコマンド名には依存しない。渡す `message` にはボタンを含む
    メッセージ自体（`interaction.message`）を使う。

    成功時の表示（メッセージの更新など）はコマンド側の責務とし、ここでは行わない
    （例: `!llm_guard_cancel` は自身で警告メッセージを edit する）。`defer()` は
    `thinking=True` を付けないため「考え中…」表示自体が出ず、成功時に followup を
    送らなくても「アプリケーションが応答しませんでした」表示にはならない。
    エラー時のみ followup で通知する。

    Args:
        interaction: 押下されたコンポーネントのインタラクション。
        tools: task ツールのマップ。コマンドハンドラへ引き渡す。
        bot: Discord クライアント。
        custom_id: `command:` で始まる custom_id（例: `command:!llm_guard_cancel`）。
    """
    content = custom_id.split(":", 1)[1]
    await interaction.response.defer()
    try:
        await command_handler.handle_command(interaction.message, content, tools, bot)
    except Exception as e:
        logger.error("Error processing command: interaction: %s", e, exc_info=True)
        try:
            await interaction.followup.send(t("interaction.error"))
        except Exception:
            pass


async def _execute_button_action(record: dict, llm_tools) -> dict:
    """ボタンに紐づく保留中アクションを実行し、標準形式の結果 dict を返す。

    保存されたツール名をそのまま LLM ツールとして実行するだけの一本道で、
    ツールごとの特別扱いは行わない。`execute_tool_call` は会話履歴の保存を
    行わないため、ボタン経由の実行が会話履歴に残ることはない。

    Args:
        record: button_actions から取得したレコード（`tool` / `params` を持つ）。
        llm_tools: LLM ツールのマップ。

    Returns:
        ツール実行結果の標準形式 dict。
    """
    ctx = build_tool_context()
    ctx["client_type"] = "discord"
    return await execute_tool_call(record["tool"], record["params"], llm_tools, ctx)
