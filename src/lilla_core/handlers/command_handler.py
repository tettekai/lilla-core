"""`!` プレフィックスのコマンドのディスパッチ。

コマンドの書式は `!コマンド名 引数`（スペース区切り）で、コマンド名は完全一致で
レジストリを引く。個別コマンドのロジックは持たず、`src/commands/` 配下の
各コマンドモジュール（`@register_command` で登録）へ委譲する。

入口は 2 つある。

- `dispatch_command(text, ctx)`: トランスポート非依存の入口。呼び出し元は
  `commands/context.py` の `CommandContext` を実装して渡す（拡張が Discord 以外の
  クライアントからコマンドを実行するときに使う）。承認判定は行わない。
- `handle_command(message, content, tools, bot)`: Discord 用の入口。Discord 用の
  コンテキスト（`DiscordCommandContext`）を組み立てて `dispatch_command` を呼ぶ
  薄いラッパー。オーナー以外からのコマンドは呼び出し前に承認フロー
  （`handlers/approval_flow.py`）を通っている。
"""
import logging
from typing import Any

from lilla_core.commands.context import (
    DISCORD_CLIENT_TYPE,
    CommandContext,
    CommandResult,
)
from lilla_core.commands.discord_util import chunk_lines
from lilla_core.commands.registry import (
    get_command_handler,
    is_discord_only,
    known_command_names,
)
from lilla_core.core.error_notify import notify_error
from lilla_core.ui.messages import t

logger = logging.getLogger(__name__)

# `dispatch_command` が tools / bot を省略されたときに使う、起動時に登録された値
# （`bot.py` が `set_command_runtime()` で登録する）。拡張の HTTP / WebSocket ハンドラは
# task ツールのレジストリや Discord クライアントを受け取らないため、ここで持つ。
_runtime_tools: dict = {}
_runtime_bot: Any = None


def set_command_runtime(tools: dict, bot: Any) -> None:
    """`dispatch_command` が既定で使う task ツールのレジストリと Discord クライアントを登録する。

    `bot.py` が起動時（ツールのロード直後）に一度だけ呼ぶ。

    Args:
        tools: task ツールのレジストリ（`load_all_tools()` の戻り値）。
        bot: Discord クライアント。
    """
    global _runtime_tools, _runtime_bot
    _runtime_tools = tools
    _runtime_bot = bot


def parse_command(command_part: str) -> tuple[str, str]:
    """`!` を除いたコマンド文字列を (コマンド名, 引数文字列) に分割する。

    最初の空白（改行を含む）で 1 回だけ分割する。引数が無い場合は空文字列を返す。
    承認フロー（`handlers/approval_flow.py`）が承認依頼の組み立て時に同じ分割規則で
    引数を取り出せるよう、公開関数にしている。
    """
    parts = command_part.split(maxsplit=1)
    if not parts:
        return "", ""
    return parts[0], parts[1] if len(parts) > 1 else ""


def extract_command_content(content: str) -> str | None:
    """既知コマンドで始まる行を探し、その行以降を返す。見つからなければ None。

    Hermes 等の外部エージェントが付与するヘッダーを除去するために使用する。
    既知コマンドの判定はコマンドレジストリ（`commands.registry`）から行うため、
    コマンドを追加しても、このモジュールに手を入れる必要はない。
    """
    known = known_command_names()
    lines = content.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("!"):
            continue
        name, _ = parse_command(stripped[1:])
        if name in known:
            return "\n".join(lines[i:]).strip()
    return None


class DiscordCommandContext(CommandContext):
    """Discord から来たコマンドのコンテキスト。

    通常のメッセージ・承認済みコマンドの代理オブジェクト（`approval_flow.ApprovedMessage`）・
    ボタン押下時のボタン付きメッセージ（`interaction.message`）のいずれも包む。
    返信は従来どおり `message.reply` で送る（`ApprovedMessage.reply` は元チャンネルへ
    参照なしで送る）。Discord の 1 メッセージ上限を超える文言は行の境界で分割し、
    2 通目以降は `message.channel.send` で同じチャンネルへ続けて送る。
    """

    def __init__(self, message: Any, bot: Any, *, is_owner: bool = True) -> None:
        """コンテキストを作る。

        Args:
            message: コマンドを含む Discord メッセージ（またはその代理オブジェクト）。
            bot: Discord クライアント。会話の開始に使う。
            is_owner: オーナーとして扱うか。`handle_command` へ届く時点でオーナーの
                発言か承認済みのため、既定は True。
        """
        author = getattr(message, "author", None)
        author_id = getattr(author, "id", None)
        super().__init__(
            client_type=DISCORD_CLIENT_TYPE,
            author_id=str(author_id) if author_id is not None else None,
            is_owner=is_owner,
            attachments=list(getattr(message, "attachments", None) or []),
        )
        self._message = message
        self._bot = bot

    @property
    def discord_message(self) -> Any:
        """包んでいる Discord メッセージ（または代理オブジェクト）を返す。"""
        return self._message

    async def deliver_reply(self, text: str) -> None:
        """元のメッセージへ返信する（長い文言は分割して続けて送る）。"""
        chunks = chunk_lines(text)
        await self._message.reply(chunks[0])
        for chunk in chunks[1:]:
            await self._message.channel.send(chunk)

    async def deliver_error(self, text: str) -> None:
        """エラーも通常の返信と同じ送信先・送り方で返す。"""
        await self.deliver_reply(text)

    async def deliver_conversation(self, content: str) -> None:
        """Discord の会話フロー（`message_handler.start_conversation`）で 1 ターン始める。"""
        # いずれも import 時に設定を読み込むため、関数内 import にしている。
        from lilla_core.handlers.message_handler import start_conversation
        from lilla_core.loaders.llm_tool_loader import get_llm_tools

        start_conversation(self._message, self._bot, get_llm_tools(), content)


