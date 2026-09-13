"""承認フロー。

handlers 配下の承認フロー共通部品。外部ユーザー（オーナー以外）から届いた
コマンドを `discord.approval_channel_id` で設定した承認チャンネルへ承認依頼として投稿し、承認 / 拒否
ボタンの押下を処理する共通ロジック。
Discord の `on_message` / `on_interaction` の双方から再利用する。

信頼境界は「承認依頼メッセージ（bot 自身の投稿）」に置く。承認依頼を作る時点で
BODY（テキスト直書き／添付ファイルのいずれでも）を解決して「実行される内容の全文」を
承認依頼メッセージ自体に載せ、承認ボタン押下時はそのメッセージ以外を一切参照しない。
元メッセージを承認後に再取得しないため、承認待ちの間に送信者がメッセージを編集しても
実行される内容は変わらない（TOCTOU 対策）。
"""
from __future__ import annotations

import io
import logging

import discord

from lilla_core.commands.attachment_body import resolve_command_body
from lilla_core.core.config import get_config
from lilla_core.repository.pending_tool_calls_repository import get_pending_tool_calls_repo
from lilla_core.services.message_util import parse_correlation_frontmatter
from lilla_core.handlers import command_handler
from lilla_core.ui.messages import t, translations

logger = logging.getLogger(__name__)

_config = get_config()

# 承認依頼メッセージの本文に実行内容を直接書ける最大文字数。超える場合は添付ファイルにする
INLINE_COMMAND_LIMIT = 300

# 実行内容の全文を添付するときのファイル名
FULL_COMMAND_FILENAME = "message.txt"

# BODY を添付ファイルで受け取れるコマンド（`commands/attachment_body.py` 経由）
_ATTACHMENT_BODY_COMMANDS = ("mongodata", "toolresult")


def command_marker() -> str:
    """承認依頼メッセージ内で「この行より後ろが実行内容の全文」を示す区切り行を返す。

    文言はロケールで変わるため、組み立て時（`_build_approval_text`）と復元時
    （`_restore_execution_content`）のどちらもこの関数を通す。

    Returns:
        現在のロケールの区切り行。
    """
    return t("approval.command_marker")


async def build_toolresult_command(content: str) -> str | None:
    """外部エージェントからの結果メッセージを `!toolresult` コマンド文字列に変換する。

    FrontMatter に correlation_id を持ち、かつ `pending_tool_calls` に有効な pending
    レコードが実在する場合のみ変換する。未登録・期限切れ・処理済みの correlation_id は
    無視してログのみ残す（見知らぬ相手のメッセージを承認フローに載せないため）。

    Returns:
        変換したコマンド文字列。対象外のメッセージの場合は None。
    """
    correlation_id, body = parse_correlation_frontmatter(content)
    if correlation_id is None:
        return None
    try:
        exists = await get_pending_tool_calls_repo().exists_pending(correlation_id)
    except Exception as e:
        logger.error("Failed to query pending request: %s", e, exc_info=True)
        return None
    if not exists:
        logger.info(
            "Ignored because no pending request exists for this correlation_id: %s", correlation_id
        )
        return None
    return f"!toolresult {correlation_id}\n{body}"


async def extract_approvable_command(content: str) -> str | None:
    """承認フローに載せるコマンド文字列を組み立てる。

    既知コマンドを優先し、見つからない場合のみ外部エージェントからの結果メッセージの
    変換を試みる。ここで得られるのはメッセージ本文だけから作った文字列であり、
    添付ファイルの BODY はまだ含まれていない（`resolve_full_command` で解決する）。
    """
    command_content = command_handler.extract_command_content(content)
    if command_content is not None:
        return command_content
    return await build_toolresult_command(content)


async def resolve_full_command(message, content: str, bot) -> str | None:
    """承認依頼に載せる「正規化済みコマンド全文」を組み立てる。

    BODY を添付ファイルで受け取れるコマンド（`!mongodata` / `!toolresult`）は、
    `commands.attachment_body.resolve_command_body` で BODY を解決し、テキストとして
    インライン化した 1 本の文字列に正規化する。こうすることで承認者が実行内容の全文を
    確認でき、承認後の実行も元メッセージの添付を読み直さずに済む。

    それ以外のコマンドは BODY を添付で受け取る仕様が無いため、`content` をそのまま返す。

    Args:
        message: 承認対象となった元の Discord メッセージ（添付 BODY の解決にのみ使う）。
        content: `extract_approvable_command` が組み立てたコマンド文字列。
        bot: Discord クライアント。BODY 解決失敗時のエラー通知に使う。

    Returns:
        BODY 込みのコマンド全文。BODY を解決できなかった場合は None。
    """
    if not content.startswith("!"):
        return content

    # 承認後の実行時（`command_handler.handle_command`）と同じ規則でコマンド名と引数に分ける
    name, arg = command_handler.parse_command(content[1:].strip())

    if name not in _ATTACHMENT_BODY_COMMANDS:
        return content

    error_title = f"!{name}"

    if name == "toolresult":
        first_line, _, rest = arg.partition("\n")
        correlation_id = first_line.strip()
        body = await resolve_command_body(message, rest, bot, error_title)
        if body is None:
            return None
        return f"!toolresult {correlation_id}\n{body}"

    body = await resolve_command_body(message, arg, bot, error_title)
    if body is None:
        return None
    return f"!{name}\n{body}"


