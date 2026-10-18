"""`lilla_core.extensions.google_calendar.event_summary` のテスト。"""
from __future__ import annotations

from lilla_core.extensions.google_calendar.event_summary import (
    generic_calendar_name,
    summarize_event,
)


class TestSummarizeEvent:
    """予定をホワイトリストの項目に絞る。"""

    def test_drops_non_whitelisted_keys(self) -> None:
        """organizer / creator / attendees / htmlLink / iCalUID / id などは返さない。"""
        event = {
            "id": "evt1",
            "htmlLink": "https://www.google.com/calendar/event?eid=x",
            "iCalUID": "evt1@google.com",
            "summary": "会議",
            "status": "confirmed",
            "organizer": {"email": "owner@example.com", "displayName": "Owner"},
            "creator": {"email": "owner@example.com"},
            "attendees": [{"email": "a@example.com"}],
            "start": {"dateTime": "2026-04-20T10:00:00+09:00", "timeZone": "Asia/Tokyo"},
            "end": {"dateTime": "2026-04-20T11:00:00+09:00", "timeZone": "Asia/Tokyo"},
        }

        assert summarize_event(event, "会社") == {
            "calendar": "会社",
            "summary": "会議",
            "start": "2026-04-20T10:00:00+09:00",
            "end": "2026-04-20T11:00:00+09:00",
            "all_day": False,
            "status": "confirmed",
        }

    def test_keeps_location_and_description(self) -> None:
        """location / description は値があれば返す。"""
        event = {"summary": "会議", "location": "会議室", "description": "説明"}

        result = summarize_event(event, "会社")

        assert result["location"] == "会議室"
        assert result["description"] == "説明"

    def test_all_day_end_becomes_inclusive(self) -> None:
        """終日予定の end.date（exclusive）を最終日（inclusive）にする。"""
        event = {"summary": "休み", "start": {"date": "2026-04-20"}, "end": {"date": "2026-04-21"}}

        result = summarize_event(event, "会社")

        assert result["all_day"] is True
        assert result["start"] == "2026-04-20"
        assert result["end"] == "2026-04-20"

    def test_invalid_all_day_end_kept_as_is(self) -> None:
        """日付として読めない end.date はそのまま返す。"""
        event = {"start": {"date": "2026-04-20"}, "end": {"date": "broken"}}

        assert summarize_event(event, "会社")["end"] == "broken"

    def test_missing_fields(self) -> None:
        """項目が無い予定でも例外にならない。"""
        assert summarize_event({}, "会社") == {
            "calendar": "会社",
            "summary": "",
            "start": None,
            "end": None,
            "all_day": False,
        }

    def test_does_not_mutate_input(self) -> None:
        """入力の予定を変更しない。"""
        event = {"summary": "会議", "attendees": [{"email": "a@example.com"}]}

        summarize_event(event, "会社")

        assert event == {"summary": "会議", "attendees": [{"email": "a@example.com"}]}


class TestGenericCalendarName:
    """後方互換モードの汎用名。"""

    def test_one_based_index(self) -> None:
        """0 始まりの位置を 1 始まりの番号にする。"""
        assert generic_calendar_name(0) == "calendar_1"
        assert generic_calendar_name(2) == "calendar_3"
