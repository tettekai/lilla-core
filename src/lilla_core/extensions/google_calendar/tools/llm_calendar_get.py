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


def _strip_attendees(events: list[dict]) -> list[dict]:
    """各イベントから attendees フィールドを除去した新しいリストを返す。

    参加者のメールアドレスをLLMに渡さないよう、安全側に倒して除去する。
    入力の events は破壊的に変更しない（副作用のない純粋関数）。
    """
    return [
        {key: value for key, value in event.items() if key != "attendees"}
        for event in events
    ]


def _mask_calendar_ids(events: list[dict], id_to_friendly: dict[str, str]) -> list[dict]:
    """イベントリスト内のカレンダーIDを friendly_name に置換した新しいリストを返す。

    構造を再帰的に走査し、値がカレンダーIDと「完全一致」する文字列だけを
    friendly_name に置換する。organizer.email / creator.email など置換対象
    フィールドを列挙せずに済む点は従来どおりだが、文字列全体の一致で判定する
    ことで以下の誤動作を防ぐ:

    - あるIDが別のIDの部分文字列であるときの多重・誤置換
    - friendly_name が他のIDと衝突するときの再置換
    - dict のイテレーション順序への依存

    入力の events は破壊的に変更しない（副作用のない純粋関数）。
    """
    # "primary" は一般名詞のため置換対象から除外する。
    replacements = {
        calendar_id: friendly_name
        for calendar_id, friendly_name in id_to_friendly.items()
        if calendar_id != "primary"
    }
    if not replacements:
        return events

    def _mask(value: object) -> object:
        """値を再帰的に走査し、IDと完全一致する文字列を置換する。"""
        if isinstance(value, dict):
            return {key: _mask(child) for key, child in value.items()}
        if isinstance(value, list):
            return [_mask(child) for child in value]
        if isinstance(value, str):
            return replacements.get(value, value)
        return value

    return [_mask(event) for event in events]


async def execute(input: dict, context: dict) -> dict:
    """Google Calendar からイベントを取得する。

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
        from lilla_core.tool_support.date_range import DateRange
        from lilla_core.extensions.google_calendar.client import get_google_calendar_client
        from lilla_core.core.exceptions import ReauthenticationRequiredError

        # extensions.google_calendar.calendars（lilla.yaml）が正。なければ後方互換で calendar_ids を使用。
        calendars_config = get_section("google-calendar", GoogleCalendarConfig).calendars
        if calendars_config:
            id_to_friendly: dict[str, str] = {c.id: c.friendly_name for c in calendars_config}
            calendar_ids = list(id_to_friendly.keys())
        else:
            calendar_ids = context.get("calendar_ids") or ["primary"]
            id_to_friendly = {}

        dr = DateRange.parse(date_range)
        time_min, time_max = dr.as_datetime()

        calendar = get_google_calendar_client()

        try:
            events = await calendar.get_events(
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

        events = _strip_attendees(events)

        if id_to_friendly:
            events = _mask_calendar_ids(events, id_to_friendly)

        memory_entry = (
            f"Google Calendar のイベントを取得しました（{date_range}）。{len(events)}件。"
        )
        return tool_success(tool_name, memory_entry, events)

    except Exception as e:
        logger.error("get_calendar_events error: %s", e)
        return tool_error(tool_name, None, str(e))
