"""長期記憶のツール `llm_remember_get`（実処理は `memory.service`）。"""
from __future__ import annotations

import logging

from lilla_core.tool_support.tool_result import tool_error, tool_success

from lilla_core.extensions.memory.service import RememberError, get_entries, resolve_read_date

logger = logging.getLogger(__name__)

SCHEMA = {
    "type": "function",
    "function": {
        "name": "llm_remember_get",
        "description": (
            "指定した日付に llm_remember_append で記録した「長期的に覚えておきたいこと」を、追記した順に全件返す（各要素は content と created_at）。date を省略するといまの日付（day_rollover の時刻で切り替わる）の記録を返し、返した日付を date に入れる。記録が無ければ空のリストを返す。date が不正なときは失敗する。長期記憶の本体（notes の memory/）は読まない。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "記録の日付（YYYY-MM-DD）。省略するといまの日付。過去の日の記録を読むときだけ指定する。",
                },
            },
            "required": [],
        },
    },
}


async def execute(input: dict, context: dict) -> dict:
    """その日の記録を追記した順に全件返す。

    Parameters
    ----------
    input : dict
        LLM から渡されるパラメータ（`date` 任意。省略時はいまの日付）
    context : dict
        ツール実行コンテキスト（未使用）

    Returns
    -------
    dict
        標準形式の結果辞書
    """
    tool_name = SCHEMA["function"]["name"]

    try:
        d = await resolve_read_date(input.get("date"))
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
