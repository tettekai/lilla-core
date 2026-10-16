"""Google Calendar からイベントを取得する LLM ツール。"""
from __future__ import annotations

import logging

from lilla_core.tool_support.tool_result import tool_error, tool_reauth_required, tool_success

logger = logging.getLogger(__name__)

SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_calendar_events",
        "description": "Google Calendarからイベントを取得します。date_rangeで期間を柔軟に指定してください。",
        "parameters": {
            "type": "object",
            "properties": {
                "date_range": {
                    "type": "string",
                    "description": (
                        "取得期間。以下の形式をすべてサポート:\n"
                        "・today\n"
                        "・yesterday\n"
                        "・last_7_days（last_30_days など任意の日数指定可）\n"
                        "・2026-04-20（単一日）\n"
                        "・2026-04-20/2026-04-26（範囲指定）"
                    ),
                },
                "query": {
                    "type": "string",
                    "description": "イベントのキーワード検索（任意）。タイトル・説明・場所などを検索。",
                },
                "max_results": {
                    "type": "integer",
                    "description": "取得する最大件数（デフォルト: 20）",
                },
            },
            "required": ["date_range"],
        },
    },
}


async def execute(input: dict, context: dict) -> dict:
    """Google Calendar からイベントを取得する。

    各予定は `event_summary.summarize_event` でホワイトリストの項目だけに絞り、
    取得元カレンダーの `friendly_name`（後方互換モードでは `calendar_1` などの
    汎用名）を `calendar` に付けて返す。主催者・作成者・参加者のメールアドレスや
    カレンダー ID は返さない。

    Parameters
    ----------
    input : dict
        date_range: 取得期間
        query: キーワード検索（任意）
        max_results: 最大取得件数（デフォルト: 20）
    context : dict
        取得対象のカレンダー設定は `lilla.yaml` の `extensions.google_calendar.calendars` から読む。
        未設定時は後方互換で context の calendar_ids（省略時は ["primary"]）を使用する。

    Returns
    -------
    dict
        success, tool_name, memory_entry, needs_auth, needs_auth_list, data, error
        を含む実行結果 dict
    """
    tool_name = SCHEMA["function"]["name"]
    try:
        date_range = input["date_range"]
        query = input.get("query")
        max_results = input.get("max_results", 20)

        from lilla_core.core.config import get_section
        from lilla_core.extensions.google_calendar import GoogleCalendarConfig
        from lilla_core.extensions.google_calendar.event_summary import (
            generic_calendar_name,
            summarize_event,
        )
        from lilla_core.tool_support.date_range import DateRange
        from lilla_core.extensions.google_calendar.client import get_google_calendar_client
        from lilla_core.core.exceptions import ReauthenticationRequiredError

        # extensions.google_calendar.calendars（lilla.yaml）が正。なければ後方互換で calendar_ids を使用。
        calendars_config = get_section("google-calendar", GoogleCalendarConfig).calendars
        if calendars_config:
            name_by_id: dict[str, str] = {c.id: c.friendly_name for c in calendars_config}
            calendar_ids = list(name_by_id.keys())
        else:
            # 後方互換モードでは ID（メールアドレスでありうる）を出さず、汎用名を付ける。
            calendar_ids = context.get("calendar_ids") or ["primary"]
            name_by_id = {}
            for index, calendar_id in enumerate(calendar_ids):
                name_by_id.setdefault(calendar_id, generic_calendar_name(index))

        dr = DateRange.parse(date_range)
        time_min, time_max = dr.as_datetime()

        calendar = get_google_calendar_client()

        try:
            items = await calendar.get_events(
                calendar_ids=calendar_ids,
                time_min=time_min,
                time_max=time_max,
                max_results=max_results,
                q=query,
            )
        except ReauthenticationRequiredError:
            return await tool_reauth_required(
                calendar,
                tool_name,
                "Google Calendar の再認証が必要だったため、ユーザーにログインを依頼しました。",
            )

        # 取得元は get_events に渡した ID のどれかなので、対応表に無いことは通常ない。
        # 万一無くても ID そのものは出さない。
        events = [
            summarize_event(event, name_by_id.get(calendar_id, "unknown"))
            for calendar_id, event in items
        ]

        memory_entry = (
            f"Google Calendar のイベントを取得しました（{date_range}）。{len(events)}件。"
        )
        return tool_success(tool_name, memory_entry, events)

    except Exception as e:
        logger.error("get_calendar_events error: %s", e)
        return tool_error(tool_name, None, str(e))
