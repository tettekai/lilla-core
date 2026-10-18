"""LLM へ返す予定の要約（項目のホワイトリスト）。

Calendar API の予定には主催者・作成者・参加者のメールアドレス（`organizer` /
`creator` / `attendees`）や、カレンダー ID を埋め込んだ `htmlLink` / `iCalUID` が
含まれる。値の置き換えでは取りこぼしが出るため、LLM に返す項目をここで列挙した
ものだけに絞る。`llm_google_calendar_get` / `llm_google_calendar_create` の両ツールが共有する。
"""
from __future__ import annotations

from datetime import date, timedelta

#: `calendars` が空の後方互換モードで、カレンダー ID の代わりに付ける名前の接頭辞。
GENERIC_CALENDAR_NAME_PREFIX = "calendar_"


def generic_calendar_name(index: int) -> str:
    """後方互換モードで使う汎用のカレンダー名（`calendar_1` など）を返す。

    Parameters
    ----------
    index : int
        取得対象のカレンダー一覧における 0 始まりの位置

    Returns
    -------
    str
        1 始まりの番号を付けた汎用名
    """
    return f"{GENERIC_CALENDAR_NAME_PREFIX}{index + 1}"


def _inclusive_end_date(end_date: str) -> str:
    """終日予定の `end.date`（exclusive）を利用者から見た最終日（inclusive）へ戻す。

    作成ツールの `end` は inclusive で受け取るため、取得結果もそれに揃える。
    日付として読めない値はそのまま返す。
    """
    try:
        return (date.fromisoformat(end_date) - timedelta(days=1)).isoformat()
    except ValueError:
        return end_date


def summarize_event(event: dict, calendar_name: str) -> dict:
    """Calendar API の予定 1 件を、LLM に返してよい項目だけの dict に絞る。

    返すのは `calendar`（取得元の `friendly_name` か汎用名）・`summary`・`start`・
    `end`・`all_day` と、値があるときだけ `status`・`location`・`description`。
    `start` / `end` は文字列（終日なら `YYYY-MM-DD`、時間指定なら ISO 8601 の日時）
    で、終日予定の `end` は最終日を含む（inclusive）日付にする。それ以外の項目
    （`organizer` / `creator` / `attendees` / `htmlLink` / `iCalUID` / `id` など）は
    返さない。入力の `event` は変更しない。

    Parameters
    ----------
    event : dict
        Calendar API の予定（events.list の `items` の要素、または events.insert の応答）
    calendar_name : str
        取得元カレンダーとして付ける名前

    Returns
    -------
    dict
        ホワイトリストの項目だけを持つ予定の要約
    """
    start = event.get("start") or {}
    end = event.get("end") or {}
    all_day = "date" in start and "dateTime" not in start

    if all_day:
        start_value = start.get("date")
        end_value = end.get("date")
        if isinstance(end_value, str):
            end_value = _inclusive_end_date(end_value)
    else:
        start_value = start.get("dateTime")
        end_value = end.get("dateTime")

    summary: dict = {
        "calendar": calendar_name,
        "summary": event.get("summary", ""),
        "start": start_value,
        "end": end_value,
        "all_day": all_day,
    }
    for key in ("status", "location", "description"):
        value = event.get(key)
        if value:
            summary[key] = value
    return summary
