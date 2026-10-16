"""`lilla_core.extensions.google_calendar` 同梱の `tools/llm_calendar_get.py` のテスト。"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from lilla_core.extensions import google_calendar

# ツールモジュールを直接ロード
_tool_path = Path(google_calendar.__file__).resolve().parent / "tools" / "llm_calendar_get.py"
_spec = importlib.util.spec_from_file_location("llm_calendar_get", str(_tool_path))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
execute = _mod.execute
SCHEMA = _mod.SCHEMA


def _make_mock_calendar(events=None, auth_url="https://accounts.google.com/auth"):
    """GoogleCalendarClient のモックを作成する。

    ``events`` は `get_events` の戻り値と同じ（取得元カレンダー ID, 予定）の組のリスト。
    """
    client = MagicMock()
    client.get_events = AsyncMock(return_value=events or [])
    client.start_authentication = AsyncMock(return_value=auth_url)
    return client


@pytest.fixture
def calendar_stub(monkeypatch):
    """カレンダーのクライアントモジュールを sys.modules にスタブし、モジュールを返す。

    各テスト前に sys.modules へ注入し、テスト後に monkeypatch が自動で元に戻す。
    カレンダー一覧は `google-calendar` 拡張が申告する `extensions.google_calendar` セクション
    由来のため、テスト用の設定（`tests/fixtures/config_root/lilla.yaml`）には含まれない。
    既定では空の一覧を入れておき、一覧を使うテストだけが
    `_stub_google_calendars()` で上書きする。
    """
    _stub_google_calendars(monkeypatch, [])
    stub = MagicMock()
    monkeypatch.setitem(sys.modules, "lilla_core.extensions.google_calendar.client", stub)
    return stub


def _stub_google_calendars(monkeypatch, calendars: list[dict]) -> None:
    """`get_section("google-calendar", ...)` の戻り値を指定のカレンダー一覧に差し替える。

    `execute()` は `get_section` / `get_config` を関数内で遅延 import するため、本物の
    `lilla_core.core.config` モジュールの属性を差し替えれば足りる（`sys.modules` の
    モジュールそのものは入れ替えない）。
    """
    from lilla_core.core import config as config_module

    section = google_calendar.GoogleCalendarConfig(calendars=calendars)
    # `DateRange` が `local_timezone()` 経由で `ZoneInfo(cfg.ui.timezone)` を解決するため、
    # 実行環境の OS タイムゾーンに左右されないよう固定する。
    cfg = SimpleNamespace(ui=SimpleNamespace(timezone="Asia/Tokyo"))

    def fake_get_section(name, model, config=None):
        assert name == "google-calendar"
        return section

    monkeypatch.setattr(config_module, "get_section", fake_get_section)
    monkeypatch.setattr(config_module, "get_config", lambda: cfg)


class TestSchema:
    """SCHEMA の構造テスト。"""

    def test_function_name(self) -> None:
        """SCHEMA の関数名が get_calendar_events である。"""
        assert SCHEMA["function"]["name"] == "get_calendar_events"

    def test_date_range_is_required(self) -> None:
        """date_range が required に含まれる。"""
        assert "date_range" in SCHEMA["function"]["parameters"]["required"]

    def test_optional_parameters_exist(self) -> None:
        """query と max_results が properties に存在する。"""
        props = SCHEMA["function"]["parameters"]["properties"]
        assert "query" in props
        assert "max_results" in props


class TestExecuteSuccess:
    """正常系テスト。"""

    async def test_returns_events(self, calendar_stub) -> None:
        """正常時は要約したイベントのリストを data に返す。"""
        mock_events = [
            (
                "primary",
                {
                    "id": "1",
                    "summary": "会議",
                    "start": {"dateTime": "2026-04-25T10:00:00+09:00"},
                    "end": {"dateTime": "2026-04-25T11:00:00+09:00"},
                },
            )
        ]
        calendar_stub.get_google_calendar_client.return_value = _make_mock_calendar(events=mock_events)

        result = await execute({"date_range": "today"}, {})

        assert result["success"] is True
        assert result["data"] == [
            {
                "calendar": "calendar_1",
                "summary": "会議",
                "start": "2026-04-25T10:00:00+09:00",
                "end": "2026-04-25T11:00:00+09:00",
                "all_day": False,
            }
        ]
        assert result["error"] is None

    async def test_needs_auth_false_on_success(self, calendar_stub) -> None:
        """正常時は needs_auth が False で needs_auth_list が空。"""
        calendar_stub.get_google_calendar_client.return_value = _make_mock_calendar()

        result = await execute({"date_range": "today"}, {})

        assert result["needs_auth"] is False
        assert result["needs_auth_list"] == []

    async def test_memory_entry_contains_date_range_and_count(self, calendar_stub) -> None:
        """memory_entry に date_range と件数が含まれる。"""
        events = [("primary", {"id": "1"}), ("primary", {"id": "2"})]
        calendar_stub.get_google_calendar_client.return_value = _make_mock_calendar(events=events)

        result = await execute({"date_range": "last_7_days"}, {})

        assert "last_7_days" in result["memory_entry"]
        assert "2件" in result["memory_entry"]

    async def test_empty_events_returns_zero_count(self, calendar_stub) -> None:
        """イベントが 0 件のとき memory_entry に 0件 が含まれる。"""
        calendar_stub.get_google_calendar_client.return_value = _make_mock_calendar(events=[])

        result = await execute({"date_range": "today"}, {})

        assert result["success"] is True
        assert "0件" in result["memory_entry"]


_OWNER_EMAIL = "owner@example.com"
_OTHER_EMAIL = "Someone.Else@Example.com"


def _full_api_event(**overrides) -> dict:
    """Calendar API が返す、メールアドレスやリンクを含んだ予定を作る。"""
    event = {
        "kind": "calendar#event",
        "etag": '"3333"',
        "id": "evt1",
        "status": "confirmed",
        "htmlLink": "https://www.google.com/calendar/event?eid=ZXZ0MSBvd25lckBleGFtcGxlLmNvbQ",
        "summary": "会議",
        "description": "議題の確認",
        "location": "会議室A",
        "creator": {"email": _OWNER_EMAIL, "self": True},
        "organizer": {"email": _OTHER_EMAIL, "displayName": "Someone Else"},
        "start": {"dateTime": "2026-04-25T10:00:00+09:00", "timeZone": "Asia/Tokyo"},
        "end": {"dateTime": "2026-04-25T11:00:00+09:00", "timeZone": "Asia/Tokyo"},
        "iCalUID": "evt1@google.com",
        "attendees": [{"email": _OWNER_EMAIL, "self": True}, {"email": _OTHER_EMAIL}],
        "hangoutLink": "https://meet.google.com/xxx",
        "reminders": {"useDefault": True},
    }
    event.update(overrides)
    return event


class TestExecuteWhitelist:
    """戻り値をホワイトリストの項目に絞るテスト。"""

    async def test_only_whitelisted_keys_are_returned(self, calendar_stub, monkeypatch) -> None:
        """予定ごとにホワイトリストの項目だけを返す。"""
        calendar_stub.get_google_calendar_client.return_value = _make_mock_calendar(
            events=[("primary", _full_api_event())]
        )
        _stub_google_calendars(monkeypatch, [{"id": "primary", "friendly_name": "個人のカレンダー"}])

        result = await execute({"date_range": "today"}, {})

        assert result["data"] == [
            {
                "calendar": "個人のカレンダー",
                "summary": "会議",
                "start": "2026-04-25T10:00:00+09:00",
                "end": "2026-04-25T11:00:00+09:00",
                "all_day": False,
                "status": "confirmed",
                "location": "会議室A",
                "description": "議題の確認",
            }
        ]

    async def test_no_email_with_primary_calendar(self, calendar_stub, monkeypatch) -> None:
        """primary を含む設定でも主催者・作成者・参加者のメールアドレスが出ない。"""
        calendar_stub.get_google_calendar_client.return_value = _make_mock_calendar(
            events=[
                ("primary", _full_api_event()),
                ("family@group.calendar.google.com", _full_api_event(id="evt2")),
            ]
        )
        _stub_google_calendars(monkeypatch, [
            {"id": "primary", "friendly_name": "個人のカレンダー"},
            {"id": "family@group.calendar.google.com", "friendly_name": "家族のカレンダー"},
        ])

        result = await execute({"date_range": "today"}, {})

        dumped = json.dumps(result, ensure_ascii=False).lower()
        assert _OWNER_EMAIL not in dumped
        assert _OTHER_EMAIL.lower() not in dumped
        assert "family@group.calendar.google.com" not in dumped
        assert "@" not in dumped
        for event in result["data"]:
            for key in ("organizer", "creator", "attendees", "htmlLink", "iCalUID", "id"):
                assert key not in event

    async def test_no_calendar_id_in_backward_compat_mode(self, calendar_stub) -> None:
        """calendars が空の後方互換モードでは ID ではなく汎用名を付ける。"""
        calendar_stub.get_google_calendar_client.return_value = _make_mock_calendar(
            events=[
                ("primary", _full_api_event()),
                ("family@group.calendar.google.com", _full_api_event(id="evt2")),
            ]
        )

        result = await execute(
            {"date_range": "today"},
            {"calendar_ids": ["primary", "family@group.calendar.google.com"]},
        )

        assert [e["calendar"] for e in result["data"]] == ["calendar_1", "calendar_2"]
        assert "@" not in json.dumps(result, ensure_ascii=False)

    async def test_optional_fields_omitted_when_absent(self, calendar_stub) -> None:
        """location / description / status が無い予定ではキーを付けない。"""
        calendar_stub.get_google_calendar_client.return_value = _make_mock_calendar(
            events=[("primary", {"id": "1", "summary": "会議"})]
        )

        result = await execute({"date_range": "today"}, {})

        assert result["success"] is True
        assert set(result["data"][0]) == {"calendar", "summary", "start", "end", "all_day"}

    async def test_all_day_event_end_is_inclusive(self, calendar_stub) -> None:
        """終日予定は日付で返し、end は最終日を含む日付にする。"""
        calendar_stub.get_google_calendar_client.return_value = _make_mock_calendar(
            events=[
                (
                    "primary",
                    {"summary": "旅行", "start": {"date": "2026-04-20"}, "end": {"date": "2026-04-23"}},
                )
            ]
        )

        result = await execute({"date_range": "today"}, {})

        event = result["data"][0]
        assert event["all_day"] is True
        assert event["start"] == "2026-04-20"
        assert event["end"] == "2026-04-22"

    async def test_input_events_not_mutated(self, calendar_stub) -> None:
        """要約は入力の予定を破壊的に変更しない。"""
        api_event = _full_api_event()
        calendar_stub.get_google_calendar_client.return_value = _make_mock_calendar(
            events=[("primary", api_event)]
        )

        await execute({"date_range": "today"}, {})

        assert api_event["organizer"]["email"] == _OTHER_EMAIL
        assert "attendees" in api_event


class TestExecuteCalendarIds:
    """calendar_ids の取り扱いテスト。"""

    async def test_default_calendar_ids_is_primary(self, calendar_stub) -> None:
        """calendar_ids が context にない場合 ['primary'] がデフォルト。"""
        mock_calendar = _make_mock_calendar()
        calendar_stub.get_google_calendar_client.return_value = mock_calendar

        await execute({"date_range": "today"}, {})

        kwargs = mock_calendar.get_events.call_args.kwargs
        assert kwargs["calendar_ids"] == ["primary"]

    async def test_empty_calendar_ids_uses_default(self, calendar_stub) -> None:
        """calendar_ids が空リストのとき ['primary'] がデフォルト。"""
        mock_calendar = _make_mock_calendar()
        calendar_stub.get_google_calendar_client.return_value = mock_calendar

        await execute({"date_range": "today"}, {"calendar_ids": []})

        kwargs = mock_calendar.get_events.call_args.kwargs
        assert kwargs["calendar_ids"] == ["primary"]

    async def test_custom_calendar_ids_passed(self, calendar_stub) -> None:
        """context の calendar_ids が get_events に渡される。"""
        mock_calendar = _make_mock_calendar()
        calendar_stub.get_google_calendar_client.return_value = mock_calendar
        ids = ["primary", "family@group.calendar.google.com"]

        await execute({"date_range": "today"}, {"calendar_ids": ids})

        kwargs = mock_calendar.get_events.call_args.kwargs
        assert kwargs["calendar_ids"] == ids


class TestExecuteCalendarsFriendlyName:
    """google.calendars（id + friendly_name）の取り扱いテスト。"""

    async def test_calendars_extracts_ids_for_get_events(self, calendar_stub, monkeypatch) -> None:
        """google.calendars 設定から id リストが get_events に渡される。"""
        mock_calendar = _make_mock_calendar()
        calendar_stub.get_google_calendar_client.return_value = mock_calendar
        _stub_google_calendars(monkeypatch, [
            {"id": "primary", "friendly_name": "個人のカレンダー"},
            {"id": "family@group.calendar.google.com", "friendly_name": "家族のカレンダー"},
        ])

        await execute({"date_range": "today"}, {})

        kwargs = mock_calendar.get_events.call_args.kwargs
        assert kwargs["calendar_ids"] == ["primary", "family@group.calendar.google.com"]

    async def test_each_event_has_source_friendly_name(self, calendar_stub, monkeypatch) -> None:
        """各予定に取得元カレンダーの friendly_name が付く。"""
        events = [
            ("primary", {"summary": "個人の予定"}),
            ("family@group.calendar.google.com", {"summary": "家族の予定"}),
        ]
        calendar_stub.get_google_calendar_client.return_value = _make_mock_calendar(events=events)
        _stub_google_calendars(monkeypatch, [
            {"id": "primary", "friendly_name": "個人のカレンダー"},
            {"id": "family@group.calendar.google.com", "friendly_name": "家族のカレンダー"},
        ])

        result = await execute({"date_range": "today"}, {})

        assert [(e["calendar"], e["summary"]) for e in result["data"]] == [
            ("個人のカレンダー", "個人の予定"),
            ("家族のカレンダー", "家族の予定"),
        ]

    async def test_calendars_empty_falls_back_to_calendar_ids(self, calendar_stub, monkeypatch) -> None:
        """google.calendars が空リストのとき context の calendar_ids にフォールバックする。"""
        mock_calendar = _make_mock_calendar()
        calendar_stub.get_google_calendar_client.return_value = mock_calendar
        _stub_google_calendars(monkeypatch, [])

        await execute({"date_range": "today"}, {"calendar_ids": ["primary"]})

        kwargs = mock_calendar.get_events.call_args.kwargs
        assert kwargs["calendar_ids"] == ["primary"]


class TestExecuteParameters:
    """パラメータの受け渡しテスト。"""

    async def test_default_max_results_is_20(self, calendar_stub) -> None:
        """max_results 未指定時はデフォルト 20 が渡される。"""
        mock_calendar = _make_mock_calendar()
        calendar_stub.get_google_calendar_client.return_value = mock_calendar

        await execute({"date_range": "today"}, {})

        kwargs = mock_calendar.get_events.call_args.kwargs
        assert kwargs["max_results"] == 20

    async def test_custom_max_results_passed(self, calendar_stub) -> None:
        """指定した max_results が get_events に渡される。"""
        mock_calendar = _make_mock_calendar()
        calendar_stub.get_google_calendar_client.return_value = mock_calendar

        await execute({"date_range": "today", "max_results": 5}, {})

        kwargs = mock_calendar.get_events.call_args.kwargs
        assert kwargs["max_results"] == 5

    async def test_query_none_when_not_provided(self, calendar_stub) -> None:
        """query 未指定時は None が渡される。"""
        mock_calendar = _make_mock_calendar()
        calendar_stub.get_google_calendar_client.return_value = mock_calendar

        await execute({"date_range": "today"}, {})

        kwargs = mock_calendar.get_events.call_args.kwargs
        assert kwargs["q"] is None

    async def test_query_passed_when_provided(self, calendar_stub) -> None:
        """query が指定された場合 get_events に渡される。"""
        mock_calendar = _make_mock_calendar()
        calendar_stub.get_google_calendar_client.return_value = mock_calendar

        await execute({"date_range": "today", "query": "会議"}, {})

        kwargs = mock_calendar.get_events.call_args.kwargs
        assert kwargs["q"] == "会議"


class TestExecuteDateRange:
    """date_range の各形式テスト。"""

    async def test_today(self, calendar_stub) -> None:
        """'today' が正常に処理される。"""
        calendar_stub.get_google_calendar_client.return_value = _make_mock_calendar()
        result = await execute({"date_range": "today"}, {})
        assert result["success"] is True

    async def test_yesterday(self, calendar_stub) -> None:
        """'yesterday' が正常に処理される。"""
        calendar_stub.get_google_calendar_client.return_value = _make_mock_calendar()
        result = await execute({"date_range": "yesterday"}, {})
        assert result["success"] is True

    async def test_last_7_days(self, calendar_stub) -> None:
        """'last_7_days' が正常に処理される。"""
        calendar_stub.get_google_calendar_client.return_value = _make_mock_calendar()
        result = await execute({"date_range": "last_7_days"}, {})
        assert result["success"] is True

    async def test_single_date(self, calendar_stub) -> None:
        """'YYYY-MM-DD' 形式が正常に処理される。"""
        calendar_stub.get_google_calendar_client.return_value = _make_mock_calendar()
        result = await execute({"date_range": "2026-04-20"}, {})
        assert result["success"] is True

    async def test_date_range(self, calendar_stub) -> None:
        """'YYYY-MM-DD/YYYY-MM-DD' 形式が正常に処理される。"""
        calendar_stub.get_google_calendar_client.return_value = _make_mock_calendar()
        result = await execute({"date_range": "2026-04-20/2026-04-26"}, {})
        assert result["success"] is True

    async def test_invalid_date_range_returns_error(self, calendar_stub) -> None:
        """不正な date_range はエラー dict を返す（例外を投げない）。"""
        result = await execute({"date_range": "invalid_format"}, {})
        assert result["success"] is False
        assert result["error"] is not None


class TestExecuteReauth:
    """再認証フローのテスト。"""

    async def test_reauth_required_returns_needs_auth_true(self, calendar_stub) -> None:
        """ReauthenticationRequiredError 発生時に needs_auth: True を返す。"""
        from lilla_core.core.exceptions import ReauthenticationRequiredError

        mock_calendar = _make_mock_calendar()
        mock_calendar.get_events = AsyncMock(side_effect=ReauthenticationRequiredError("再認証が必要"))
        calendar_stub.get_google_calendar_client.return_value = mock_calendar

        result = await execute({"date_range": "today"}, {})

        assert result["success"] is False
        assert result["needs_auth"] is True

    async def test_reauth_needs_auth_list_contains_google(self, calendar_stub) -> None:
        """needs_auth_list に google エントリが含まれる。"""
        from lilla_core.core.exceptions import ReauthenticationRequiredError

        mock_calendar = _make_mock_calendar(auth_url="https://accounts.google.com/auth?state=xyz")
        mock_calendar.get_events = AsyncMock(side_effect=ReauthenticationRequiredError("再認証が必要"))
        calendar_stub.get_google_calendar_client.return_value = mock_calendar

        result = await execute({"date_range": "today"}, {})

        assert len(result["needs_auth_list"]) == 1
        assert result["needs_auth_list"][0]["auth_service"] == "google"
        assert result["needs_auth_list"][0]["auth_url"] == "https://accounts.google.com/auth?state=xyz"

    async def test_reauth_calls_start_authentication(self, calendar_stub) -> None:
        """ReauthenticationRequiredError 発生時に start_authentication が呼ばれる。"""
        from lilla_core.core.exceptions import ReauthenticationRequiredError

        mock_calendar = _make_mock_calendar()
        mock_calendar.get_events = AsyncMock(side_effect=ReauthenticationRequiredError("再認証が必要"))
        calendar_stub.get_google_calendar_client.return_value = mock_calendar

        await execute({"date_range": "today"}, {})

        mock_calendar.start_authentication.assert_called_once()

    async def test_reauth_memory_entry_set(self, calendar_stub) -> None:
        """再認証時の memory_entry が設定されている。"""
        from lilla_core.core.exceptions import ReauthenticationRequiredError

        mock_calendar = _make_mock_calendar()
        mock_calendar.get_events = AsyncMock(side_effect=ReauthenticationRequiredError("再認証が必要"))
        calendar_stub.get_google_calendar_client.return_value = mock_calendar

        result = await execute({"date_range": "today"}, {})

        assert result["memory_entry"] is not None
        assert "再認証" in result["memory_entry"]
