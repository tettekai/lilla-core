"""日記ドラフトのツール `llm_diary_draft_get`（実処理は `diary.service`）。"""
from __future__ import annotations

import logging

from lilla_core.tool_support.tool_result import tool_error, tool_success

from lilla_core.extensions.diary.service import DiaryDraftError, get_draft, resolve_read_date

logger = logging.getLogger(__name__)

SCHEMA = {
    "type": "function",
    "function": {
        "name": "llm_diary_draft_get",
        "description": (
            "指定した日付の日記の下書き（Markdown）を返す。date を省略するといまの日記日（day_rollover の時刻で切り替わる）の下書きを返し、返した日付を date に入れる。下書きが無ければテンプレから作って保存し、それを返す。本文の <<名前>> は未記入の穴で、<<date>> は作成時に曜日付きの日付で埋まっている。date が不正なとき、下書きが無く、テンプレが未設定のときや日記の設定を読めないときは失敗する。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "日記の日付（YYYY-MM-DD）。省略するといまの日記日。過去の日を読むときだけ指定する。",
                },
            },
            "required": [],
        },
    },
}


async def execute(input: dict, context: dict) -> dict:
    """その日の日記ドラフト（Markdown）を返す。

    Parameters
    ----------
    input : dict
        LLM から渡されるパラメータ（`date` 任意。省略時はいまの日記日）
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
        d = await resolve_read_date(input.get("date"))
        content = await get_draft(d)
    except DiaryDraftError as e:
        return tool_error(tool_name, "llm_diary_draft_get 失敗", str(e))
    except Exception as e:
        logger.exception("llm_diary_draft_get failed")
        return tool_error(tool_name, "llm_diary_draft_get 失敗", f"日記ドラフトの取得に失敗しました: {e}")
    return tool_success(
        tool_name,
        f"日記ドラフト（{d.isoformat()}）を取得",
        {"date": d.isoformat(), "content": content},
    )
