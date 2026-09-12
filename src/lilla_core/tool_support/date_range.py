"""日付範囲 Value Object。"""
import datetime as dt
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Self

from lilla_core.utils.datetime_utils import local_now

_LAST_N_DAYS_PATTERN = re.compile(r"^last_(\d+)_days$")
_NEXT_N_DAYS_PATTERN = re.compile(r"^next_(\d+)_days$")
_SINGLE_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RANGE_DATE_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})/(\d{4}-\d{2}-\d{2})$")


def _parse_to_dates(date_range: str) -> tuple[date, date]:
    """date_range 文字列を (date_from, date_to) に変換する（内部関数）。

    today / yesterday / tomorrow / last_N_days / next_N_days /
    this_week / last_week / YYYY-MM-DD / YYYY-MM-DD/YYYY-MM-DD 形式をサポートする。
    this_week / last_week は日曜始まり・土曜終わりの週とする。

    相対指定の基準日は `local_timezone()` が解決したタイムゾーン（`ui.timezone`、
    未指定なら OS のローカル）のカレンダー日付で数える。
    """
    today = local_now().date()

    if date_range == "today":
        return today, today

    if date_range == "yesterday":
        y = today - timedelta(days=1)
        return y, y

    if date_range == "tomorrow":
        t = today + timedelta(days=1)
        return t, t

    m = _LAST_N_DAYS_PATTERN.match(date_range)
    if m:
        n = int(m.group(1))
        if n <= 0:
            raise ValueError(f"Number of days must be 1 or more: {date_range}")
        return today - timedelta(days=n - 1), today

    m = _NEXT_N_DAYS_PATTERN.match(date_range)
    if m:
        n = int(m.group(1))
        if n <= 0:
            raise ValueError(f"Number of days must be 1 or more: {date_range}")
        return today, today + timedelta(days=n - 1)

    if date_range == "this_week":
        offset = (today.weekday() + 1) % 7
        week_start = today - timedelta(days=offset)
        return week_start, week_start + timedelta(days=6)

    if date_range == "last_week":
        offset = (today.weekday() + 1) % 7
        this_week_start = today - timedelta(days=offset)
        last_week_start = this_week_start - timedelta(days=7)
        return last_week_start, last_week_start + timedelta(days=6)

    if _SINGLE_DATE_PATTERN.match(date_range):
        d = date.fromisoformat(date_range)
        return d, d

    m = _RANGE_DATE_PATTERN.match(date_range)
    if m:
        d_from = date.fromisoformat(m.group(1))
        d_to = date.fromisoformat(m.group(2))
        if d_from > d_to:
            raise ValueError(f"Start date is after end date: {date_range}")
        return d_from, d_to

    raise ValueError(f"Invalid date_range format: {date_range}")


@dataclass(frozen=True)
class DateRange:
    """日付範囲を表す Value Object。

    parse クラスメソッドで文字列から生成し、
    as_date / as_datetime / as_unixtime で各 API に応じた型で取得する。
    """

    date_from: date
    date_to: date

    @classmethod
    def parse(cls, s: str) -> Self:
        """date_range 文字列を DateRange に変換する。

        Parameters
        ----------
        s : str
            today / yesterday / tomorrow / last_N_days / next_N_days /
            this_week / last_week / YYYY-MM-DD / YYYY-MM-DD/YYYY-MM-DD 形式をサポートする。
            this_week / last_week は日曜始まり・土曜終わりの週とする。

        Raises
        ------
        ValueError
            どの形式にもマッチしない場合
        """
        d_from, d_to = _parse_to_dates(s)
        return cls(d_from, d_to)

    def as_date(self) -> tuple[date, date]:
        """date タプルで返す。（Google Health など）"""
        return self.date_from, self.date_to

    def as_datetime(self) -> tuple[dt.datetime, dt.datetime]:
        """日の始端・終端の datetime タプルで返す。（Google Calendar など）"""
        return (
            dt.datetime.combine(self.date_from, dt.time.min),
            dt.datetime.combine(self.date_to, dt.time.max),
        )

    def as_unixtime(self) -> tuple[int, int]:
        """Unix タイムスタンプのタプルで返す。（外部 API のリクエストパラメータなど）"""
        time_min, time_max = self.as_datetime()
        return int(time_min.timestamp()), int(time_max.timestamp())


@dataclass(frozen=True)
class DateTimeRange:
    """日時範囲を表す Value Object。

    parse クラスメソッドで文字列から生成する。
    日付のみ形式（DateRange と同じ形式）も受け付け、
    その場合は date_from の 00:00:00.000000 〜 date_to の 23:59:59.999999 に変換する。
    日時範囲形式の場合は指定された時刻をそのまま使用する。
    """

    datetime_from: datetime
    datetime_to: datetime

    # dataclass のフィールドではない共有定数（アノテーションを付けない）。
    _DATETIME_RANGE_PATTERN = re.compile(
        r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})/(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})$"
    )

    @classmethod
    def parse(cls, s: str) -> Self:
        """datetime_range 文字列を DateTimeRange に変換する。

        以下の形式をサポートする:
        - today / yesterday / tomorrow / last_N_days / next_N_days /
          this_week / last_week / YYYY-MM-DD / YYYY-MM-DD/YYYY-MM-DD
          → date_from の 00:00:00 〜 date_to の 23:59:59
          （this_week / last_week は日曜始まり・土曜終わりの週とする）
        - YYYY-MM-DD HH:MM:SS/YYYY-MM-DD HH:MM:SS
          → 指定された日時をそのまま使用

        Raises
        ------
        ValueError
            どの形式にもマッチしない場合、または開始日時が終了日時より後の場合
        """
        m = cls._DATETIME_RANGE_PATTERN.match(s)
        if m:
            dt_from = datetime.fromisoformat(m.group(1))
            dt_to = datetime.fromisoformat(m.group(2))
            if dt_from > dt_to:
                raise ValueError(f"Start datetime is after end datetime: {s}")
            return cls(dt_from, dt_to)

        d_from, d_to = _parse_to_dates(s)
        return cls(
            datetime.combine(d_from, dt.time.min),
            datetime.combine(d_to, dt.time.max),
        )

    def as_datetime(self) -> tuple[datetime, datetime]:
        """datetime タプルで返す。"""
        return self.datetime_from, self.datetime_to
