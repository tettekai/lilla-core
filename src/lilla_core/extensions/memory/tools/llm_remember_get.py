"""長期記憶のツール `llm_remember_get`（実処理は `memory.service`）。"""
from __future__ import annotations

import logging

from lilla_core.tool_support.tool_result import tool_error, tool_success

from lilla_core.extensions.memory.service import RememberError, get_entries, parse_date

logger = logging.getLogger(__name__)

SCHEMA = {
    "type": "function",
    "function": {
        "name": "llm_remember_get",
        "description": (
            "その日に llm_remember_append で記録した「長期的に覚えておきたいこと」を、追記した順に全件返す（各要素は content と created_at）。記録が無ければ空のリストを返す。date が不正なときは失敗する。長期記憶の本体（notes の memory/）は読まない。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "記録の日付（YYYY-MM-DD）。必須。日付の区切り（day_rollover の時刻。既定 04:00）より前は前日、それ以外はその日の日付を指定する。省略しても補完されない。",
                },
            },
            "required": ["date"],
        },
    },
}


async def execute(input: dict, context: dict) -> dict:
    """その日の記録を追記した順に全件返す。

    Parameters
    ----------
    input : dict
        LLM から渡されるパラメータ（`date` 必須）
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
        entries = await get_entries(d)
    except RememberError as e:
        return tool_error(tool_name, "llm_remember_get 失敗", str(e))
    except Exception as e:
        logger.exception("llm_remember_get failed")
        return tool_error(tool_name, "llm_remember_get 失敗", f"記録の取得に失敗しました: {e}")
    return tool_success(
        tool_name,
        f"remember（{d.isoformat()}）を {len(entries)} 件取得",
        {
            "date": d.isoformat(),
            "entries": [{"content": e.content, "created_at": e.created_at} for e in entries],
        },
    )
