"""現在日時を返すだけのサンプル LLM ツール。

個人データ・外部サービスへの依存を持たない、コア組み込みツールの実例。
`${CONFIG_ROOT}/tools/` に `type: lilla_core.builtin_tools.llm_current_datetime` の
YAML を置くと有効化できる。
"""
from __future__ import annotations

from lilla_core.tool_support.tool_result import tool_success
from lilla_core.utils.datetime_utils import local_now

SCHEMA = {
    "type": "function",
    "function": {
        "name": "llm_current_datetime",
        "description": "現在の日時を取得します。",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}


async def execute(input: dict, context: dict) -> dict:
    """現在日時を ISO 8601 形式で返す。

    Args:
        input: ツール入力（未使用）。
        context: ツール実行コンテキスト（未使用）。

    Returns:
        標準結果辞書（`data` に ISO 8601 形式の現在日時文字列）。
    """
    now = local_now()
    return tool_success(
        "llm_current_datetime",
        f"現在日時: {now.isoformat()}",
        now.isoformat(),
    )
