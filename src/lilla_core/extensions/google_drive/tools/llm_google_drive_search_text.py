"""Google Drive のテキストファイルを検索する LLM ツール。"""
from __future__ import annotations

import logging
from typing import Any

from lilla_core.tool_support.tool_result import tool_error, tool_success

logger = logging.getLogger(__name__)

#: `limit` の既定値と上限。
DEFAULT_LIMIT = 10
MAX_LIMIT = 50
#: 1 ページの件数と、辿るページ数の上限。Drive の検索は「フォルダの配下すべて」を
#: 条件にできないため、全体を検索してからホワイトリストで絞る。
_PAGE_SIZE = 100
_MAX_PAGES = 5

SCHEMA = {
    "type": "function",
    "function": {
        "name": "google_drive_search_text",
        "description": (
            "Google Drive のテキストファイル（text/*）をキーワードで検索します。"
            "ファイル名と本文が検索対象で、アクセスが許可されたフォルダ配下のものだけを返します。"
            "中身は llm_google_drive_get_text で取得してください。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "検索キーワード",
                },
                "limit": {
                    "type": "integer",
                    "description": f"返す最大件数（デフォルト: {DEFAULT_LIMIT}、最大: {MAX_LIMIT}）",
                },
            },
            "required": ["query"],
        },
    },
}


def _escape_query_literal(value: str) -> str:
    """Drive の検索クエリの文字列リテラル（シングルクォートの中）向けにエスケープする。"""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _parse_limit(value: Any) -> int | None:
    """`limit` を検証して返す（省略は既定値、上限超えは上限に丸める。不正なら None）。"""
    if value is None:
        return DEFAULT_LIMIT
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return min(value, MAX_LIMIT)


async def execute(input: dict, context: dict) -> dict:
    """Google Drive のテキストファイルを検索する。

    Parameters
    ----------
    input : dict
        query: 検索キーワード（必須）
        limit: 返す最大件数（任意）
    context : dict
        使わない（ホワイトリストは `extensions.google_drive` から読む）。

    Returns
    -------
    dict
        標準形式の結果辞書。`data` は `files`（id / name / mime_type / modified_time /
        writable / size）と `more_results_may_exist` を持つ。
    """
    from lilla_core.extensions.google_drive.access import Access
    from lilla_core.extensions.google_drive.tool_support import (
        build_resolver,
        is_visible_text_file,
        run_drive_operation,
        summarize_file,
    )

    tool_name = SCHEMA["function"]["name"]
    query = input.get("query")
    if not isinstance(query, str) or not query.strip():
        return tool_error(tool_name, None, "query を指定してください。")
    limit = _parse_limit(input.get("limit"))
    if limit is None:
        return tool_error(tool_name, None, "limit には 1 以上の整数を指定してください。")

    drive_query = (
        f"fullText contains '{_escape_query_literal(query.strip())}'"
        " and mimeType contains 'text/' and trashed = false"
    )

    async def operation(client, config) -> dict:
        """検索結果のページを辿り、ホワイトリスト配下の `text/*` だけを集める。"""
        resolver = build_resolver(client, config)
        found: list[dict] = []
        more = False
        if not resolver.is_empty:
            page_token: str | None = None
            for _ in range(_MAX_PAGES):
                page = await client.search_files(
                    drive_query, page_size=_PAGE_SIZE, page_token=page_token
                )
                page_token = page.get("nextPageToken")
                for metadata in page.get("files", []):
                    if len(found) >= limit:
                        # 確かめていない候補が残っている。
                        more = True
                        break
                    if not is_visible_text_file(metadata):
                        continue
                    access = await resolver.resolve(metadata["id"], metadata.get("parents"))
                    if access is Access.NONE:
                        continue
                    found.append(summarize_file(metadata, writable=access is Access.WRITE))
                if len(found) >= limit or not page_token:
                    more = more or bool(page_token)
                    break
            else:
                # ページ数の上限で打ち切った。
                more = bool(page_token)

        memory_entry = f"Google Drive でテキストファイルを検索しました（{query}）。{len(found)}件。"
        return tool_success(
            tool_name, memory_entry, {"files": found, "more_results_may_exist": more}
        )

    return await run_drive_operation(tool_name, operation)
