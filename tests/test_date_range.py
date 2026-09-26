"""date_range.py（`DateRange` / `DateTimeRange`）のテスト。

相対指定の「今日」は `local_timezone()`（`ui.timezone`）のカレンダー日付で決まるため、
実行時刻・OS のタイムゾーンによって結果が変わらないよう、すべての相対指定のテストは
`app_timezone` で `ui.timezone` を、`_freeze_utc()` で現在時刻を固定して期待値を書く。
"""
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import patch

import pytest

from lilla_core.tool_support.date_range import DateRange, DateTimeRange


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


# JST の「今日」を 2026-08-31（月曜）に固定するための UTC 時刻。
# 2026-08-30 15:00 UTC = 2026-08-31 00:00 JST。
_MONDAY_JST = datetime(2026, 8, 30, 15, 0, tzinfo=timezone.utc)


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

    def test_date_changes_across_jst_boundary(self, app_timezone):
        """UTC 15:00 の境界の前後で Asia/Tokyo の「今日」が 1 日進むこと。"""
        app_timezone("Asia/Tokyo")
        with _freeze_utc(datetime(2026, 8, 30, 14, 59, 59, tzinfo=timezone.utc)):
            before = DateRange.parse("today")
        with _freeze_utc(datetime(2026, 8, 30, 15, 0, 0, tzinfo=timezone.utc)):
            after = DateRange.parse("today")
        assert before.date_from.isoformat() == "2026-08-30"
        assert after.date_from.isoformat() == "2026-08-31"


class TestDateRangeRelativeKeywords:
    """`today` / `yesterday` / `tomorrow` の解釈。"""

    def test_today(self, app_timezone):
        """"today" が固定した今日の 1 日分になること。"""
        app_timezone("Asia/Tokyo")
        with _freeze_utc(_MONDAY_JST):
            r = DateRange.parse("today")
        assert r.date_from.isoformat() == "2026-08-31"
        assert r.date_to.isoformat() == "2026-08-31"

    def test_yesterday(self, app_timezone):
        """"yesterday" が前日の 1 日分になること。"""
        app_timezone("Asia/Tokyo")
        with _freeze_utc(_MONDAY_JST):
            r = DateRange.parse("yesterday")
        assert r.date_from.isoformat() == "2026-08-30"
        assert r.date_to.isoformat() == "2026-08-30"

    def test_tomorrow(self, app_timezone):
        """"tomorrow" が翌日の 1 日分になること。"""
        app_timezone("Asia/Tokyo")
        with _freeze_utc(_MONDAY_JST):
            r = DateRange.parse("tomorrow")
        assert r.date_from.isoformat() == "2026-09-01"
        assert r.date_to.isoformat() == "2026-09-01"

    def test_tomorrow_crosses_month_boundary(self, app_timezone):
        """"tomorrow" が月末をまたいで翌月 1 日になること。"""
        app_timezone("Asia/Tokyo")
        # JST の今日は 8 月末日（2026-08-31）なので "tomorrow" は翌月へ入る
        with _freeze_utc(_MONDAY_JST):
            r = DateRange.parse("tomorrow")
        assert r.date_from.isoformat() == "2026-09-01"


class TestDateRangeNDays:
    """`last_N_days` / `next_N_days` の解釈。"""

    def test_last_n_days_includes_today(self, app_timezone):
        """"last_3_days" が今日を含む直近 3 日間になること。"""
        app_timezone("Asia/Tokyo")
        with _freeze_utc(_MONDAY_JST):
            r = DateRange.parse("last_3_days")
        assert r.date_from.isoformat() == "2026-08-29"
        assert r.date_to.isoformat() == "2026-08-31"

    def test_last_1_days_is_today_only(self, app_timezone):
        """"last_1_days" が今日 1 日だけになること。"""
        app_timezone("Asia/Tokyo")
        with _freeze_utc(_MONDAY_JST):
            r = DateRange.parse("last_1_days")
        assert r.date_from.isoformat() == "2026-08-31"
        assert r.date_to.isoformat() == "2026-08-31"

    def test_next_n_days_starts_today(self, app_timezone):
        """"next_3_days" が今日を含む先 3 日間になること。"""
        app_timezone("Asia/Tokyo")
        with _freeze_utc(_MONDAY_JST):
            r = DateRange.parse("next_3_days")
        assert r.date_from.isoformat() == "2026-08-31"
        assert r.date_to.isoformat() == "2026-09-02"

    def test_next_1_days_is_today_only(self, app_timezone):
        """"next_1_days" が今日 1 日だけになること。"""
        app_timezone("Asia/Tokyo")
        with _freeze_utc(_MONDAY_JST):
            r = DateRange.parse("next_1_days")
        assert r.date_from.isoformat() == "2026-08-31"
        assert r.date_to.isoformat() == "2026-08-31"

    def test_last_n_days_crosses_month_boundary(self, app_timezone):
        """"last_5_days" が月初をまたいで前月末まで遡ること。"""
        app_timezone("Asia/Tokyo")
        # 2026-09-01 15:00 UTC = 2026-09-02 00:00 JST
        with _freeze_utc(datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)):
            r = DateRange.parse("last_5_days")
        assert r.date_from.isoformat() == "2026-08-29"
        assert r.date_to.isoformat() == "2026-09-02"

    def test_last_zero_days_raises(self):
        """"last_0_days" は ValueError になること。"""
        with pytest.raises(ValueError, match="Number of days must be 1 or more"):
            DateRange.parse("last_0_days")

    def test_next_zero_days_raises(self):
        """"next_0_days" は ValueError になること。"""
        with pytest.raises(ValueError, match="Number of days must be 1 or more"):
            DateRange.parse("next_0_days")


