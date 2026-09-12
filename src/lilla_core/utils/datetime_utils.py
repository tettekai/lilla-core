from datetime import date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

# 日本標準時（UTC+9）。`ui.timezone` の設定とは独立した固定値で、日本時間での
# 表示・変換が必要な箇所（ホスト側のツールなど）が明示的に使うための定数。
JST = timezone(timedelta(hours=9))


def utc_now() -> datetime:
    """タイムゾーン aware な UTC の現在日時を返す。

    `datetime.now(timezone.utc)` と等価で、MongoDB へ保存する
    タイムスタンプなど UTC 基準の現在時刻が必要な箇所で共通利用する。
    """
    return datetime.now(timezone.utc)


def local_timezone() -> tzinfo:
    """アプリが「人間側の今日 / いま」として使うタイムゾーンを返す。

    呼び出しのたびに設定から解決するため、`set_config()` による差し替えも反映される。
    解決規則は次のとおり:

    - `ui.timezone` が未指定 → OS のローカルタイムゾーン
      （`datetime.now().astimezone().tzinfo` と等価）
    - `ui.timezone` が文字列 → `ZoneInfo(その値)`。OS のタイムゾーンは見ない

    受け付けられない名前や空文字は `UiConfig` のバリデーションで起動時に弾かれるため、
    ここでフォールバックすることはない。
    """
    from lilla_core.core.config import get_config

    name = get_config().ui.timezone
    if name is None:
        return datetime.now().astimezone().tzinfo
    return ZoneInfo(name)


def local_now() -> datetime:
    """`local_timezone()` のタイムゾーン付きの現在日時を返す。

    `ui.timezone` を指定していればそのタイムゾーン、未指定なら OS の
    ローカルタイムゾーンでの現在時刻を返す。
    """
    return datetime.now(local_timezone())


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
    """datetime を `local_timezone()` のカレンダー日付（時刻切り捨て）に変換する。

    naive な値は `ensure_utc` で UTC とみなして aware 化したうえで変換する。
    「何日ごと」の繰り返しのように、時刻ではなくカレンダー日付で比較したい箇所で
    共通利用する（習慣の期限判定など）。

    NOTE: 関数名は互換のため残しているが、変換先は `JST` 固定ではなく
    `ui.timezone` で解決したタイムゾーンになる（未指定なら OS のローカル）。

    Args:
        dt: 変換する日時（UTC の aware / naive のいずれでもよい）。

    Returns:
        解決したタイムゾーンに変換したときのカレンダー日付。
    """
    return ensure_utc(dt).astimezone(local_timezone()).date()


def jst_day_end_utc(dt: datetime) -> datetime:
    """datetime のカレンダー日付の終端（翌日 0:00）を UTC で返す。

    日付は `local_timezone()` で解決したタイムゾーンで数える。MongoDB のクエリで
    タイムゾーン付きの日付比較を直接書く代わりに、「その日付の終わりまで」を UTC の
    上限時刻として表現するために使う
    （`{"$lt": jst_day_end_utc(now)}` でその日いっぱいまでを含められる）。

    NOTE: 関数名は互換のため残しているが、基準は `JST` 固定ではなく
    `ui.timezone` で解決したタイムゾーンになる（未指定なら OS のローカル）。

    Args:
        dt: 基準となる日時（UTC の aware / naive のいずれでもよい）。

    Returns:
        解決したタイムゾーンでの翌日 0:00 を UTC に変換した aware datetime。
    """
    tz = local_timezone()
    next_day = ensure_utc(dt).astimezone(tz).date() + timedelta(days=1)
    return datetime.combine(next_day, time.min, tzinfo=tz).astimezone(timezone.utc)
