"""ホワイトリストのディレクトリの中身を一覧する LLM ツール。"""
from __future__ import annotations

import fnmatch
import logging
import os

from lilla_core.tool_support.tool_result import tool_error, tool_success

logger = logging.getLogger(__name__)

#: 1 回の一覧で返すエントリ数の上限（超えたら `truncated: true`）。
MAX_LIST_ENTRIES = 500

SCHEMA = {
    "type": "function",
    "function": {
        "name": "file_list",
        "description": (
            "アクセスが許可されたディレクトリの直下のファイルとディレクトリを一覧します（再帰しません）。"
            "path を空文字にすると、アクセスできるディレクトリの一覧を返します。"
            "type が dir のエントリの path をそのまま渡せば、その中をさらに一覧できます。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "一覧するディレクトリ（「ディレクトリの名前/相対パス」の形。"
                        "空文字でアクセスできるディレクトリの一覧）"
                    ),
                },
                "pattern": {
                    "type": "string",
                    "description": "名前で絞り込むグロブ（例: *.md）。省略時はすべて",
                },
            },
            "required": ["path"],
        },
    },
}


async def execute(input: dict, context: dict) -> dict:
    """ホワイトリストのディレクトリの直下を一覧する。

    実体パスがホワイトリストの外を指すエントリ（外へのシンボリックリンク）は返さない。

    Parameters
    ----------
    input : dict
        path: 一覧するディレクトリ（必須。空文字でホワイトリストのディレクトリの一覧）
        pattern: 名前で絞り込むグロブ（任意）
    context : dict
        エラー通知の Discord クライアント（`discord_client`）を探すのにだけ使う。

    Returns
    -------
    dict
        標準形式の結果辞書。`data` は `path` / `writable` / `entries`（各要素は `path` /
        `name` / `type`（`file` / `dir`）/ `size`（ファイルのみ）/ `modified_time` /
        `writable`）/ `truncated`。
    """
    from lilla_core.extensions.files.access import (
        Access,
        FileAccessError,
        access_for,
        child_logical,
        is_within,
        resolve_target,
        split_logical_path,
    )
    from lilla_core.extensions.files.tool_support import format_mtime, run_file_operation

    tool_name = SCHEMA["function"]["name"]
    pattern = input.get("pattern")
    if pattern is not None and (not isinstance(pattern, str) or not pattern.strip()):
        return tool_error(tool_name, None, "pattern は空でない文字列で指定してください。")

    def operation(config, roots) -> dict:
        """一覧を組み立てる（スレッドで実行する）。"""
        segments = split_logical_path(input.get("path"))
        if not roots:
            raise FileAccessError("アクセスが許可されたディレクトリがありません。")

        if not segments:
            entries = [
                {"path": r.label, "name": r.label, "type": "dir", "writable": r.writable}
                for r in roots
                if pattern is None or fnmatch.fnmatch(r.label, pattern)
            ]
            data = {"path": "", "writable": False, "entries": entries, "truncated": False}
            return tool_success(
                tool_name, f"アクセスできるディレクトリを一覧しました（{len(entries)}件）。", data
            )

        target = resolve_target(roots, segments)
        if not os.path.isdir(target.real):
            if os.path.exists(target.real):
                raise FileAccessError("ディレクトリではありません。")
            raise FileNotFoundError(target.real)

        root_real = target.root.real_path()
        entries: list[dict] = []
        truncated = False
        with os.scandir(target.real) as it:
            items = sorted(it, key=lambda e: e.name)
        for item in items:
            if pattern is not None and not fnmatch.fnmatch(item.name, pattern):
                continue
            real = os.path.realpath(item.path)
            if not is_within(root_real, real):
                continue
            if len(entries) >= MAX_LIST_ENTRIES:
                truncated = True
                break
            try:
                is_dir = item.is_dir()
                stat = item.stat()
            except OSError:
                # 壊れたリンクなど。一覧から外す。
                continue
            entry = {
                "path": child_logical(target.logical, item.name),
                "name": item.name,
                "type": "dir" if is_dir else "file",
                "modified_time": format_mtime(stat.st_mtime),
                "writable": access_for(roots, real) is Access.WRITE,
            }
            if not is_dir:
                entry["size"] = stat.st_size
            entries.append(entry)

        data = {
            "path": target.logical,
            "writable": target.access is Access.WRITE,
            "entries": entries,
            "truncated": truncated,
        }
        note = "、上限で切り詰め" if truncated else ""
        memory_entry = f"ディレクトリ「{target.logical}」を一覧しました（{len(entries)}件{note}）。"
        return tool_success(tool_name, memory_entry, data)

    return await run_file_operation(tool_name, context, operation)
