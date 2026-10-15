"""Google Drive / Sheets の LLM ツールで共通の処理。

設定とクライアントの取得、ホワイトリスト判定器の組み立て、再認証・API エラーの
標準結果辞書への変換、本文の切り詰めなどをまとめる。ツールローダーが `type` 指定で
解決するのはツール本体だけのため、`tools/` の外（`client.py` と同じ兄弟モジュールの
位置）に置く。
"""
from __future__ import annotations

import json
import logging
import mimetypes
from collections.abc import Awaitable, Callable
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

import aiohttp

from lilla_core.core.exceptions import ReauthenticationRequiredError
from lilla_core.tool_support.tool_result import tool_error, tool_reauth_required

from .access import AccessResolver

if TYPE_CHECKING:
    from lilla_core.extensions.google_drive import GoogleDriveConfig
    from lilla_core.extensions.google_drive.client import GoogleDriveClient

logger = logging.getLogger(__name__)

#: ホワイトリスト外・`text/*` 以外・ゴミ箱・存在しないファイルに共通で返す文言
#: （ホワイトリスト外のファイルがあるかどうかを LLM に区別させない）。
NOT_FOUND_MESSAGE = "ファイルが見つからないか、アクセスが許可されていません。"

REAUTH_MESSAGE = "Google Drive の再認証が必要だったため、ユーザーにログインを依頼しました。"


def load_config() -> GoogleDriveConfig:
    """`extensions.google_drive` セクションを型付きで返す。"""
    from lilla_core.core.config import get_section
    from lilla_core.extensions.google_drive import GoogleDriveConfig

    return get_section("google-drive", GoogleDriveConfig)


def build_resolver(client: Any, config: GoogleDriveConfig) -> AccessResolver:
    """設定のホワイトリストでホワイトリスト判定器を作る。"""
    return AccessResolver(client, config.read_only_folders, config.writable_folders)


def is_text_mime(mime_type: Any) -> bool:
    """この拡張が扱うテキストファイル（`text/*`）の MIME タイプかを返す。"""
    return isinstance(mime_type, str) and mime_type.startswith("text/")


def is_visible_text_file(metadata: dict[str, Any]) -> bool:
    """ゴミ箱に無い `text/*` のファイルかを返す（それ以外は存在しないものとして扱う）。"""
    return not metadata.get("trashed") and is_text_mime(metadata.get("mimeType"))


def guess_text_mime(name: str) -> str:
    """新規作成するファイルの MIME タイプをファイル名から決める（必ず `text/*` を返す）。

    Markdown は環境によって `mimetypes` に登録が無いため先に決め打ちし、それ以外は
    `mimetypes` の推測が `text/*` ならそれを、そうでなければ `text/plain` を使う。
    """
    suffix = PurePosixPath(name).suffix.lower()
    if suffix in (".md", ".markdown"):
        return "text/markdown"
    guessed, _ = mimetypes.guess_type(name, strict=False)
    if is_text_mime(guessed):
        return guessed
    return "text/plain"


def truncate_text(text: str, limit: int) -> tuple[str, bool]:
    """本文を `limit` 文字までに切り詰め、(本文, 切り詰めたか) を返す。"""
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def summarize_file(metadata: dict[str, Any], *, writable: bool) -> dict[str, Any]:
    """LLM へ返すファイルの情報を、必要な項目だけに整形する（親フォルダの ID は返さない）。"""
    summary: dict[str, Any] = {
        "id": metadata.get("id"),
        "name": metadata.get("name"),
        "mime_type": metadata.get("mimeType"),
        "modified_time": metadata.get("modifiedTime"),
        "writable": writable,
    }
    if metadata.get("size") is not None:
        summary["size"] = metadata["size"]
    return summary


def api_error_message(error: aiohttp.ClientResponseError) -> str:
    """Google API のエラーを LLM 向けの短い文言に変換する。"""
    if error.status == 404:
        return NOT_FOUND_MESSAGE
    if error.status == 429:
        return "Google API のレート制限に達しました。時間をおいて再度お試しください。"
    if error.status == 403:
        return "Google API に拒否されました（権限が足りないか、利用制限に達しています）。"
    if error.status == 400:
        detail = _google_error_detail(error.message)
        suffix = f": {detail}" if detail else ""
        return f"Google API がリクエストを受け付けませんでした（400）{suffix}"
    return f"Google API の呼び出しに失敗しました（HTTP {error.status}）。"


def _google_error_detail(body: Any) -> str | None:
    """Google API のエラー本文（JSON）から `error.message` を取り出す。"""
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return None
    error = parsed.get("error") if isinstance(parsed, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    return message if isinstance(message, str) and message else None


async def run_drive_operation(
    tool_name: str,
    operation: Callable[[GoogleDriveClient, GoogleDriveConfig], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """設定とクライアントを用意して操作を実行し、例外を標準結果辞書へ変換する。

    再認証が必要なら認可フローを開始して `needs_auth` の結果を、Google API の
    エラーなら `api_error_message()` の文言で失敗結果を返す。それ以外の例外も
    失敗結果にして外へ漏らさない。

    Args:
        tool_name: ツール名（LLM に渡す function 名）。
        operation: クライアントと設定を受け取り、標準結果辞書を返す非同期関数。

    Returns:
        標準形式の結果辞書。
    """
    try:
        from lilla_core.extensions.google_drive.client import get_google_drive_client

        config = load_config()
        client = get_google_drive_client()
    except Exception as e:
        logger.error("%s setup error: %s", tool_name, e)
        return tool_error(tool_name, None, str(e))

    try:
        return await operation(client, config)
    except ReauthenticationRequiredError:
        return await tool_reauth_required(client, tool_name, REAUTH_MESSAGE)
    except aiohttp.ClientResponseError as e:
        logger.warning("%s: Google API error status=%s", tool_name, e.status)
        return tool_error(tool_name, None, api_error_message(e))
    except Exception as e:
        logger.error("%s error: %s", tool_name, e)
        return tool_error(tool_name, None, str(e))