class TestDateRangeWeek:
    """`this_week` / `last_week` は日曜始まり・土曜終わりであること。"""

    def test_this_week_on_monday(self, app_timezone):
        """週の途中（月曜）でも前の日曜から次の土曜までになること。"""
        app_timezone("Asia/Tokyo")
        with _freeze_utc(_MONDAY_JST):
            r = DateRange.parse("this_week")
        assert r.date_from.isoformat() == "2026-08-30"
        assert r.date_to.isoformat() == "2026-09-05"

    def test_this_week_on_sunday_starts_today(self, app_timezone):
        """今日が日曜なら週の始端が今日になること（境界）。"""
        app_timezone("Asia/Tokyo")
        # 2026-08-29 15:00 UTC = 2026-08-30 00:00 JST（日曜）
        with _freeze_utc(datetime(2026, 8, 29, 15, 0, tzinfo=timezone.utc)):
            r = DateRange.parse("this_week")
        assert r.date_from.isoformat() == "2026-08-30"
        assert r.date_to.isoformat() == "2026-09-05"

    def test_this_week_on_saturday_ends_today(self, app_timezone):
        """今日が土曜なら週の終端が今日になること（境界）。"""
        app_timezone("Asia/Tokyo")
        # 2026-09-04 15:00 UTC = 2026-09-05 00:00 JST（土曜）
        with _freeze_utc(datetime(2026, 9, 4, 15, 0, tzinfo=timezone.utc)):
            r = DateRange.parse("this_week")
        assert r.date_from.isoformat() == "2026-08-30"
        assert r.date_to.isoformat() == "2026-09-05"

    def test_this_week_spans_seven_days(self, app_timezone):
        """週の範囲がちょうど 7 日分（差は 6 日）であること。"""
        app_timezone("Asia/Tokyo")
        with _freeze_utc(_MONDAY_JST):
            r = DateRange.parse("this_week")
        assert r.date_to - r.date_from == timedelta(days=6)

    def test_last_week_on_monday(self, app_timezone):
        """"last_week" が前週の日曜〜土曜になること。"""
        app_timezone("Asia/Tokyo")
        with _freeze_utc(_MONDAY_JST):
            r = DateRange.parse("last_week")
        assert r.date_from.isoformat() == "2026-08-23"
        assert r.date_to.isoformat() == "2026-08-29"

    def test_last_week_on_sunday(self, app_timezone):
        """今日が日曜のときの "last_week" が 1 週前の日曜〜土曜になること（境界）。"""
        app_timezone("Asia/Tokyo")
        with _freeze_utc(datetime(2026, 8, 29, 15, 0, tzinfo=timezone.utc)):
            r = DateRange.parse("last_week")
        assert r.date_from.isoformat() == "2026-08-23"
        assert r.date_to.isoformat() == "2026-08-29"

    def test_last_week_ends_just_before_this_week(self, app_timezone):
        """"last_week" の終端の翌日が "this_week" の始端になること。"""
        app_timezone("Asia/Tokyo")
        with _freeze_utc(_MONDAY_JST):
            last = DateRange.parse("last_week")
            this = DateRange.parse("this_week")
        assert last.date_to + timedelta(days=1) == this.date_from


