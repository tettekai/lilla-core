"""ノートを書く LLM ツール。"""
from __future__ import annotations

from lilla_core.core.file_storage import WRITE_MODES
from lilla_core.tool_support.tool_result import tool_error, tool_success

SCHEMA = {
    "type": "function",
    "function": {
        "name": "notes_write",
        "description": (
            "ノート（テキスト）を書きます。"
            "mode は create（新規作成。既にあれば失敗）/ overwrite（丸ごと上書き。無ければ作成）/"
            " append（末尾に追記。無ければ作成）のいずれかです。ディレクトリは自動でできます。"
            "上書きの前には notes_read で現在の内容を確認してください。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "書くノート（`/` 区切りの相対パス。例: diary/2026-11-08.md）",
                },
                "content": {
                    "type": "string",
                    "description": "書き込む内容",
                },
                "mode": {
                    "type": "string",
                    "enum": list(WRITE_MODES),
                    "description": "create / overwrite / append",
                },
            },
            "required": ["path", "content", "mode"],
        },
    },
}


async def execute(input: dict, context: dict) -> dict:
    """ノートを書く。1 回の書き込みが 1 コミットで、前の内容は Git の履歴に残る。

    Parameters
    ----------
    input : dict
        path: 書くノート（必須）
        content: 書き込む内容（必須）
        mode: `create` / `overwrite` / `append`（必須）
    context : dict
        エラー通知の Discord クライアント（`discord_client`）を探すのにだけ使う。

    Returns
    -------
    dict
        標準形式の結果辞書。`data` は `path` / `mode` / `created`（新しく作ったか）/
        `size`（書いた後の文字数）。
    """
    from lilla_core.extensions.notes import service
    from lilla_core.extensions.notes.tool_support import run_notes_operation

    tool_name = SCHEMA["function"]["name"]
    content = input.get("content")
    mode = input.get("mode")
    if not isinstance(content, str):
        return tool_error(tool_name, None, "content を文字列で指定してください。")
    if mode not in WRITE_MODES:
        return tool_error(
            tool_name, None, "mode は create / overwrite / append のいずれかを指定してください。"
        )

    async def operation() -> dict:
        """ノートを書く。"""
        result = await service.write_note(input.get("path"), content, mode)
        data = {"path": result.path, "mode": mode, "created": result.created, "size": result.size}
        verb = {"create": "作成", "overwrite": "上書き", "append": "追記"}[mode]
        memory_entry = f"ノート「{result.path}」に{verb}しました（{len(content)}文字）。"
        return tool_success(tool_name, memory_entry, data)

    return await run_notes_operation(tool_name, context, operation)
