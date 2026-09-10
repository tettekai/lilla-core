from datetime import date, datetime, timedelta, timezone

import pytest

from lilla_core.utils.datetime_utils import (
    JST,
    ensure_utc,
    jst_day_end_utc,
    local_now,
    local_timezone,
    parse_iso_utc,
    to_jst_date,
    utc_now,
)


class TestLocalTimezone:
    """local_timezone のテスト。"""

    def test_returns_tzinfo(self):
        """tzinfo を返し、None ではないこと。"""
        tz = local_timezone()
        assert tz is not None

    def test_matches_system_local_offset(self):
        """システムのローカルオフセットと一致すること。"""
        expected = datetime.now().astimezone().tzinfo
        assert local_timezone().utcoffset(None) == expected.utcoffset(None)


class TestUtcNow:
    """utc_now のテスト。"""

    def test_returns_utc_aware_datetime(self):
        """UTC の tzinfo を持つ aware な datetime を返すこと。"""
        now = utc_now()
        assert isinstance(now, datetime)
        assert now.tzinfo is not None
        assert now.utcoffset() == timezone.utc.utcoffset(None)

    def test_close_to_current_time(self):
        """現在時刻（UTC）に十分近い値を返すこと。"""
        before = datetime.now(timezone.utc)
        now = utc_now()
        after = datetime.now(timezone.utc)
        assert before <= now <= after


class TestLocalNow:
    """local_now のテスト。"""

    def test_returns_timezone_aware_datetime(self):
        """タイムゾーン aware な datetime を返すこと。"""
        now = local_now()
        assert isinstance(now, datetime)
        assert now.tzinfo is not None

    def test_close_to_current_time(self):
        """現在時刻に十分近い値を返すこと。"""
        before = datetime.now().astimezone()
        now = local_now()
        after = datetime.now().astimezone()
        assert before <= now <= after


class TestEnsureUtc:
    """ensure_utc のテスト。"""

    def test_naive_datetime_gets_utc_tzinfo(self):
        """naive な datetime に UTC の tzinfo が付与されること。"""
        naive = datetime(2024, 1, 15, 10, 30, 0)
        result = ensure_utc(naive)
        assert result.tzinfo == timezone.utc
        # 時刻の数値は変換されず、そのまま UTC とみなされること
        assert result.replace(tzinfo=None) == naive

    def test_aware_datetime_is_returned_unchanged(self):
        """aware な datetime はタイムゾーン変換されず、そのまま返ること。"""
        jst = timezone(timedelta(hours=9))
        aware = datetime(2024, 1, 15, 10, 30, 0, tzinfo=jst)
        result = ensure_utc(aware)
        assert result == aware
        assert result.tzinfo == jst


class TestParseIsoUtc:
    """parse_iso_utc のテスト。"""

    def test_parses_offset_datetime_to_utc(self):
        """オフセット付き ISO8601 文字列を UTC の aware datetime に変換すること。"""
        result = parse_iso_utc("2024-01-15T19:30:00+09:00")
        assert result.tzinfo == timezone.utc
        assert result == datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)

    def test_accepts_trailing_z(self):
        """末尾 "Z"（Zulu 表記）を UTC として解釈すること。"""
        result = parse_iso_utc("2024-01-15T10:30:00Z")
        assert result == datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)

    def test_invalid_string_raises_value_error(self):
        """パースできない文字列では ValueError を送出すること。"""
        with pytest.raises(ValueError):
            parse_iso_utc("not-a-date")


class TestJst:
    """JST 定数のテスト。"""

    def test_offset_is_plus_nine_hours(self):
        """UTC+9 のオフセットを持つこと。"""
        assert JST.utcoffset(None) == timedelta(hours=9)


class TestToJstDate:
    """to_jst_date のテスト。"""

    def test_converts_utc_to_jst_calendar_date(self):
        """UTC の日時を JST のカレンダー日付に変換すること。"""
        dt = datetime(2026, 8, 30, 13, 11, 23, 439000, tzinfo=timezone.utc)
        assert to_jst_date(dt) == date(2026, 8, 30)

    def test_crosses_to_next_day_after_jst_midnight(self):
        """UTC 15:00 以降は JST では翌日になること。"""
        dt = datetime(2026, 8, 30, 15, 0, tzinfo=timezone.utc)
        assert to_jst_date(dt) == date(2026, 8, 31)

    def test_utc_14_59_stays_on_same_jst_day(self):
        """UTC 14:59 は JST 23:59 で同じ日付のままであること。"""
        dt = datetime(2026, 8, 30, 14, 59, tzinfo=timezone.utc)
        assert to_jst_date(dt) == date(2026, 8, 30)

    def test_naive_value_is_treated_as_utc(self):
        """naive な datetime は UTC とみなして変換すること。"""
        assert to_jst_date(datetime(2026, 8, 30, 15, 0)) == date(2026, 8, 31)

    def test_aware_non_utc_value_is_converted(self):
        """UTC 以外の aware な datetime も JST へ変換したうえで日付を取ること。"""
        dt = datetime(2026, 8, 30, 20, 0, tzinfo=timezone(timedelta(hours=-5)))
        # UTC では 2026-08-31 01:00 → JST では 2026-08-31 10:00
        assert to_jst_date(dt) == date(2026, 8, 31)


class TestJstDayEndUtc:
    """jst_day_end_utc のテスト。"""

    def test_returns_next_jst_midnight_in_utc(self):
        """JST 日付の翌 0:00（＝UTC の当日 15:00）を返すこと。"""
        dt = datetime(2026, 8, 30, 13, 11, tzinfo=timezone.utc)
        assert jst_day_end_utc(dt) == datetime(2026, 8, 30, 15, 0, tzinfo=timezone.utc)

    def test_is_stable_within_the_same_jst_day(self):
        """同じ JST 日付なら時刻によらず同じ上限を返すこと。"""
        jst_midnight = datetime(2026, 8, 30, 0, 0, tzinfo=JST)
        jst_morning = datetime(2026, 8, 30, 8, 0, tzinfo=JST)
        assert jst_day_end_utc(jst_midnight) == jst_day_end_utc(jst_morning)

    def test_upper_bound_is_exclusive_end_of_jst_day(self):
        """返り値は JST 当日いっぱいを含む排他的上限であること。"""
        upper = jst_day_end_utc(datetime(2026, 8, 30, 0, 0, tzinfo=JST))
        assert datetime(2026, 8, 30, 23, 59, tzinfo=JST) < upper
        assert not datetime(2026, 8, 31, 0, 0, tzinfo=JST) < upper

    def test_returns_utc_aware_datetime(self):
        """UTC の aware な datetime を返すこと。"""
        assert jst_day_end_utc(datetime(2026, 8, 30, 13, 11)).tzinfo == timezone.utc