def _build_approval_text(message: discord.Message, jump_url: str, full_command: str) -> str:
    """承認依頼メッセージの本文を組み立てる。

    実行内容が `INLINE_COMMAND_LIMIT` 以内なら全文をそのまま本文へ書き、超える場合は
    先頭のみのプレビューと「全文は添付ファイル」の案内に差し替える。

    Args:
        message: 承認対象となった元の Discord メッセージ（送信者・チャンネル名の表示用）。
        jump_url: 元メッセージへのリンク。
        full_command: BODY 込みのコマンド全文。

    Returns:
        承認依頼メッセージの本文。
    """
    if len(full_command) <= INLINE_COMMAND_LIMIT:
        body_section = full_command
    else:
        body_section = (
            f"{full_command[:INLINE_COMMAND_LIMIT]}\n"
            + t("approval.truncated", filename=FULL_COMMAND_FILENAME)
        )

    return t(
        "approval.request",
        user_id=_config.discord.my_user_id,
        author=message.author.display_name,
        channel=message.channel.name,
        jump_url=jump_url,
        marker=command_marker(),
        body=body_section,
    )


async def send_approval_request(bot, message: discord.Message, content: str) -> None:
    """外部ユーザーからのコマンドを設定済みの承認チャンネルに承認依頼として投稿する。

    投稿前に BODY（テキスト直書き／添付ファイル）を解決し、実行される内容の全文を
    承認依頼メッセージ自体に載せる。全文が `INLINE_COMMAND_LIMIT` を超える場合は、
    本文にはプレビューのみを書き、bot が新規に作成した `message.txt` に全文を添付する
    （元メッセージの添付ファイルは使い回さない）。

    BODY を解決できなかった場合は承認依頼を作成しない（通知は
    `resolve_command_body` が `notify_error` で行う）。

    Args:
        bot: Discord ボットインスタンス。承認チャンネルの検索に使う。
        message: 承認対象となった元のメッセージ。
        content: 承認後に実行されるコマンド文字列（BODY 解決前）。
    """
    approval_channel_id = _config.discord.approval_channel_id
    if not approval_channel_id:
        logger.warning("Cannot send approval request because approval channel is not configured")
        return
    try:
        approval_channel = bot.get_channel(int(approval_channel_id))
    except (TypeError, ValueError):
        logger.warning("Approval channel ID is invalid: %s", approval_channel_id)
        return
    if approval_channel is None:
        logger.warning("Approval channel not found: %s", approval_channel_id)
        return

    full_command = await resolve_full_command(message, content, bot)
    if full_command is None:
        logger.error("Aborted creating approval request: could not resolve BODY")
        return

    guild_id = message.guild.id
    channel_id = message.channel.id
    message_id = message.id
    jump_url = f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"

    approval_text = _build_approval_text(message, jump_url, full_command)

    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label=t("approval.button.approve"),
        style=discord.ButtonStyle.success,
        custom_id=f"approve:{channel_id}:{message_id}",
    ))
    view.add_item(discord.ui.Button(
        label=t("approval.button.reject"),
        style=discord.ButtonStyle.danger,
        custom_id=f"reject:{channel_id}:{message_id}",
    ))

    if len(full_command) <= INLINE_COMMAND_LIMIT:
        await approval_channel.send(content=approval_text, view=view)
        return

    attachment = discord.File(
        io.BytesIO(full_command.encode("utf-8")), filename=FULL_COMMAND_FILENAME
    )
    await approval_channel.send(content=approval_text, view=view, file=attachment)


class ApprovedMessage:
    """承認済みコマンドの実行時にコマンドハンドラへ渡すメッセージ代理オブジェクト。

    実行内容は承認依頼メッセージから復元済みの文字列がすべてであり、添付ファイルを
    再度読ませてはいけない（`message.txt` は BODY ではなくコマンド全文のため）。
    そこで `content` を復元済みの文字列に、`attachments` を空に固定し、それ以外の属性
    （`reply` / `author` など）は承認依頼メッセージへ委譲する。

    `channel` だけは、`message.reply` を使うコマンドの返信が承認チャンネルではなく
    元の依頼チャンネルへ届くよう、渡された元チャンネルを優先する。元チャンネルは
    `custom_id` の channel_id から解決したもので、実行内容の信頼境界には関与しない
    （返信先の決定にしか使わない）。
    """

    def __init__(self, approval_message, content: str, original_channel=None) -> None:
        """代理オブジェクトを作る。

        Args:
            approval_message: 承認依頼メッセージ（bot 自身の投稿）。
            content: 承認依頼メッセージから復元した実行内容の全文。
            original_channel: 元メッセージがあったチャンネル。解決できなかった場合は
                None を渡すと承認依頼メッセージのチャンネルへフォールバックする。
        """
        self._approval_message = approval_message
        self._original_channel = original_channel
        self.content = content
        self.attachments: list = []

    @property
    def channel(self):
        """返信先チャンネル。元チャンネルが解決できていればそちらを使う。"""
        return self._original_channel or self._approval_message.channel

    def __getattr__(self, name: str):
        """未定義の属性アクセスを承認依頼メッセージへ委譲する。"""
        return getattr(self._approval_message, name)


