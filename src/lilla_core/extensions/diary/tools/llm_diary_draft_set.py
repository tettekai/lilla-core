"""日記ドラフトのツール `llm_diary_draft_set`（実処理は `diary.service`）。"""
from __future__ import annotations

import logging

from lilla_core.tool_support.tool_result import tool_error, tool_success

from lilla_core.extensions.diary.service import DiaryDraftError, set_draft

logger = logging.getLogger(__name__)

SCHEMA = {
    "type": "function",
    "function": {
        "name": "llm_diary_draft_set",
        "description": (
            "いまの日記日の下書きを、渡した Markdown で丸ごと置き換える。日記日はツールが決め（day_rollover の時刻で切り替わる）、実際に書いた日付を date で返す。content はテンプレの固定文（見出し・frontmatter のキー・日付・末尾）を保ち、変えてよいのは <<名前>> の穴の中だけ（固定文が壊れていると保存しない）。日付の行はいまの日記日と一致しなければならず、日記日が切り替わる前に取得した下書きを渡すと失敗するので、llm_diary_draft_get で取り直す。テンプレが未設定のときや日記の設定を読めないときも失敗する。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "置き換え後の日記の下書き全文（Markdown）。テンプレの固定文を保ち、<<名前>> の穴の中だけを変えたもの。",
                },
            },
            "required": ["content"],
        },
    },
}


async def execute(input: dict, context: dict) -> dict:
    """いまの日記日のドラフトを、渡した Markdown で丸ごと置き換える。

    Parameters
    ----------
    input : dict
        LLM から渡されるパラメータ（`content` 必須）
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
        d = await set_draft(input.get("content"))
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
