"""date_range.py の相対日付が `ui.timezone` に追従することのテスト。

`DateRange` の書式そのもののテストではなく、「今日」の基準がどのタイムゾーンの
カレンダー日付になるかを固定する。
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from lilla_core.tool_support.date_range import DateRange


@pytest.fixture
def app_timezone(monkeypatch):
    """`ui.timezone` を差し替える関数を返す（テスト終了時に元の値へ戻る）。"""
    from lilla_core.core.config import get_config

    def _set(name: str | None) -> None:
        monkeypatch.setattr(get_config().ui, "timezone", name)

    return _set


def _freeze_utc(dt: datetime):
    """`datetime.now(tz)` が指定の UTC 時刻を返すようにするパッチャを返す。"""
    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.astimezone(tz) if tz is not None else dt.replace(tzinfo=None)

    return patch("lilla_core.utils.datetime_utils.datetime", _FrozenDatetime)


class TestRelativeDateBaseTimezone:
    """相対指定の基準日が設定タイムゾーンのカレンダー日付になること。"""

    def test_today_uses_configured_timezone(self, app_timezone):
        """UTC 15:00 は Asia/Tokyo では翌日の "today" になること。"""
        app_timezone("Asia/Tokyo")
        with _freeze_utc(datetime(2026, 8, 30, 15, 0, tzinfo=timezone.utc)):
            r = DateRange.parse("today")
        assert r.date_from == r.date_to
        assert r.date_from.isoformat() == "2026-08-31"

    def test_today_in_utc_stays_on_utc_date(self, app_timezone):
        """同じ時刻でも `ui.timezone: UTC` なら UTC の日付のままであること。"""
        app_timezone("UTC")
        with _freeze_utc(datetime(2026, 8, 30, 15, 0, tzinfo=timezone.utc)):
            r = DateRange.parse("today")
        assert r.date_from.isoformat() == "2026-08-30"

    def test_yesterday_is_relative_to_configured_today(self, app_timezone):
        """"yesterday" も設定タイムゾーンの今日を基準に数えること。"""
        app_timezone("Asia/Tokyo")
        with _freeze_utc(datetime(2026, 8, 30, 15, 0, tzinfo=timezone.utc)):
            r = DateRange.parse("yesterday")
        assert r.date_from.isoformat() == "2026-08-30"

    def test_last_n_days_ends_on_configured_today(self, app_timezone):
        """"last_N_days" の終端が設定タイムゾーンの今日になること。"""
        app_timezone("Asia/Tokyo")
        with _freeze_utc(datetime(2026, 8, 30, 15, 0, tzinfo=timezone.utc)):
            r = DateRange.parse("last_3_days")
        assert r.date_to.isoformat() == "2026-08-31"
        assert r.date_to - r.date_from == timedelta(days=2)

    def test_absolute_date_ignores_timezone(self, app_timezone):
        """絶対日付の指定はタイムゾーン設定に影響されないこと。"""
        app_timezone("UTC")
        r = DateRange.parse("2026-08-30")
        assert r.date_from.isoformat() == "2026-08-30"
        assert r.date_to.isoformat() == "2026-08-30"
