"""長期記憶のツール `llm_remember_append`（実処理は `memory.service`）。"""
from __future__ import annotations

import logging

from lilla_core.tool_support.tool_result import tool_error, tool_success

from lilla_core.extensions.memory.service import RememberError, append_entry, parse_date

logger = logging.getLogger(__name__)

SCHEMA = {
    "type": "function",
    "function": {
        "name": "llm_remember_append",
        "description": (
            "長期的に覚えておきたいこと（好み・決定事項・約束・学んだことなど）を、その日の remember に 1 件記録する。記録は夜間の整理タスクがまとめて長期記憶へ整理するため、文章は整えなくてよい。追記するだけで、既存の記録の編集・削除はできない。その日に既にある本文と完全に一致する本文は追記されず、appended が false になる。日付 d の分に追記できるのは d の日付の区切り（day_rollover の時刻。既定 04:00）から翌日の区切りまでで、それ以外の日付・空の本文・2000 文字を超える本文は失敗する。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "記録の日付（YYYY-MM-DD）。必須。日付の区切りより前は前日、それ以外はその日の日付を指定する。省略しても補完されない。",
                },
                "content": {
                    "type": "string",
                    "description": "覚えておきたい内容。1 件に 1 つの事柄を書く。メモ書きのままでよい。",
                },
            },
            "required": ["date", "content"],
        },
    },
}


async def execute(input: dict, context: dict) -> dict:
    """その日の記録に 1 件追記する。

    Parameters
    ----------
    input : dict
        LLM から渡されるパラメータ（`date` 必須、`content` 必須）
    context : dict
        ツール実行コンテキスト（未使用）

    Returns
    -------
    dict
        標準形式の結果辞書
    """
    tool_name = SCHEMA["function"]["name"]

    try:
        d = parse_date(input.get("date"))
        appended = await append_entry(d, input.get("content"))
    except RememberError as e:
        return tool_error(tool_name, "llm_remember_append 失敗", str(e))
    except Exception as e:
        logger.exception("llm_remember_append failed")
        return tool_error(tool_name, "llm_remember_append 失敗", f"記録の追記に失敗しました: {e}")
    if not appended:
        return tool_success(
            tool_name,
            f"remember（{d.isoformat()}）は既にあるため追記せず",
            {
                "date": d.isoformat(),
                "appended": False,
                "reason": "その日の記録に同じ本文が既にあるため追記しませんでした",
            },
        )
    return tool_success(
        tool_name,
        f"remember（{d.isoformat()}）に追記",
        {"date": d.isoformat(), "appended": True},
    )
