"""ノートのディレクトリの中身を一覧する LLM ツール。"""
from __future__ import annotations

import fnmatch

from lilla_core.tool_support.tool_result import tool_error, tool_success

#: 1 回の一覧で返すエントリ数の上限（超えたら `truncated: true`）。
MAX_LIST_ENTRIES = 500

SCHEMA = {
    "type": "function",
    "function": {
        "name": "notes_list",
        "description": (
            "ノートのディレクトリの直下のファイルとディレクトリを一覧します（再帰しません）。"
            "path を空文字にすると最上位を一覧します。"
            "件数の上限を超えたら truncated が true になります。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "一覧するディレクトリ（`/` 区切りの相対パス。空文字で最上位）",
                },
                "pattern": {
                    "type": "string",
                    "description": "名前の絞り込み（グロブ。例: *.md）",
                },
            },
            "required": ["path"],
        },
    },
}


async def execute(input: dict, context: dict) -> dict:
    """ディレクトリの直下のエントリを名前順に返す。

    ディレクトリは実体を持たず、配下のノートのパスから導く（空のディレクトリは無い）。

    Parameters
    ----------
    input : dict
        path: 一覧するディレクトリ（必須。空文字で最上位）
        pattern: 名前で絞り込むグロブ（任意）
    context : dict
        エラー通知の Discord クライアント（`discord_client`）を探すのにだけ使う。

    Returns
    -------
    dict
        標準形式の結果辞書。`data` は `path` / `writable` / `entries`（各要素は `path` /
        `name` / `type`（`file` / `dir`）/ `size`（ファイルのみ。文字数）/ `modified_time` /
        `writable`）/ `truncated`。
    """
    from lilla_core.extensions.notes import service
    from lilla_core.extensions.notes.paths import normalize_path
    from lilla_core.extensions.notes.tool_support import format_time, run_notes_operation

    tool_name = SCHEMA["function"]["name"]
    pattern = input.get("pattern")
    if pattern is not None and (not isinstance(pattern, str) or not pattern.strip()):
        return tool_error(tool_name, None, "pattern は空でない文字列で指定してください。")

    async def operation() -> dict:
        """一覧を組み立てる。"""
        path = normalize_path(input.get("path"), allow_empty=True)
        items = await service.list_entries(path)
        if pattern is not None:
            items = [e for e in items if fnmatch.fnmatch(e.name, pattern)]
        truncated = len(items) > MAX_LIST_ENTRIES
        entries = []
        for item in items[:MAX_LIST_ENTRIES]:
            entry = {
                "path": item.path,
                "name": item.name,
                "type": item.type,
                "modified_time": format_time(item.modified_time),
                "writable": True,
            }
            if item.size is not None:
                entry["size"] = item.size
            entries.append(entry)
        data = {"path": path, "writable": True, "entries": entries, "truncated": truncated}
        note = "、上限で切り詰め" if truncated else ""
        where = f"「{path}」" if path else "の最上位"
        memory_entry = f"ノート{where}を一覧しました（{len(entries)}件{note}）。"
        return tool_success(tool_name, memory_entry, data)

    return await run_notes_operation(tool_name, context, operation)
