"""日記ドラフトのツール `llm_diary_draft_faircopy_get`（実処理は `diary.service`）。"""
from __future__ import annotations

import logging

from lilla_core.tool_support.tool_result import tool_error, tool_success

from lilla_core.extensions.diary.service import DiaryDraftError, get_faircopy, parse_date

logger = logging.getLogger(__name__)

SCHEMA = {
    "type": "function",
    "function": {
        "name": "llm_diary_draft_faircopy_get",
        "description": (
            "指定した日付の日記の下書きを読み、本文に残った <<名前>> の穴を - にした清書を返す。<<moodscore>> は埋めずにそのまま返し、unresolved_holes に名前を入れる。保存や外部への書き出しはしない。その日の下書きが無ければ失敗する。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "日記の日付（YYYY-MM-DD）。必須。日付の区切り（day_rollover の時刻。既定 04:00）より前は前日、それ以外はその日の日付を指定する。省略しても補完されない。",
                },
            },
            "required": ["date"],
        },
    },
}


async def execute(input: dict, context: dict) -> dict:
    """日記ドラフトを読み、本文に残った <<...>> の穴を - にした清書を返す。

    Parameters
    ----------
    input : dict
        LLM から渡されるパラメータ（`date` 必須）
    context : dict
        ツール実行コンテキスト（未使用）

    Returns
    -------
    dict
        success / tool_name / memory_entry / needs_auth / needs_auth_list / data / error
        を含む標準形式の結果辞書
    """
    tool_name = SCHEMA["function"]["name"]

    try:
        d = parse_date(input.get("date"))
        result = await get_faircopy(d)
    except DiaryDraftError as e:
        return tool_error(tool_name, "llm_diary_draft_faircopy_get 失敗", str(e))
    except Exception as e:
        logger.exception("llm_diary_draft_faircopy_get failed")
        return tool_error(tool_name, "llm_diary_draft_faircopy_get 失敗", f"日記の清書の取得に失敗しました: {e}")
    return tool_success(
        tool_name,
        f"日記の清書（{d.isoformat()}）を取得",
        {
            "date": d.isoformat(),
            "content": result.content,
            "unresolved_holes": result.unresolved,
        },
    )
