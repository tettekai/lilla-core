"""`notes` 拡張の LLM ツールで共通の処理。

ツールローダーが `type` 指定で解決するのはツール本体だけのため、`tools/` の外に置く
（`files` 拡張と同じ）。
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from lilla_core.tool_support.tool_result import tool_error

from .errors import NotesError

logger = logging.getLogger(__name__)

#: 想定外のエラーのときに LLM へ返す汎用の文言（例外の文字列は返さない）。
UNEXPECTED_ERROR_MESSAGE = "ノートの操作中に想定外のエラーが発生しました。"


def _resolve_bot(context: dict) -> Any:
    """エラー通知に使う Discord クライアントを返す（無ければ共有インスタンス）。"""
    bot = context.get("discord_client") if isinstance(context, dict) else None
    if bot is not None:
        return bot
    try:
        from lilla_core.bot_client import bot as shared_bot
    except Exception:  # 通知先が無くてもツールの結果は返す
        return None
    return shared_bot


async def run_notes_operation(
    tool_name: str,
    context: dict,
    operation: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """操作を実行し、例外を標準結果辞書へ変換する。

    `NotesError`（パスの不正・不在・既存・大きすぎる）はその文言を LLM へ返す。それ以外の
    例外（MongoDB に届かないなど）は文字列をそのまま返さず、汎用の文言にして
    `notify_error`（ERROR ログ + エラー通知チャンネル）へ流す。

    Args:
        tool_name: ツール名（LLM に渡す function 名）。
        context: ツール実行 context（エラー通知の Discord クライアントを探すのに使う）。
        operation: 標準結果辞書を返す非同期関数。

    Returns:
        標準形式の結果辞書。
    """
    try:
        return await operation()
    except NotesError as e:
        return tool_error(tool_name, None, str(e))
    except Exception as e:
        from lilla_core.core.error_notify import notify_error
        from lilla_core.ui.messages import t

        await notify_error(
            _resolve_bot(context), t("notes.tool.unexpected_error", tool=tool_name), e
        )
        return tool_error(tool_name, None, UNEXPECTED_ERROR_MESSAGE)


def format_time(value) -> str | None:
    """datetime を UTC の ISO 8601 文字列にする（無ければ None）。"""
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")