async def dispatch_command(
    text: str, ctx: CommandContext, *, tools: dict | None = None, bot: Any = None
) -> CommandResult:
    """`!` で始まるコマンド文字列を実行し、結果を返す（トランスポート非依存の入口）。

    承認判定は行わない。呼び出し元が認証済みのオーナーとしてコマンドを実行する入口で、
    Discord のオーナー以外からのコマンドは承認フローを経てから `handle_command`
    経由でここへ届く。Discord 以外のクライアントは、拡張がクライアントトークン
    （`verify_client_token`）などで認証した接続についてのみ、`is_owner=True` の
    コンテキストで呼ぶこと。`is_owner` が False のコンテキストでは何も実行せず error を返す。

    エラーの扱い:

    - 空のコマンド・Discord 専用コマンドを Discord 以外から呼んだ場合は
      `ctx.reply_error` で呼び出し元へ返す。
    - 未知のコマンドとハンドラ内の例外は `notify_error` で ERROR ログとエラー通知
      チャンネルへ出し、呼び出し元へは何も返さない（結果は error。例外の文字列は
      結果にも載せない）。

    Args:
        text: `!` で始まるコマンド文字列（前後の空白は無視する）。
        ctx: 呼び出し元のコマンドコンテキスト。1 回の呼び出しごとに新しく作ること。
        tools: task ツールのレジストリ。省略時は `set_command_runtime()` で登録した値。
        bot: Discord クライアント。省略時は `set_command_runtime()` で登録した値。

    Returns:
        実行結果。コマンドが何も返信しなかった場合でも ok / error を判別できる。

    Raises:
        ValueError: `text` が `!` で始まらない場合（呼び出し元の誤り）。
    """
    stripped = text.strip()
    if not stripped.startswith("!"):
        raise ValueError("Command text must start with '!'")
    tools = _runtime_tools if tools is None else tools
    bot = _runtime_bot if bot is None else bot

    if not ctx.is_owner:
        logger.warning(
            "Refusing to dispatch a command from a non-owner context (client_type=%s)",
            ctx.client_type,
        )
        ctx.mark_failed()
        return ctx.build_result()

    try:
        command_part = stripped[1:].strip()  # 先頭の ! を除去

        if not command_part:
            await ctx.reply_error(t("command.handler.empty"))
            return ctx.build_result()

        cmd_name, arg = parse_command(command_part)
        handler = get_command_handler(cmd_name)

        if handler is None:
            supported = ", ".join(f"!{name}" for name in sorted(known_command_names()))
            await notify_error(
                bot,
                t("command.handler.unknown_title"),
                t("command.handler.unknown", name=cmd_name, supported=supported),
            )
            ctx.mark_failed()
            return ctx.build_result()

        if is_discord_only(cmd_name) and ctx.client_type != DISCORD_CLIENT_TYPE:
            await ctx.reply_error(t("command.handler.discord_only", name=cmd_name))
            return ctx.build_result()

        await handler(ctx, arg, tools, bot)

    except Exception as e:
        await notify_error(bot, t("command.handler.error"), e)
        ctx.mark_failed()

    return ctx.build_result()


async def handle_command(message, content: str, tools: dict, bot) -> CommandResult:
    """Discord から来た ! で始まるコマンドを処理する。

    Discord 用のコンテキストを組み立てて `dispatch_command` を呼ぶだけのラッパー。
    外部エージェント由来のヘッダー除去（`extract_command_content`）と承認フローは
    呼び出し元（`message_handler` / `approval_flow`）の責務。

    Args:
        message: コマンドを含む Discord メッセージ（承認済みコマンドの代理オブジェクトや
            ボタン付きメッセージでもよい）。
        content: コマンド文字列（`!` で始まる）。
        tools: ツールレジストリ。
        bot: Discord クライアント。

    Returns:
        `dispatch_command` の実行結果。
    """
    ctx = DiscordCommandContext(message, bot)
    return await dispatch_command(content, ctx, tools=tools, bot=bot)
