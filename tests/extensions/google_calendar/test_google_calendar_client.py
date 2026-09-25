"""`lilla_core.extensions.google_calendar.client` の `GoogleCalendarClient` のテスト。"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

import lilla_core.extensions.google_calendar.client as google_calendar_client_module
from lilla_core.extensions.google_calendar.client import SCOPES_CALENDAR, GoogleCalendarClient


def _make_client() -> GoogleCalendarClient:
    """`_request_json` をモックした GoogleCalendarClient を生成する。"""
    client = GoogleCalendarClient(
        MagicMock(), "client-id", "client-secret", "http://localhost/google-callback"
    )
    client._request_json = AsyncMock(
        return_value={"id": "evt1", "htmlLink": "https://calendar.google.com/event?eid=evt1"}
    )
    return client


def _mock_get_config(monkeypatch, *, ui_timezone: str | None):
    """`lilla_core.core.config.get_config` をモックし、``ui.timezone`` を差し替える。

    _resolve_timezone_name() は関数内 lazy import (`from lilla_core.core.config
    import get_config`) のため、本物のモジュールの属性を差し替えれば足りる。
    """
    from lilla_core.core import config as config_module

    config = MagicMock()
    config.ui.timezone = ui_timezone
    monkeypatch.setattr(config_module, "get_config", MagicMock(return_value=config))
    return config


class TestCreateEventAllDay:
    """終日イベントの日付の組み立て。"""

    async def test_single_day_sets_end_to_next_day(self) -> None:
        """終日1日のイベントでは end.date が翌日になる。"""
        client = _make_client()

        await client.create_event(
            "primary", "予定", all_day=True, start="2026-04-20", end="2026-04-20"
        )

        body = client._request_json.call_args.kwargs["data"]
        assert body["start"] == {"date": "2026-04-20"}
        assert body["end"] == {"date": "2026-04-21"}

    async def test_rejects_datetime_start(self) -> None:
        """終日イベントで start に日時文字列（YYYY-MM-DD 以外）を渡すと ValueError になる。

        レビュー指摘: end だけ date.fromisoformat で検証していて start は無検証だった。
        """
        client = _make_client()

        with pytest.raises(ValueError):
            await client.create_event(
                "primary", "予定", all_day=True, start="2026-04-20T10:00:00", end="2026-04-20"
            )

    async def test_multi_day_end_is_exclusive_next_day(self) -> None:
        """終日の複数日イベントでは end.date が指定最終日の翌日になる。"""
        client = _make_client()

        await client.create_event(
            "primary", "旅行", all_day=True, start="2026-04-20", end="2026-04-22"
        )

        body = client._request_json.call_args.kwargs["data"]
        assert body["start"] == {"date": "2026-04-20"}
        assert body["end"] == {"date": "2026-04-23"}


class TestCreateEventTimezone:
    """時間指定イベントのタイムゾーン解決（`ui.timezone`）。"""

    async def test_uses_resolved_timezone_name(self, monkeypatch) -> None:
        """時間指定イベントの timeZone が local_timezone() の IANA 名になる。"""
        _mock_get_config(monkeypatch, ui_timezone=None)
        monkeypatch.setattr(
            google_calendar_client_module, "local_timezone", lambda: ZoneInfo("Asia/Tokyo")
        )
        client = _make_client()
        start = datetime(2026, 4, 20, 10, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        end = datetime(2026, 4, 20, 11, 0, tzinfo=ZoneInfo("Asia/Tokyo"))

        await client.create_event("primary", "会議", all_day=False, start=start, end=end)

        body = client._request_json.call_args.kwargs["data"]
        assert body["start"]["timeZone"] == "Asia/Tokyo"
        assert body["end"]["timeZone"] == "Asia/Tokyo"
        assert body["start"]["dateTime"].startswith("2026-04-20T10:00:00")
        assert body["end"]["dateTime"].startswith("2026-04-20T11:00:00")

    async def test_prefers_ui_timezone_over_local_timezone_key(self, monkeypatch) -> None:
        """timeZone は local_timezone().key より先に ui.timezone の値を使う。"""
        _mock_get_config(monkeypatch, ui_timezone="Asia/Tokyo")
        # OS の tzinfo フォールバックを模して、.key を持たない tzinfo を返す。
        monkeypatch.setattr(
            google_calendar_client_module, "local_timezone", lambda: timezone.utc
        )
        client = _make_client()
        start = datetime(2026, 4, 20, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 4, 20, 11, 0, tzinfo=timezone.utc)

        await client.create_event("primary", "会議", all_day=False, start=start, end=end)

        body = client._request_json.call_args.kwargs["data"]
        assert body["start"]["timeZone"] == "Asia/Tokyo"
        assert body["end"]["timeZone"] == "Asia/Tokyo"

    async def test_raises_clear_error_when_timezone_unresolvable(self, monkeypatch) -> None:
        """ui.timezone 未設定かつ local_timezone() が IANA 名を持たない場合は ValueError になる。

        レビュー指摘: 未修正のままだと `local_timezone().key` で AttributeError になっていた。
        """
        _mock_get_config(monkeypatch, ui_timezone=None)
        monkeypatch.setattr(
            google_calendar_client_module, "local_timezone", lambda: timezone.utc
        )
        client = _make_client()
        start = datetime(2026, 4, 20, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 4, 20, 11, 0, tzinfo=timezone.utc)

        with pytest.raises(ValueError, match="ui.timezone"):
            await client.create_event("primary", "会議", all_day=False, start=start, end=end)

    async def test_naive_datetime_uses_local_timezone_not_fixed_jst(self, monkeypatch) -> None:
        """naive datetime は固定の JST ではなく local_timezone() のオフセットで解釈される。"""
        _mock_get_config(monkeypatch, ui_timezone="Pacific/Auckland")
        # NZDT (+13:00)。修正前は JST (+09:00) 固定で塗られていた。
        nzdt = ZoneInfo("Pacific/Auckland")
        monkeypatch.setattr(google_calendar_client_module, "local_timezone", lambda: nzdt)
        client = _make_client()
        start = datetime(2026, 1, 20, 10, 0)  # naive
        end = datetime(2026, 1, 20, 11, 0)  # naive

        await client.create_event("primary", "会議", all_day=False, start=start, end=end)

        body = client._request_json.call_args.kwargs["data"]
        assert body["start"]["dateTime"] == "2026-01-20T10:00:00.000000+13:00"
        assert body["end"]["dateTime"] == "2026-01-20T11:00:00.000000+13:00"
        assert body["start"]["timeZone"] == "Pacific/Auckland"


class TestCreateEventRequest:
    """送信するリクエストとレスポンスの扱い。"""

    async def test_posts_with_post_method(self) -> None:
        """create_event が POST メソッドで _request_json を呼ぶ。"""
        client = _make_client()

        await client.create_event(
            "primary", "予定", all_day=True, start="2026-04-20", end="2026-04-20"
        )

        kwargs = client._request_json.call_args.kwargs
        assert kwargs["method"] == "POST"
        assert kwargs["json_body"] is True

    async def test_includes_optional_fields(self) -> None:
        """description / location を指定すると body に含まれる。"""
        client = _make_client()

        await client.create_event(
            "primary",
            "予定",
            all_day=True,
            start="2026-04-20",
            end="2026-04-20",
            description="説明文",
            location="会議室A",
        )

        body = client._request_json.call_args.kwargs["data"]
        assert body["description"] == "説明文"
        assert body["location"] == "会議室A"

    async def test_omits_optional_fields_when_not_given(self) -> None:
        """description / location 未指定なら body に含まれない。"""
        client = _make_client()

        await client.create_event(
            "primary", "予定", all_day=True, start="2026-04-20", end="2026-04-20"
        )

        body = client._request_json.call_args.kwargs["data"]
        assert "description" not in body
        assert "location" not in body

    async def test_returns_response_with_html_link(self) -> None:
        """作成レスポンスの htmlLink がそのまま返る。"""
        client = _make_client()

        result = await client.create_event(
            "primary", "予定", all_day=True, start="2026-04-20", end="2026-04-20"
        )

        assert result["htmlLink"] == "https://calendar.google.com/event?eid=evt1"


class TestScopes:
    """拡張が自分で持つスコープ定数。"""

    def test_scopes_calendar_is_events_only(self) -> None:
        """SCOPES_CALENDAR が calendar.events のみである。"""
        assert SCOPES_CALENDAR == ["https://www.googleapis.com/auth/calendar.events"]
        assert GoogleCalendarClient.SCOPES == SCOPES_CALENDAR

    def test_credential_type_is_google_calendar(self) -> None:
        """`/oauth/google-oauth/callback` が `state` から復元する種別と一致する。"""
        assert GoogleCalendarClient.CREDENTIAL_TYPE == "google_calendar"
