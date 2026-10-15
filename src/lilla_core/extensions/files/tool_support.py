"""`files` 拡張の LLM ツールで共通の処理。

設定の取得、テキストとしての読み取り（NUL・UTF-8 の判定と BOM の除去）、
エントリ情報の整形、例外の標準結果辞書への変換をまとめる。ツールローダーが `type`
指定で解決するのはツール本体だけのため、`tools/` の外に置く（google-drive と同じ）。
"""
from __future__ import annotations

import asyncio
import codecs
import logging
import os
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from lilla_core.tool_support.tool_result import tool_error

from .access import FileAccessError, Root, build_roots

if TYPE_CHECKING:
    from lilla_core.extensions.files import FilesConfig

logger = logging.getLogger(__name__)

#: NUL バイトを探す先頭のバイト数（ここに NUL があればバイナリとみなす）。
BINARY_SNIFF_BYTES = 8192
#: `llm_file_read` が読み込むファイルの大きさの上限（バイト）。超えたら読まずに断る。
MAX_READ_FILE_BYTES = 50 * 1024 * 1024

#: テキストとして読めないファイルに返す文言。
NOT_TEXT_MESSAGE = "テキストファイル（UTF-8）として読めないファイルです。"
#: 想定外のエラーのときに LLM へ返す汎用の文言（例外の文字列は返さない）。
UNEXPECTED_ERROR_MESSAGE = "ファイル操作中に想定外のエラーが発生しました。"
#: OS にアクセスを拒否されたときの文言。
PERMISSION_DENIED_MESSAGE = "OS にアクセスを拒否されました（ファイルの権限を確認してください）。"
#: 存在しないパスに返す文言。
FILE_NOT_FOUND_MESSAGE = "指定したパスは存在しません。"


class NotTextError(Exception):
    """バイナリ、または UTF-8 として読めないファイル。"""


class FileTooLargeError(Exception):
    """読み込みの上限を超える大きさのファイル。"""


def load_config() -> FilesConfig:
    """`extensions.files` セクションを型付きで返す。"""
    from lilla_core.core.config import get_section
    from lilla_core.extensions.files import FilesConfig

    return get_section("files", FilesConfig)


def load_roots() -> tuple[FilesConfig, list[Root]]:
    """設定とホワイトリストのディレクトリの一覧を返す。"""
    config = load_config()
    return config, build_roots(config)


def decode_text(raw: bytes) -> str:
    """バイト列を UTF-8 のテキストとして読む（先頭の BOM は取り除く）。

    Raises:
        NotTextError: 先頭 `BINARY_SNIFF_BYTES` バイトに NUL がある、または UTF-8 として
            読めない場合。
    """
    if b"\0" in raw[:BINARY_SNIFF_BYTES]:
        raise NotTextError
    if raw.startswith(codecs.BOM_UTF8):
        raw = raw[len(codecs.BOM_UTF8):]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise NotTextError from e


def read_text_file(real: str, max_bytes: int | None = None) -> str:
    """ファイルを UTF-8 のテキストとして読む。

    Args:
        real: 実体パス。
        max_bytes: 読み込むファイルの大きさの上限（バイト。省略時は上限なし）。

    Raises:
        FileTooLargeError: `max_bytes` を超える場合（中身は読まない）。
        NotTextError: テキストとして読めない場合。
    """
    with open(real, "rb") as f:
        if max_bytes is not None and os.fstat(f.fileno()).st_size > max_bytes:
            raise FileTooLargeError
        raw = f.read()
    return decode_text(raw)


def format_mtime(timestamp: float) -> str:
    """更新日時（UNIX 時刻）を UTC の ISO 8601 文字列にする。"""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


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


async def run_file_operation(
    tool_name: str,
    context: dict,
    operation: Callable[[FilesConfig, list[Root]], dict[str, Any]],
) -> dict[str, Any]:
    """設定を読み、同期の操作をスレッドで実行して、例外を標準結果辞書へ変換する。

    `FileAccessError` はその文言を、想定できる OS のエラー（不在・権限など）は短い
    文言を LLM へ返す。それ以外の例外は文字列をそのまま LLM へ返さず、汎用の文言にして
    `notify_error`（ERROR ログ + エラー通知チャンネル）へ流す。

    Args:
        tool_name: ツール名（LLM に渡す function 名）。
        context: ツール実行 context（エラー通知の Discord クライアントを探すのに使う）。
        operation: 設定とディレクトリの一覧を受け取り、標準結果辞書を返す同期関数。

    Returns:
        標準形式の結果辞書。
    """
    try:
        config, roots = load_roots()
        return await asyncio.to_thread(operation, config, roots)
    except FileAccessError as e:
        return tool_error(tool_name, None, str(e))
    except FileNotFoundError:
        return tool_error(tool_name, None, FILE_NOT_FOUND_MESSAGE)
    except PermissionError:
        logger.warning("%s: permission denied by the OS", tool_name)
        return tool_error(tool_name, None, PERMISSION_DENIED_MESSAGE)
    except Exception as e:
        from lilla_core.core.error_notify import notify_error
        from lilla_core.ui.messages import t

        await notify_error(
            _resolve_bot(context), t("files.tool.unexpected_error", tool=tool_name), e
        )
        return tool_error(tool_name, None, UNEXPECTED_ERROR_MESSAGE)
