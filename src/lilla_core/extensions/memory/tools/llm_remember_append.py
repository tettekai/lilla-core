"""長期記憶のツール `llm_remember_append`（実処理は `memory.service`）。"""
from __future__ import annotations

import logging

from lilla_core.tool_support.tool_result import tool_error, tool_success

from lilla_core.extensions.memory.service import RememberError, append_entry

logger = logging.getLogger(__name__)

SCHEMA = {
    "type": "function",
    "function": {
        "name": "llm_remember_append",
        "description": (
            "長期的に覚えておきたいこと（好み・決定事項・約束・学んだことなど）を、いまの日付の remember に 1 件記録する。記録は夜間の整理タスクがまとめて長期記憶へ整理するため、文章は整えなくてよい。追記するだけで、既存の記録の編集・削除はできない。その日に既にある本文と完全に一致する本文は追記されず、appended が false になる。記録する日付はツールが決め（日付は day_rollover の時刻で切り替わる）、実際に記録した日付を date で返す。空の本文・2000 文字を超える本文は失敗する。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "覚えておきたい内容。1 件に 1 つの事柄を書く。メモ書きのままでよい。",
                },
            },
            "required": ["content"],
        },
    },
}


async def execute(input: dict, context: dict) -> dict:
    """いまの日付の記録に 1 件追記する。

    Parameters
    ----------
    input : dict
        LLM から渡されるパラメータ（`content` 必須）
    context : dict
        ツール実行コンテキスト（未使用）

    Returns
    -------
    dict
        標準形式の結果辞書
    """
    tool_name = SCHEMA["function"]["name"]

    try:
        d, appended = await append_entry(input.get("content"))
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
