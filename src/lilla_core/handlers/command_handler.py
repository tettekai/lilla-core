"""`!` プレフィックスの Discord コマンドのディスパッチ。

コマンドの書式は `!コマンド名 引数`（スペース区切り）で、コマンド名は完全一致で
レジストリを引く。個別コマンドのロジックは持たず、`src/commands/` 配下の
各コマンドモジュール（`@register_command` で登録）へ委譲する。
"""
import logging

from lilla_core.commands.registry import get_command_handler, known_command_names
from lilla_core.core.error_notify import notify_error
from lilla_core.ui.messages import t

logger = logging.getLogger(__name__)


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


async def handle_command(message, content: str, tools: dict, bot):
    """! で始まるコマンドを処理する。

    エラー（未知コマンド・ハンドラ内例外）は元のチャンネルへ返信せず、
    `notify_error` で ERROR ログとエラー通知チャンネルにのみ出力する。

    Args:
        message: コマンドを含む Discord メッセージ。
        content: コマンド文字列（`!` で始まる）。
        tools: ツールレジストリ。
        bot: Discord クライアント。
    """
    try:
        command_part = content[1:].strip()  # 先頭の ! を除去

        if not command_part:
            await message.reply(t("command.handler.empty"))
            return

        cmd_name, arg = parse_command(command_part)
        handler = get_command_handler(cmd_name)

        if handler is None:
            supported = ", ".join(f"!{name}" for name in sorted(known_command_names()))
            await notify_error(
                bot,
                t("command.handler.unknown_title"),
                t("command.handler.unknown", name=cmd_name, supported=supported),
            )
            return

        await handler(message, arg, tools, bot)

    except Exception as e:
        await notify_error(bot, t("command.handler.error"), e)
