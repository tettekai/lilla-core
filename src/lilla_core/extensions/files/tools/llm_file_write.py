"""書き込み可能なディレクトリにテキストファイルを書く LLM ツール。"""
from __future__ import annotations

import logging
import os

from lilla_core.tool_support.tool_result import tool_error, tool_success

logger = logging.getLogger(__name__)

#: `mode` に指定できる値と、`open()` に渡すモード。
_OPEN_MODES = {"create": "x", "overwrite": "w", "append": "a"}

SCHEMA = {
    "type": "function",
    "function": {
        "name": "file_write",
        "description": (
            "書き込みが許可されたディレクトリにテキストファイル（UTF-8）を書きます。"
            "mode は create（新規作成。既にあれば失敗）/ overwrite（丸ごと上書き。無ければ作成）/"
            " append（末尾に追記。無ければ作成）のいずれかです。親ディレクトリが無ければ作ります。"
            "上書きの前には file_read で現在の内容を確認してください。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "書くファイル（「ディレクトリの名前/相対パス」の形）",
                },
                "content": {
                    "type": "string",
                    "description": "書き込む内容",
                },
                "mode": {
                    "type": "string",
                    "enum": list(_OPEN_MODES),
                    "description": "create / overwrite / append",
                },
            },
            "required": ["path", "content", "mode"],
        },
    },
}


async def execute(input: dict, context: dict) -> dict:
    """テキストファイルを UTF-8（BOM 無し）で書く。

    書き込み可能ディレクトリ（`writable_dirs`）の配下だけを対象にし、読み取り専用
    ディレクトリの配下（書き込み可能ディレクトリの中に置いたものを含む）は拒否する。
    `append` で既存ファイルの先頭がテキスト（UTF-8）として読めなければ追記しない。
    改行コードは変換せず、`content` をそのまま書く。

    Parameters
    ----------
    input : dict
        path: 書くファイル（必須）
        content: 書き込む内容（必須）
        mode: `create` / `overwrite` / `append`（必須）
    context : dict
        エラー通知の Discord クライアント（`discord_client`）を探すのにだけ使う。

    Returns
    -------
    dict
        標準形式の結果辞書。`data` は `path` / `mode` / `created`（新しく作ったか）/ `size`。
    """
    from lilla_core.extensions.files.access import (
        Access,
        FileAccessError,
        resolve_target,
        split_logical_path,
    )
    from lilla_core.extensions.files.tool_support import (
        NOT_TEXT_MESSAGE,
        NotTextError,
        ensure_text_head,
        run_file_operation,
    )

    tool_name = SCHEMA["function"]["name"]
    content = input.get("content")
    mode = input.get("mode")
    if not isinstance(content, str):
        return tool_error(tool_name, None, "content を文字列で指定してください。")
    if mode not in _OPEN_MODES:
        return tool_error(
            tool_name, None, "mode は create / overwrite / append のいずれかを指定してください。"
        )

    def operation(config, roots) -> dict:
        """ファイルを書く（スレッドで実行する）。"""
        segments = split_logical_path(input.get("path"))
        target = resolve_target(roots, segments)
        if len(segments) < 2 or os.path.isdir(target.real):
            raise FileAccessError("ディレクトリには書き込めません。ファイルのパスを指定してください。")
        if target.access is not Access.WRITE:
            raise FileAccessError("このディレクトリには書き込みが許可されていません。")

        existed = os.path.exists(target.real)
        if mode == "create" and existed:
            raise FileAccessError("ファイルが既にあります（上書きするなら mode に overwrite を指定してください）。")
        if mode == "append" and existed:
            # Shift_JIS などへ UTF-8 で追記すると文字コードが混ざって読めなくなるため断る。
            try:
                ensure_text_head(target.real)
            except NotTextError:
                raise FileAccessError(NOT_TEXT_MESSAGE)
        parent = os.path.dirname(target.real)
        if os.path.exists(parent) and not os.path.isdir(parent):
            raise FileAccessError("親のパスがディレクトリではありません。")
        os.makedirs(parent, exist_ok=True)
        try:
            with open(target.real, _OPEN_MODES[mode], encoding="utf-8", newline="") as f:
                f.write(content)
        except FileExistsError:
            raise FileAccessError("ファイルが既にあります（上書きするなら mode に overwrite を指定してください）。")

        data = {
            "path": target.logical,
            "mode": mode,
            "created": not existed,
            "size": os.path.getsize(target.real),
        }
        verb = {"create": "作成", "overwrite": "上書き", "append": "追記"}[mode]
        memory_entry = f"ファイル「{target.logical}」に{verb}しました（{len(content)}文字）。"
        return tool_success(tool_name, memory_entry, data)

    return await run_file_operation(tool_name, context, operation)