def _resolve_original_channel(bot, custom_id: str):
    """`custom_id` の channel_id から元メッセージのチャンネルを解決する。

    キャッシュ参照（`bot.get_channel`）のみで、元メッセージの再取得は行わない。
    解決に使うのはチャンネル ID だけなので、実行内容の信頼境界には影響しない。

    Args:
        bot: Discord ボットインスタンス。
        custom_id: `approve:{channel_id}:{message_id}` 形式の custom_id。

    Returns:
        解決したチャンネル。解決できなかった場合は None。
    """
    parts = custom_id.split(":")
    if len(parts) < 2:
        logger.warning("Could not extract channel ID from custom_id: %s", custom_id)
        return None
    try:
        return bot.get_channel(int(parts[1]))
    except (TypeError, ValueError) as e:
        logger.warning("Failed to resolve original channel (custom_id=%s): %s", custom_id, e)
        return None


async def _restore_execution_content(bot, approval_message) -> str | None:
    """承認依頼メッセージから実行内容の全文を復元する。

    添付ファイルがあればそちらを全文とみなし（`send_approval_request` が全文を
    `message.txt` として添付したケース）、無ければ本文の区切り行より後ろを使う。
    元メッセージは一切参照しない。

    Args:
        bot: Discord クライアント。添付読み取り失敗時のエラー通知に使う。
        approval_message: 承認依頼メッセージ（bot 自身の投稿）。

    Returns:
        実行内容の全文。復元できなかった場合は None。
    """
    if getattr(approval_message, "attachments", None):
        return await resolve_command_body(approval_message, "", bot, t("approval.error_title"))

    text = approval_message.content or ""
    # ロケールを切り替えた前後で残っている承認依頼も復元できるよう、他ロケールの
    # 区切り行も候補にする（現在のロケールの区切り行を最優先で探す）。
    for marker in translations("approval.command_marker"):
        marker_index = text.find(marker)
        if marker_index >= 0:
            restored = text[marker_index + len(marker):].strip()
            return restored or None
    return None


async def handle_approve_interaction(
    bot, tools, interaction: discord.Interaction, custom_id: str
) -> None:
    """承認ボタン押下時の処理。

    実行内容は承認依頼メッセージ（`interaction.message`）からのみ復元し、元メッセージの
    `fetch_message()` は行わない。外部から編集できない bot 自身の投稿だけを信頼できる
    情報源とすることで、承認待ちの間に元メッセージが編集されても実行内容は変わらない。

    `custom_id` の channel_id は返信先チャンネルの解決にのみ使う（実行内容は
    一切そこから取らない）。

    Args:
        bot: Discord ボットインスタンス。返信先チャンネルの解決とコマンドハンドラへの
            引き渡しに使う。
        tools: task ツールのマップ。コマンドハンドラへ引き渡す。
        interaction: 押下されたボタンのインタラクション。
        custom_id: `approve:{channel_id}:{message_id}` 形式の custom_id。
            実行内容の取得には使わず、返信先チャンネルの解決にだけ使う。
    """
    approval_message = interaction.message

    content = await _restore_execution_content(bot, approval_message)
    if content is None:
        logger.error("Failed to restore approval content (custom_id=%s)", custom_id)
        await interaction.response.send_message(
            t("approval.restore_failed"),
            ephemeral=True,
        )
        return

    await interaction.response.edit_message(
        content=approval_message.content + "\n\n" + t("approval.approved_suffix"),
        view=make_disabled_view(approved=True),
    )

    original_channel = _resolve_original_channel(bot, custom_id)

    try:
        await command_handler.handle_command(
            ApprovedMessage(approval_message, content, original_channel), content, tools, bot
        )
    except Exception as e:
        logger.error("Command execution error after approval: %s", e, exc_info=True)


async def handle_reject_interaction(interaction: discord.Interaction, custom_id: str) -> None:
    """拒否ボタン押下時の処理。"""
    await interaction.response.edit_message(
        content=interaction.message.content + "\n\n" + t("approval.rejected_suffix"),
        view=make_disabled_view(approved=False),
    )


def make_disabled_view(approved: bool) -> discord.ui.View:
    """両ボタンを無効化した View を返す。"""
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label=t("approval.button.approved" if approved else "approval.button.approve"),
        style=discord.ButtonStyle.success,
        disabled=True,
    ))
    view.add_item(discord.ui.Button(
        label=t("approval.button.reject" if approved else "approval.button.rejected"),
        style=discord.ButtonStyle.danger,
        disabled=True,
    ))
    return view