class TestDateRangeAbsolute:
    """絶対日付（単日・範囲）の解釈。"""

    def test_single_date(self):
        """単一日付はその日の 1 日分になること。"""
        r = DateRange.parse("2026-02-28")
        assert r.date_from.isoformat() == "2026-02-28"
        assert r.date_to.isoformat() == "2026-02-28"

    def test_date_range(self):
        """範囲指定が始端・終端そのままになること。"""
        r = DateRange.parse("2026-08-01/2026-08-31")
        assert r.date_from.isoformat() == "2026-08-01"
        assert r.date_to.isoformat() == "2026-08-31"

    def test_date_range_same_day_is_allowed(self):
        """始端と終端が同じ日の範囲指定は許容されること。"""
        r = DateRange.parse("2026-08-01/2026-08-01")
        assert r.date_from == r.date_to

    def test_date_range_reversed_raises(self):
        """始端が終端より後の範囲指定は ValueError になること。"""
        with pytest.raises(ValueError, match="Start date is after end date"):
            DateRange.parse("2026-08-31/2026-08-01")

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "tommorow",
            "last_days",
            "last_N_days",
            "2026/08/31",
            "2026-8-31",
            "20260831",
            "2026-08-31/",
            "2026-08-31/2026-09",
            "this_month",
            "TODAY",
        ],
    )
    def test_invalid_format_raises(self, value):
        """どの形式にもマッチしない文字列は ValueError になること。"""
        with pytest.raises(ValueError, match="Invalid date_range format"):
            DateRange.parse(value)


class TestDateRangeConversions:
    """`as_date` / `as_datetime` / `as_unixtime` の変換。"""

    def test_as_date_returns_date_tuple(self):
        """as_date が (date, date) を返すこと。"""
        r = DateRange.parse("2026-08-01/2026-08-31")
        date_from, date_to = r.as_date()
        assert (date_from, date_to) == (r.date_from, r.date_to)
        assert isinstance(date_from, date)

    def test_as_datetime_spans_whole_days(self):
        """as_datetime が日の始端 00:00 と終端 23:59:59.999999 になること。"""
        r = DateRange.parse("2026-08-01/2026-08-31")
        dt_from, dt_to = r.as_datetime()
        assert dt_from == datetime(2026, 8, 1, 0, 0, 0)
        assert dt_to == datetime(2026, 8, 31, 23, 59, 59, 999999)

    def test_as_datetime_is_naive(self):
        """as_datetime の戻り値が naive（tzinfo なし）であること。"""
        dt_from, dt_to = DateRange.parse("2026-08-01").as_datetime()
        assert dt_from.tzinfo is None
        assert dt_to.tzinfo is None

    def test_as_unixtime_matches_naive_local_datetime(self):
        """as_unixtime が as_datetime のローカル解釈と一致すること。"""
        r = DateRange.parse("2026-08-01/2026-08-31")
        unix_from, unix_to = r.as_unixtime()
        assert unix_from == int(datetime(2026, 8, 1, 0, 0, 0).timestamp())
        assert unix_to == int(
            datetime(2026, 8, 31, 23, 59, 59, 999999).timestamp()
        )

    def test_as_unixtime_single_day_spans_one_day(self):
        """単日の as_unixtime が 1 日分（86399 秒差）になること。"""
        unix_from, unix_to = DateRange.parse("2026-08-01").as_unixtime()
        assert unix_to - unix_from == 86399

    def test_is_frozen(self):
        """DateRange が frozen dataclass で書き換えられないこと。"""
        r = DateRange.parse("2026-08-01")
        with pytest.raises(Exception):
            r.date_from = date(2026, 1, 1)


