"""日記ドラフトのツール `llm_diary_draft_set`（実処理は `diary.service`）。"""
from __future__ import annotations

import logging

from lilla_core.tool_support.tool_result import tool_error, tool_success

from lilla_core.extensions.diary.service import DiaryDraftError, set_draft, parse_date

logger = logging.getLogger(__name__)

SCHEMA = {
    "type": "function",
    "function": {
        "name": "llm_diary_draft_set",
        "description": (
            "指定した日付の日記の下書きを、渡した Markdown で丸ごと置き換える。content はテンプレの固定文（見出し・frontmatter のキー・日付・末尾）を保ち、変えてよいのは <<名前>> の穴の中だけ（固定文が壊れていると保存しない）。書き込めるのは日記日の日付の区切りから翌日の日付の区切りまでで、その外では、いま書き込める date を含めて失敗する。テンプレが未設定のときや日記の設定を読めないときも失敗する。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "日記の日付（YYYY-MM-DD）。必須。日付の区切り（day_rollover の時刻。既定 04:00）より前は前日、それ以外はその日の日付を指定する。省略しても補完されない。",
                },
                "content": {
                    "type": "string",
                    "description": "置き換え後の日記の下書き全文（Markdown）。テンプレの固定文を保ち、<<名前>> の穴の中だけを変えたもの。",
                },
            },
            "required": ["date", "content"],
        },
    },
}


async def execute(input: dict, context: dict) -> dict:
    """その日の日記ドラフトを、渡した Markdown で丸ごと置き換える。

    Parameters
    ----------
    input : dict
        LLM から渡されるパラメータ（`date` 必須、`content` 必須）
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
        await set_draft(d, input.get("content"))
    except DiaryDraftError as e:
        return tool_error(tool_name, "llm_diary_draft_set 失敗", str(e))
    except Exception as e:
        logger.exception("llm_diary_draft_set failed")
        return tool_error(tool_name, "llm_diary_draft_set 失敗", f"日記ドラフトの保存に失敗しました: {e}")
    return tool_success(
        tool_name,
        f"日記ドラフト（{d.isoformat()}）を更新",
        {"date": d.isoformat(), "saved": True},
    )
