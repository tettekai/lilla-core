"""コマンドハンドラのレジストリ。

`src/commands/` 配下の各コマンドモジュールは `@register_command("コマンド名")` で
ハンドラ関数を登録し、ディスパッチ側（`handlers/command_handler.py`）は
`get_command_handler()` / `known_command_names()` を通してレジストリを引く。

既知コマンド名の一覧はこのレジストリだけが持つ。コマンドを追加するときは
`src/commands/` にファイルを置くだけでよく、どこかに名前を書き足す必要はない。
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

# コマンドハンドラのシグネチャ: (message, arg, tools, bot) -> Awaitable[None]
#   message: コマンドを含む Discord メッセージ
#   arg:     コマンド名の後ろに続く引数文字列（無い場合は空文字列）
#   tools:   task ツールのレジストリ
#   bot:     Discord クライアント
CommandHandler = Callable[..., Awaitable[None]]

_KNOWN_COMMANDS: dict[str, CommandHandler] = {}


def _identity(func: CommandHandler):
    """ハンドラの同一性を判定するためのキーを返す。

    `__qualname__` を持つ通常の関数は `(モジュール, 修飾名)` で比較する。
    再 exec されたモジュールの同じ関数を「別物」と誤判定しないため。
    持たないもの（モック等）はオブジェクトそのものを返し、同一性で比較する。
    """
    qualname = getattr(func, "__qualname__", None)
    if qualname is None:
        return func
    return (getattr(func, "__module__", "?"), qualname)


def _describe(func: CommandHandler) -> str:
    """例外メッセージ用にハンドラを表す文字列を返す。"""
    qualname = getattr(func, "__qualname__", None)
    if qualname is None:
        return repr(func)
    return f"{getattr(func, '__module__', '?')}.{qualname}"


def register_command(name: str) -> Callable[[CommandHandler], CommandHandler]:
    """コマンド名にハンドラ関数を登録するデコレータを返す。

    同じ名前を別のハンドラで登録しようとした場合は fail-fast する（静かな
    後勝ちにすると、どちらが動くかがロード順に依存してしまうため）。
    同じハンドラの再登録は許容する。ツールモジュールやテストからモジュールが
    複数回 exec されると同じ関数でも別オブジェクトになるため、同一性の判定には
    `_identity()` を使う。

    Args:
        name: `!` を除いたコマンド名（例: "cleardirty"）。

    Returns:
        ハンドラ関数をそのまま返すデコレータ。

    Raises:
        ValueError: 同じ名前が別のハンドラで既に登録されている場合。
    """
    def decorator(func: CommandHandler) -> CommandHandler:
        existing = _KNOWN_COMMANDS.get(name)
        if existing is not None and _identity(existing) != _identity(func):
            raise ValueError(
                f"Command name '{name}' is already registered by {_describe(existing)}"
            )
        _KNOWN_COMMANDS[name] = func
        return func

    return decorator


def known_command_names() -> list[str]:
    """既知コマンド名の一覧を返す（`extract_command_content` 等の判定用）。"""
    return list(_KNOWN_COMMANDS.keys())


def get_command_handler(name: str) -> CommandHandler | None:
    """コマンド名に対応するハンドラを返す。未登録なら None。"""
    return _KNOWN_COMMANDS.get(name)
