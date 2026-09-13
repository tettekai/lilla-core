"""Discord のメッセージ受信イベントのディスパッチ。

メッセージフック → 承認フロー振り分け → コマンド処理 → 通常会話、の順に処理する
入口層。通常会話は送信中タスクをチャンネル単位で管理し、後続メッセージが来たら
先行タスクをキャンセルする。
"""
import asyncio
import logging

import discord

from lilla_core.core.config import get_config
from lilla_core.core.error_notify import notify_error
from lilla_core.ui.messages import t
from lilla_core.core.runtime_state import get_active_llm_name
from lilla_core.services.conversation_service import run_conversation
from lilla_core.services.memory_manager import get_memory_manager
from lilla_core.services.message_splitter import split_response
from lilla_core.services import image_attachment
from lilla_core.handlers import command_handler, approval_flow

logger = logging.getLogger(__name__)

_config = get_config()

_memory_manager = get_memory_manager()

# チャンネルごとの送信中タスクを管理する辞書
_discord_active_tasks: dict[int, asyncio.Task] = {}


async def _save_assistant_block(message, block: str, sent_msg) -> None:
    """送信済みの返信ブロックを、対応する Discord メッセージ情報つきで会話履歴に保存する。

    `conversations` の `discord_channel_id` / `discord_message_ids` は
    「Discord メッセージと会話履歴エントリの紐付け」を表すフィールドなので、
    `!toolresult` 経由に限らず通常会話の返信にも一貫して付与する。

    Args:
        message: 返信元となったユーザーのメッセージ。チャンネル ID の取得に使う。
        block: 会話履歴に残す返信本文（分割後の 1 ブロック）。
        sent_msg: 実際に送信された Discord メッセージ。
    """
    await _memory_manager.add_conversation(
        {"role": "assistant", "content": block},
        discord_channel_id=message.channel.id,
        discord_message_ids=[sent_msg.id],
    )


async def handle_message(message, bot, tools, llm_tools, message_hook) -> None:
    """Discord のメッセージ受信イベントを処理する。

    Args:
        message: 受信した Discord メッセージ。
        bot: Discord ボットインスタンス。
        tools: task ツールのマップ。コマンドハンドラ・承認フローへ引き渡す。
        llm_tools: LLM ツールのマップ。会話処理へ引き渡す。
        message_hook: `on_message` フック。メッセージを受け取り、自身で処理した
            場合は True を返す（True が返れば以降の標準処理は行わない）。
    """
    if message.author == bot.user:
        return

    # メッセージフックを先に処理する（オーナー判定より前に行う。フックの用途は
    # 呼び出し側の実装依存で、専用チャンネル名やトークンなどでの検証を前提としない）
    processed = await message_hook(message)
    if processed:
        return

    # オーナー以外からのメッセージは承認フロー対象。オーナーからのメッセージは
    # FrontMatter が付いていても通常の会話としてそのまま LLM に渡す（下のブロックへ抜ける）。
    if str(message.author.id) != _config.discord.my_user_id:
        # DM は対象外
        if isinstance(message.channel, discord.DMChannel):
            return
        # 承認チャンネル自体への書き込みは対象外（ループ防止）
        approval_channel_id = _config.discord.approval_channel_id
        if approval_channel_id and str(message.channel.id) == str(approval_channel_id):
            return
        # 既知コマンド、または外部エージェントからの結果メッセージなら承認フローへ。
        # それ以外は無視する。
        command_content = await approval_flow.extract_approvable_command(message.content)
        if command_content is not None:
            await approval_flow.send_approval_request(bot, message, command_content)
        return

    content = message.content.strip()

    # 既知コマンドで始まる行があればコマンドとして処理
    command_content = command_handler.extract_command_content(message.content)
    if command_content is not None:
        await command_handler.handle_command(message, command_content, tools, bot)
        return

    # 通常の会話処理
    if bot.user.mentioned_in(message) or isinstance(message.channel, discord.DMChannel):
        channel_id = message.channel.id

        # 送信中のタスクがあればキャンセル
        existing_task = _discord_active_tasks.get(channel_id)
        if existing_task and not existing_task.done():
            existing_task.cancel()

        async def send_split_messages(message, content: str | list, memory_content: str | None = None):
            try:
                async with message.channel.typing():
                    save_content = memory_content if memory_content is not None else content
                    await _memory_manager.add_conversation({"role": "user", "content": save_content})
                    override = content if memory_content is not None else None
                    reply = await run_conversation(
                        llm_tools,
                        client_type="discord",
                        discord_channel_id=channel_id,
                        llm_name=get_active_llm_name(),
                        override_last_user_content=override,
                        tool_call_notifier=lambda line: message.channel.send(line),
                    )
                    blocks = split_response(reply)
                    # 送信済みブロックを (本文, Discord メッセージ) のタプルで保持する。
                    # 会話履歴には対応する Discord メッセージの情報もあわせて残す。
                    sent_blocks = []
                    try:
                        for i, block in enumerate(blocks):
                            sent_msg = await (
                                message.reply(block) if i == 0 else message.channel.send(block)
                            )
                            sent_blocks.append((block, sent_msg))
                            if i < len(blocks) - 1:
                                await asyncio.sleep(2.00)
                    except asyncio.CancelledError:
                        # 送信済み分のみ履歴に保存して終了
                        for block, sent_msg in sent_blocks:
                            await _save_assistant_block(message, block, sent_msg)
                        raise
                    # 正常完了時：全ブロックを履歴に保存
                    for block, sent_msg in sent_blocks:
                        await _save_assistant_block(message, block, sent_msg)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                await notify_error(bot, t("message.error"), e)
            finally:
                if _discord_active_tasks.get(channel_id) is task:
                    _discord_active_tasks.pop(channel_id, None)

        image_attachments = image_attachment.filter_image_attachments(message.attachments)

        if image_attachments:
            # サイズ超過・ダウンロード失敗はサービス側で notify_error 済み。
            # None が返ったら会話処理そのものを行わない。
            image_parts = await image_attachment.build_image_content_parts(
                image_attachments, bot, _config
            )
            if image_parts is None:
                return

            content_parts = []
            if content:
                content_parts.append({"type": "text", "text": content})
            content_parts.extend(image_parts)

            image_count = len(image_attachments)
            text_for_memory = (content + " " if content else "") + f"[画像 {image_count} 枚添付]"

            task = asyncio.create_task(
                send_split_messages(message, content_parts, memory_content=text_for_memory)
            )
        else:
            task = asyncio.create_task(send_split_messages(message, content))

        _discord_active_tasks[channel_id] = task
