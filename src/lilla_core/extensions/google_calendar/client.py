"""Google Calendar API クライアント。

`google-calendar` 拡張の中身で、OAuth2 のトークン処理は `google-oauth` 拡張が
提供する `GoogleOAuthClient` に委譲する。この拡張が持つのは Calendar 固有の
`CREDENTIAL_TYPE` / `SCOPES` と Events API の呼び出しだけ。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache

from lilla_core.extensions.google_oauth.client import GoogleOAuthClient
from lilla_core.utils.datetime_utils import local_timezone


#: Google Calendar スコープ（予定の読み書きに必要な最小権限。
#: calendar / calendar.readonly は使わない）。
SCOPES_CALENDAR = ["https://www.googleapis.com/auth/calendar.events"]


@lru_cache(maxsize=1)
def get_google_calendar_client() -> "GoogleCalendarClient":
    """GoogleCalendarClient のシングルトンインスタンスを返す。

    初回呼び出し時に設定を読み込んでインスタンスを生成し、以降は同じインスタンスを返す。

    Returns
    -------
    GoogleCalendarClient
        GoogleCalendarClient のインスタンス
    """
    return GoogleCalendarClient.from_config()


_GOOGLE_CALENDAR_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/{calendarId}/events"


class GoogleCalendarClient(GoogleOAuthClient):
    """Google Calendar API クライアント。

    OAuth2 トークンの管理と Calendar Events API 呼び出しを行う。
    トークン取得・認証フローは基底クラス ``GoogleOAuthClient`` が担う。
    """

    CREDENTIAL_TYPE = "google_calendar"
    SCOPES = SCOPES_CALENDAR

    def _to_rfc3339(self, dt: datetime) -> str:
        """Google Calendar API用にRFC3339形式（タイムゾーン付き）に変換する。

        naive datetimeの場合、``local_timezone()``（``ui.timezone``）のタイムゾーン
        として扱う。
        """
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=local_timezone())
        return dt.isoformat(timespec="microseconds")

    def _resolve_timezone_name(self) -> str:
        """時間指定イベントの ``timeZone`` に使う IANA タイムゾーン名を解決する。

        ``ui.timezone`` の設定値を優先し、無ければ ``local_timezone()`` の戻り値
        から IANA 名（``key``）を取り出す。``local_timezone()`` は ``ui.timezone``
        未指定時に OS の ``tzinfo`` へフォールバックし ``key`` を持たないため、
        その場合はここで分かりやすいエラーにする。

        Raises
        ------
        ValueError
            IANA タイムゾーン名を解決できなかった場合
        """
        from lilla_core.core.config import get_config

        timezone_name = get_config().ui.timezone or getattr(local_timezone(), "key", None)
        if timezone_name is None:
            raise ValueError(
                "Could not resolve an IANA time zone name; set ui.timezone in lilla.yaml"
            )
        return timezone_name

    async def get_events(
        self,
        calendar_ids: list[str],
        time_min: datetime,
        time_max: datetime,
        max_results: int = 50,
        q: str | None = None,
    ) -> list[dict]:
        """指定カレンダー群のイベントを取得し、startTime 昇順にマージして返す。

        calendar_ids をループし、カレンダーごとに 1 回 API を呼ぶ。
        各カレンダーの結果をマージ後、start.dateTime（または start.date）で昇順ソートして返す。

        Parameters
        ----------
        calendar_ids : list[str]
            取得対象のカレンダー ID リスト
        time_min : datetime
            取得開始日時
        time_max : datetime
            取得終了日時
        max_results : int, optional
            各カレンダーの最大取得件数（デフォルト: 50）
        q : str | None, optional
            全文検索クエリ（None の場合は検索しない）

        Returns
        -------
        list[dict]
            start.dateTime（または start.date）昇順にソートされたイベントのリスト

        Raises
        ------
        ReauthenticationRequiredError
            トークンの更新に失敗した場合（再認証が必要）
        """
        headers = await self._auth_headers()

        params: dict = {
            "timeMin": self._to_rfc3339(time_min),
            "timeMax": self._to_rfc3339(time_max),
            "maxResults": str(max_results),
            "singleEvents": "true",
            "orderBy": "startTime",
        }
        if q is not None:
            params["q"] = q

        all_events: list[dict] = []

        for calendar_id in calendar_ids:
            url = _GOOGLE_CALENDAR_EVENTS_URL.format(calendarId=calendar_id)
            # ループ内で同じ認証ヘッダを使い回し、トークンの再取得を避ける。
            result = await self._request_json(url, params=params, headers=headers)
            all_events.extend(result.get("items", []))

        def _sort_key(event: dict) -> str:
            start = event.get("start", {})
            return start.get("dateTime") or start.get("date") or ""

        all_events.sort(key=_sort_key)
        return all_events

    async def create_event(
        self,
        calendar_id: str,
        summary: str,
        *,
        all_day: bool,
        start: str | datetime,
        end: str | datetime,
        description: str | None = None,
        location: str | None = None,
    ) -> dict:
        """指定カレンダーへ新規イベントを作成する。

        終日イベントは ``start`` / ``end`` を ``YYYY-MM-DD`` 形式の文字列（どちらも
        利用者から見た最終日を含む・inclusive）で受け取る。Calendar API の
        ``end.date`` は exclusive のため、本メソッドが ``end`` の翌日を算出して送る
        （呼び出し元で変換する必要はない）。時間指定イベントは aware/naive な
        ``datetime`` を受け取り、``timeZone`` は ``ui.timezone`` から解決した
        IANA タイムゾーン名を使う。

        Parameters
        ----------
        calendar_id : str
            書き込み先カレンダーの ID
        summary : str
            イベントのタイトル
        all_day : bool
            終日イベントかどうか
        start : str | datetime
            開始日（終日）または開始日時（時間指定）
        end : str | datetime
            終了日（終日。inclusive）または終了日時（時間指定）
        description : str | None, optional
            説明文
        location : str | None, optional
            場所

        Returns
        -------
        dict
            作成されたイベントの情報（``htmlLink`` を含む）

        Raises
        ------
        ReauthenticationRequiredError
            トークンの取得または更新に失敗した場合
        ValueError
            IANA タイムゾーン名を解決できなかった場合（時間指定イベントのみ）
        """
        body: dict = {"summary": summary}
        if description:
            body["description"] = description
        if location:
            body["location"] = location

        if all_day:
            start_date = date.fromisoformat(start)
            end_date = date.fromisoformat(end) + timedelta(days=1)
            body["start"] = {"date": start_date.isoformat()}
            body["end"] = {"date": end_date.isoformat()}
        else:
            timezone_name = self._resolve_timezone_name()
            body["start"] = {"dateTime": self._to_rfc3339(start), "timeZone": timezone_name}
            body["end"] = {"dateTime": self._to_rfc3339(end), "timeZone": timezone_name}

        url = _GOOGLE_CALENDAR_EVENTS_URL.format(calendarId=calendar_id)
        return await self._request_json(url, method="POST", data=body, json_body=True)
