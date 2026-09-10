from datetime import date, datetime, time, timedelta, timezone, tzinfo

# 日本標準時（UTC+9）。カレンダー・ヘルスなど JST 前提の日付処理で共通利用する。
JST = timezone(timedelta(hours=9))


def utc_now() -> datetime:
    """タイムゾーン aware な UTC の現在日時を返す。

    `datetime.now(timezone.utc)` と等価で、MongoDB へ保存する
    タイムスタンプなど UTC 基準の現在時刻が必要な箇所で共通利用する。
    """
    return datetime.now(timezone.utc)


def local_timezone() -> tzinfo:
    """実行環境のローカルタイムゾーン（tzinfo）を返す。

    現在時刻のローカルオフセットに基づく tzinfo を返す。
    `datetime.now().astimezone().tzinfo` と等価。
    """
    return datetime.now().astimezone().tzinfo


def local_now() -> datetime:
    """ローカルタイムゾーン付きの現在日時を返す。

    `datetime.now().astimezone()` と等価で、タイムゾーン aware な現在時刻を返す。
    """
    return datetime.now().astimezone()


def ensure_utc(dt: datetime) -> datetime:
    """naive な datetime を UTC とみなして aware 化する。

    tzinfo を持たない datetime は UTC の tzinfo を付与して返す。
    すでに aware な datetime はタイムゾーン変換を行わずそのまま返す。
    MongoDB から読み出した値など、naive の可能性がある datetime を
    安全に UTC 基準で扱いたい箇所で共通利用する。
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def parse_iso_utc(value: str) -> datetime:
    """ISO 8601 文字列を UTC の aware datetime に変換する。

    末尾の "Z"（Zulu 表記）を許容する。naive な値はローカルタイムとして
    解釈したうえで UTC に変換する（`datetime.fromisoformat(...).astimezone(timezone.utc)`
    と等価）。パースできない値では `ValueError` を送出する。
    """
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def to_jst_date(dt: datetime) -> date:
    """datetime を JST のカレンダー日付（時刻切り捨て）に変換する。

    naive な値は `ensure_utc` で UTC とみなして aware 化したうえで JST へ変換する。
    「何日ごと」の繰り返しのように、時刻ではなく日本時間の日付で比較したい箇所で
    共通利用する（習慣の期限判定など）。

    Args:
        dt: 変換する日時（UTC の aware / naive のいずれでもよい）。

    Returns:
        JST に変換したときのカレンダー日付。
    """
    return ensure_utc(dt).astimezone(JST).date()


def jst_day_end_utc(dt: datetime) -> datetime:
    """datetime の JST 日付の終端（翌日 0:00 JST）を UTC で返す。

    MongoDB のクエリでタイムゾーン付きの日付比較を直接書く代わりに、
    「その JST 日付の終わりまで」を UTC の上限時刻として表現するために使う
    （`{"$lt": jst_day_end_utc(now)}` でその日いっぱいまでを含められる）。

    Args:
        dt: 基準となる日時（UTC の aware / naive のいずれでもよい）。

    Returns:
        JST 日付の翌日 0:00 を UTC に変換した aware datetime。
    """
    next_day = to_jst_date(dt) + timedelta(days=1)
    return datetime.combine(next_day, time.min, tzinfo=JST).astimezone(timezone.utc)
