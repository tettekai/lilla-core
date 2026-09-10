"""コマンドハンドラのレジストリ。

`src/commands/` 配下の各コマンドモジュールは `@register_command("コマンド名")` で
ハンドラ関数を登録し、ディスパッチ側（`handlers/command_handler.py`）は
`get_command_handler()` / `known_command_names()` を通してレジストリを引く。

既知コマンド名の一覧はこのレジストリだけが持つ。コマンドを追加するときは
`src/commands/` にファイルを置くだけでよく、どこかに名前を書き足す必要はない。
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

# コマンドハンドラのシグネチャ: (message, arg, tools, bot) -> Awaitable[None]
#   message: コマンドを含む Discord メッセージ
#   arg:     コマンド名の後ろに続く引数文字列（無い場合は空文字列）
#   tools:   task ツールのレジストリ
#   bot:     Discord クライアント
CommandHandler = Callable[..., Awaitable[None]]

_KNOWN_COMMANDS: dict[str, CommandHandler] = {}


def register_command(name: str) -> Callable[[CommandHandler], CommandHandler]:
    """コマンド名にハンドラ関数を登録するデコレータを返す。

    Args:
        name: `!` を除いたコマンド名（例: "cleardirty"）。

    Returns:
        ハンドラ関数をそのまま返すデコレータ。
    """
    def decorator(func: CommandHandler) -> CommandHandler:
        if name in _KNOWN_COMMANDS:
            logger.warning("Command name is duplicated (will overwrite): %s", name)
        _KNOWN_COMMANDS[name] = func
        return func

    return decorator


def known_command_names() -> list[str]:
    """既知コマンド名の一覧を返す（`extract_command_content` 等の判定用）。"""
    return list(_KNOWN_COMMANDS.keys())


def get_command_handler(name: str) -> CommandHandler | None:
    """コマンド名に対応するハンドラを返す。未登録なら None。"""
    return _KNOWN_COMMANDS.get(name)
