"""Google Calendar に予定を作成する LLM ツール。"""
from __future__ import annotations

import logging
from datetime import datetime

from lilla_core.tool_support.tool_result import tool_error, tool_reauth_required, tool_success

logger = logging.getLogger(__name__)

SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_calendar_event",
        "description": (
            "Google Calendarに新しい予定を作成します。"
            "書き込み先はlilla.yamlのextensions.google_calendar.calendarsに登録済みのカレンダーに限られます。"
            "作成前の確認は行わないため、内容が確定してから呼び出してください。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "calendar": {
                    "type": "string",
                    "description": "書き込み先カレンダーの表示名。",
                },
                "summary": {
                    "type": "string",
                    "description": "予定のタイトル。",
                },
                "all_day": {
                    "type": "boolean",
                    "description": "終日イベントかどうか（デフォルト: false）。",
                },
                "start": {
                    "type": "string",
                    "description": (
                        "開始日時。all_day=trueならYYYY-MM-DD形式の開始日、"
                        "false（既定）ならISO8601形式の開始日時（例: 2026-04-20T10:00:00+09:00）。"
                    ),
                },
                "end": {
                    "type": "string",
                    "description": (
                        "終了日時。all_day=trueならYYYY-MM-DD形式の終了日"
                        "（省略時はstartと同じ日の終日予定になる）、"
                        "false（既定）ならISO8601形式の終了日時（必須）。"
                    ),
                },
                "description": {
                    "type": "string",
                    "description": "予定の説明（任意）。",
                },
                "location": {
                    "type": "string",
                    "description": "予定の場所（任意）。",
                },
            },
            "required": ["calendar", "summary", "start"],
        },
    },
}


def build_schema(config: dict) -> dict:
    """設定済みカレンダーの friendly_name 一覧を calendar パラメータへ注入する。

    カレンダー一覧の正は `lilla.yaml` の `extensions.google_calendar.calendars` のため、ツール自身の
    YAML 設定（`config` 引数）は参照しない。

    Parameters
    ----------
    config : dict
        YAML から読み込んだこのツールの設定 dict（未使用）。

    Returns
    -------
    dict
        calendar パラメータへ friendly_name の enum・説明を注入した SCHEMA のコピー。
    """
    from lilla_core.core.config import get_section
    from lilla_core.extensions.google_calendar import GoogleCalendarConfig

    names = [c.friendly_name for c in get_section("google-calendar", GoogleCalendarConfig).calendars]

    schema = dict(SCHEMA)
    function = dict(schema.get("function", {}))
    parameters = dict(function.get("parameters", {}))
    properties = dict(parameters.get("properties", {}))
    calendar_prop = dict(properties.get("calendar", {}))

    if names:
        calendar_prop["enum"] = names
        calendar_prop["description"] = (
            "書き込み先カレンダーの表示名。利用可能な値: " + "、".join(names)
        )

    properties["calendar"] = calendar_prop
    parameters["properties"] = properties
    function["parameters"] = parameters
    schema["function"] = function
    return schema


async def execute(input: dict, context: dict) -> dict:
    """Google Calendar に新規イベントを作成する。

    Parameters
    ----------
    input : dict
        calendar（必須・friendly_name）、summary（必須）、all_day（既定 false）、
        start（必須）、end（任意）、description / location（任意）
    context : dict
        未使用（カレンダー一覧は `lilla.yaml` の `extensions.google_calendar.calendars` から読む）

    Returns
    -------
    dict
        success, tool_name, memory_entry, needs_auth, needs_auth_list, data, error
        を含む実行結果 dict
    """
    tool_name = SCHEMA["function"]["name"]
    try:
        calendar_name = input.get("calendar")
        summary = input.get("summary")
        all_day = input.get("all_day", False)
        start = input.get("start")
        end = input.get("end")
        description = input.get("description")
        location = input.get("location")

        if not calendar_name:
            return tool_error(tool_name, None, "calendar は必須パラメータです。")
        if not summary:
            return tool_error(tool_name, None, "summary は必須パラメータです。")
        if not start:
            return tool_error(tool_name, None, "start は必須パラメータです。")

        from lilla_core.core.config import get_section
        from lilla_core.extensions.google_calendar import GoogleCalendarConfig

        calendars = get_section("google-calendar", GoogleCalendarConfig).calendars
        if not calendars:
            return tool_error(
                tool_name, None, "extensions.google_calendar.calendars が設定されていません。"
            )

        id_by_friendly = {c.friendly_name: c.id for c in calendars}
        calendar_id = id_by_friendly.get(calendar_name)
        if calendar_id is None:
            return tool_error(tool_name, None, f"設定に存在しないカレンダーです: {calendar_name}")

        if all_day:
            end = end or start
        else:
            if not end:
                return tool_error(tool_name, None, "時間指定の予定には end が必須です。")
            try:
                start = datetime.fromisoformat(start)
                end = datetime.fromisoformat(end)
            except ValueError as e:
                return tool_error(tool_name, None, f"start / end の形式が不正です: {e}")

        from lilla_core.extensions.google_calendar.client import get_google_calendar_client
        from lilla_core.core.exceptions import ReauthenticationRequiredError

        calendar = get_google_calendar_client()

        try:
            event = await calendar.create_event(
                calendar_id,
                summary,
                all_day=all_day,
                start=start,
                end=end,
                description=description,
                location=location,
            )
        except ReauthenticationRequiredError:
            return await tool_reauth_required(
                calendar,
                tool_name,
                "Google Calendar の再認証が必要だったため、ユーザーにログインを依頼しました。",
            )

        memory_entry = f"Google Calendar に予定「{summary}」を作成しました。{event.get('htmlLink', '')}"
        return tool_success(tool_name, memory_entry, event)

    except Exception as e:
        logger.error("create_calendar_event error: %s", e)
        return tool_error(tool_name, None, str(e))