class TestDateTimeRange:
    """`DateTimeRange` の解釈。"""

    def test_datetime_range_keeps_specified_times(self):
        """日時範囲形式は指定された時刻をそのまま使うこと。"""
        r = DateTimeRange.parse("2026-08-01 09:30:00/2026-08-01 18:45:10")
        assert r.datetime_from == datetime(2026, 8, 1, 9, 30, 0)
        assert r.datetime_to == datetime(2026, 8, 1, 18, 45, 10)

    def test_datetime_range_across_days(self):
        """日をまたぐ日時範囲も指定どおりになること。"""
        r = DateTimeRange.parse("2026-08-01 23:00:00/2026-08-02 01:00:00")
        assert r.datetime_from == datetime(2026, 8, 1, 23, 0, 0)
        assert r.datetime_to == datetime(2026, 8, 2, 1, 0, 0)

    def test_datetime_range_same_moment_is_allowed(self):
        """始端と終端が同じ日時の範囲は許容されること。"""
        r = DateTimeRange.parse("2026-08-01 09:00:00/2026-08-01 09:00:00")
        assert r.datetime_from == r.datetime_to

    def test_datetime_range_reversed_raises(self):
        """始端が終端より後の日時範囲は ValueError になること。"""
        with pytest.raises(ValueError, match="Start datetime is after end datetime"):
            DateTimeRange.parse("2026-08-01 18:00:00/2026-08-01 09:00:00")

    def test_single_date_becomes_whole_day(self):
        """日付のみ形式は その日の 00:00:00 〜 23:59:59.999999 になること。"""
        r = DateTimeRange.parse("2026-08-01")
        assert r.datetime_from == datetime(2026, 8, 1, 0, 0, 0)
        assert r.datetime_to == datetime(2026, 8, 1, 23, 59, 59, 999999)

    def test_date_range_becomes_whole_days(self):
        """日付範囲形式は始端の 00:00 〜 終端の 23:59:59.999999 になること。"""
        r = DateTimeRange.parse("2026-08-01/2026-08-31")
        assert r.datetime_from == datetime(2026, 8, 1, 0, 0, 0)
        assert r.datetime_to == datetime(2026, 8, 31, 23, 59, 59, 999999)

    def test_today_uses_configured_timezone(self, app_timezone):
        """相対指定も設定タイムゾーンの今日を基準に丸 1 日へ広げること。"""
        app_timezone("Asia/Tokyo")
        with _freeze_utc(_MONDAY_JST):
            r = DateTimeRange.parse("today")
        assert r.datetime_from == datetime(2026, 8, 31, 0, 0, 0)
        assert r.datetime_to == datetime(2026, 8, 31, 23, 59, 59, 999999)

    def test_yesterday_uses_configured_timezone(self, app_timezone):
        """"yesterday" も設定タイムゾーンの今日を基準にすること。"""
        app_timezone("Asia/Tokyo")
        with _freeze_utc(_MONDAY_JST):
            r = DateTimeRange.parse("yesterday")
        assert r.datetime_from == datetime(2026, 8, 30, 0, 0, 0)
        assert r.datetime_to == datetime(2026, 8, 30, 23, 59, 59, 999999)

    def test_this_week_is_sunday_to_saturday(self, app_timezone):
        """"this_week" が日曜 00:00 〜 土曜 23:59:59.999999 になること。"""
        app_timezone("Asia/Tokyo")
        with _freeze_utc(_MONDAY_JST):
            r = DateTimeRange.parse("this_week")
        assert r.datetime_from == datetime(2026, 8, 30, 0, 0, 0)
        assert r.datetime_to == datetime(2026, 9, 5, 23, 59, 59, 999999)

    def test_date_boundary_follows_configured_timezone(self, app_timezone):
        """UTC 15:00 の境界の前後で「今日」の丸 1 日が 1 日ずれること。"""
        app_timezone("Asia/Tokyo")
        with _freeze_utc(datetime(2026, 8, 30, 14, 59, 59, tzinfo=timezone.utc)):
            before = DateTimeRange.parse("today")
        with _freeze_utc(datetime(2026, 8, 30, 15, 0, 0, tzinfo=timezone.utc)):
            after = DateTimeRange.parse("today")
        assert before.datetime_from == datetime(2026, 8, 30, 0, 0, 0)
        assert after.datetime_from == datetime(2026, 8, 31, 0, 0, 0)

    def test_as_datetime_returns_both_ends(self):
        """as_datetime が (datetime_from, datetime_to) を返すこと。"""
        r = DateTimeRange.parse("2026-08-01 09:00:00/2026-08-01 10:00:00")
        assert r.as_datetime() == (r.datetime_from, r.datetime_to)

    @pytest.mark.parametrize(
        "value",
        [
            "2026-08-01 09:00/2026-08-01 10:00",
            "2026-08-01 09:00:00",
            "2026-08-01T09:00:00/2026-08-01T10:00:00",
            "2026-08-01 09:00:00/",
            "next_0_days_range",
        ],
    )
    def test_invalid_format_raises(self, value):
        """日時範囲としても日付範囲としても解釈できない文字列は ValueError になること。"""
        with pytest.raises(ValueError):
            DateTimeRange.parse(value)

    def test_is_frozen(self):
        """DateTimeRange が frozen dataclass で書き換えられないこと。"""
        r = DateTimeRange.parse("2026-08-01")
        with pytest.raises(Exception):
            r.datetime_from = datetime(2026, 1, 1)

    def test_day_end_uses_time_max(self):
        """日付のみ形式の終端が `time.max` と一致すること。"""
        r = DateTimeRange.parse("2026-08-01")
        assert r.datetime_to.time() == time.max
