"""`lilla_core.extensions.google_calendar` 同梱の `tools/llm_calendar_get.py` のテスト。"""
from __future__ import annotations

import importlib.util
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
    """GoogleCalendarClient のモックを作成する。"""
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
        """正常時はイベントリストを data に返す。"""
        mock_events = [{"id": "1", "summary": "会議", "start": {"dateTime": "2026-04-25T10:00:00"}}]
        calendar_stub.get_google_calendar_client.return_value = _make_mock_calendar(events=mock_events)

        result = await execute({"date_range": "today"}, {})

        assert result["success"] is True
        assert result["data"] == mock_events
        assert result["error"] is None

    async def test_needs_auth_false_on_success(self, calendar_stub) -> None:
        """正常時は needs_auth が False で needs_auth_list が空。"""
        calendar_stub.get_google_calendar_client.return_value = _make_mock_calendar()

        result = await execute({"date_range": "today"}, {})

        assert result["needs_auth"] is False
        assert result["needs_auth_list"] == []

    async def test_memory_entry_contains_date_range_and_count(self, calendar_stub) -> None:
        """memory_entry に date_range と件数が含まれる。"""
        events = [{"id": "1"}, {"id": "2"}]
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


class TestExecuteAttendeesStripped:
    """attendees フィールド除去のテスト。"""

    async def test_attendees_removed_from_event(self, calendar_stub) -> None:
        """attendees フィールドが結果から除去される。"""
        events = [
            {
                "id": "1",
                "summary": "会議",
                "attendees": [{"email": "someone@example.com"}],
            }
        ]
        calendar_stub.get_google_calendar_client.return_value = _make_mock_calendar(events=events)

        result = await execute({"date_range": "today"}, {})

        assert "attendees" not in result["data"][0]

    async def test_no_error_when_attendees_missing(self, calendar_stub) -> None:
        """attendees フィールドが元々ないイベントでもエラーにならない。"""
        events = [{"id": "1", "summary": "会議"}]
        calendar_stub.get_google_calendar_client.return_value = _make_mock_calendar(events=events)

        result = await execute({"date_range": "today"}, {})

        assert result["success"] is True
        assert "attendees" not in result["data"][0]

    async def test_attendees_removed_with_friendly_name_replacement(
        self, calendar_stub, monkeypatch
    ) -> None:
        """カレンダーID置換と併用してもattendeesが除去される。"""
        events = [
            {
                "id": "1",
                "organizer": {"email": "family@group.calendar.google.com"},
                "attendees": [{"email": "someone@example.com"}],
            }
        ]
        mock_calendar = _make_mock_calendar(events=events)
        calendar_stub.get_google_calendar_client.return_value = mock_calendar
        _stub_google_calendars(
            monkeypatch,
            [{"id": "family@group.calendar.google.com", "friendly_name": "家族のカレンダー"}],
        )

        result = await execute({"date_range": "today"}, {})

        assert "attendees" not in result["data"][0]
        assert result["data"][0]["organizer"]["email"] == "家族のカレンダー"

    async def test_attendees_removed_for_multiple_events(self, calendar_stub) -> None:
        """複数イベントすべてでattendeesが除去される。"""
        events = [
            {"id": "1", "attendees": [{"email": "a@example.com"}]},
            {"id": "2", "attendees": [{"email": "b@example.com"}]},
        ]
        calendar_stub.get_google_calendar_client.return_value = _make_mock_calendar(events=events)

        result = await execute({"date_range": "today"}, {})

        assert all("attendees" not in e for e in result["data"])


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

    async def test_organizer_email_replaced_with_friendly_name(self, calendar_stub, monkeypatch) -> None:
        """organizer.email のカレンダーIDが friendly_name に置換される。"""
        events = [
            {
                "id": "1",
                "organizer": {"email": "family@group.calendar.google.com"},
            }
        ]
        mock_calendar = _make_mock_calendar(events=events)
        calendar_stub.get_google_calendar_client.return_value = mock_calendar
        _stub_google_calendars(
            monkeypatch,
            [{"id": "family@group.calendar.google.com", "friendly_name": "家族のカレンダー"}],
        )

        result = await execute({"date_range": "today"}, {})

        assert result["data"][0]["organizer"]["email"] == "家族のカレンダー"

    async def test_creator_email_replaced_with_friendly_name(self, calendar_stub, monkeypatch) -> None:
        """creator.email のカレンダーIDが friendly_name に置換される。"""
        events = [
            {
                "id": "1",
                "creator": {"email": "family@group.calendar.google.com"},
            }
        ]
        mock_calendar = _make_mock_calendar(events=events)
        calendar_stub.get_google_calendar_client.return_value = mock_calendar
        _stub_google_calendars(
            monkeypatch,
            [{"id": "family@group.calendar.google.com", "friendly_name": "家族のカレンダー"}],
        )

        result = await execute({"date_range": "today"}, {})

        assert result["data"][0]["creator"]["email"] == "家族のカレンダー"

    async def test_primary_id_not_replaced(self, calendar_stub, monkeypatch) -> None:
        """'primary' は一般名詞のため置換対象外。"""
        events = [
            {
                "id": "1",
                "organizer": {"email": "primary"},
            }
        ]
        mock_calendar = _make_mock_calendar(events=events)
        calendar_stub.get_google_calendar_client.return_value = mock_calendar
        _stub_google_calendars(monkeypatch, [{"id": "primary", "friendly_name": "個人のカレンダー"}])

        result = await execute({"date_range": "today"}, {})

        assert result["data"][0]["organizer"]["email"] == "primary"

    async def test_no_calendars_no_replacement(self, calendar_stub, monkeypatch) -> None:
        """google.calendars 未設定時は置換が行われない（後方互換）。"""
        events = [{"id": "1", "organizer": {"email": "family@group.calendar.google.com"}}]
        mock_calendar = _make_mock_calendar(events=events)
        calendar_stub.get_google_calendar_client.return_value = mock_calendar
        _stub_google_calendars(monkeypatch, [])

        result = await execute({"date_range": "today"}, {})

        assert result["data"][0]["organizer"]["email"] == "family@group.calendar.google.com"

    async def test_calendars_empty_falls_back_to_calendar_ids(self, calendar_stub, monkeypatch) -> None:
        """google.calendars が空リストのとき context の calendar_ids にフォールバックする。"""
        mock_calendar = _make_mock_calendar()
        calendar_stub.get_google_calendar_client.return_value = mock_calendar
        _stub_google_calendars(monkeypatch, [])

        await execute({"date_range": "today"}, {"calendar_ids": ["primary"]})

        kwargs = mock_calendar.get_events.call_args.kwargs
        assert kwargs["calendar_ids"] == ["primary"]


class TestMaskCalendarIdsRobustness:
    """カレンダーID置換のエッジケース（部分一致・衝突・非破壊）テスト。"""

    async def test_id_that_is_substring_of_another_id_not_mistakenly_replaced(
        self, calendar_stub, monkeypatch
    ) -> None:
        """あるIDが別IDの部分文字列でも、完全一致した値だけが置換される。"""
        events = [
            {"id": "1", "organizer": {"email": "a@group.calendar.google.com"}},
            {"id": "2", "organizer": {"email": "xa@group.calendar.google.com"}},
        ]
        calendar_stub.get_google_calendar_client.return_value = _make_mock_calendar(events=events)
        _stub_google_calendars(monkeypatch, [
            {"id": "a@group.calendar.google.com", "friendly_name": "カレンダーA"},
            {"id": "xa@group.calendar.google.com", "friendly_name": "カレンダーXA"},
        ])

        result = await execute({"date_range": "today"}, {})

        # 部分文字列 "a@..." が "xa@..." の一部を誤って置換しないこと。
        assert result["data"][0]["organizer"]["email"] == "カレンダーA"
        assert result["data"][1]["organizer"]["email"] == "カレンダーXA"

    async def test_friendly_name_matching_another_id_not_double_replaced(
        self, calendar_stub, monkeypatch
    ) -> None:
        """friendly_name が別のIDと一致しても再置換（多重置換）されない。"""
        events = [{"id": "1", "organizer": {"email": "first@group.calendar.google.com"}}]
        calendar_stub.get_google_calendar_client.return_value = _make_mock_calendar(events=events)
        # 1つ目の friendly_name が 2つ目の id と同じ文字列。
        _stub_google_calendars(monkeypatch, [
            {"id": "first@group.calendar.google.com", "friendly_name": "second@group.calendar.google.com"},
            {"id": "second@group.calendar.google.com", "friendly_name": "誤置換されるべきでない"},
        ])

        result = await execute({"date_range": "today"}, {})

        # 1回の走査で置換するため、置換結果がさらに置換されることはない。
        assert result["data"][0]["organizer"]["email"] == "second@group.calendar.google.com"

    async def test_partial_occurrence_in_free_text_not_replaced(self, calendar_stub, monkeypatch) -> None:
        """summary など自由記述に含まれる部分文字列は置換されない（完全一致のみ）。"""
        events = [
            {
                "id": "1",
                "summary": "family@group.calendar.google.com への招待",
                "organizer": {"email": "family@group.calendar.google.com"},
            }
        ]
        calendar_stub.get_google_calendar_client.return_value = _make_mock_calendar(events=events)
        _stub_google_calendars(
            monkeypatch,
            [{"id": "family@group.calendar.google.com", "friendly_name": "家族のカレンダー"}],
        )

        result = await execute({"date_range": "today"}, {})

        # フィールド値が完全一致する organizer.email のみ置換される。
        assert result["data"][0]["organizer"]["email"] == "家族のカレンダー"
        assert result["data"][0]["summary"] == "family@group.calendar.google.com への招待"

    async def test_masking_does_not_mutate_input_events(self, calendar_stub, monkeypatch) -> None:
        """置換処理は入力の events を破壊的に変更しない。"""
        events = [{"id": "1", "organizer": {"email": "family@group.calendar.google.com"}}]
        calendar_stub.get_google_calendar_client.return_value = _make_mock_calendar(events=events)
        _stub_google_calendars(
            monkeypatch,
            [{"id": "family@group.calendar.google.com", "friendly_name": "家族のカレンダー"}],
        )

        await execute({"date_range": "today"}, {})

        # 元の events オブジェクトは変更されていない。
        assert events[0]["organizer"]["email"] == "family@group.calendar.google.com"

    async def test_stripping_does_not_mutate_input_events(self, calendar_stub) -> None:
        """attendees 除去は入力の events を破壊的に変更しない。"""
        events = [{"id": "1", "attendees": [{"email": "someone@example.com"}]}]
        calendar_stub.get_google_calendar_client.return_value = _make_mock_calendar(events=events)

        result = await execute({"date_range": "today"}, {})

        # 戻り値からは除去され、元の events には残っている。
        assert "attendees" not in result["data"][0]
        assert "attendees" in events[0]


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
