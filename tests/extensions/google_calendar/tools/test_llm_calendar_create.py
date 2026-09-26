"""`lilla_core.extensions.google_calendar` 同梱の `tools/llm_calendar_create.py` のテスト。"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from lilla_core.core.exceptions import ReauthenticationRequiredError
from lilla_core.extensions import google_calendar

_TOOL_PATH = Path(google_calendar.__file__).resolve().parent / "tools" / "llm_calendar_create.py"
_CLIENT_MODULE = "lilla_core.extensions.google_calendar.client"


def _make_cfg(calendars: list[dict] | None = None) -> google_calendar.GoogleCalendarConfig:
    """`extensions.google_calendar` セクション（カレンダー一覧）を生成する。"""
    return google_calendar.GoogleCalendarConfig(calendars=calendars or [])


def _make_client(created_event: dict | None = None, reauth: bool = False) -> MagicMock:
    """GoogleCalendarClient のモックを生成する。"""
    client = MagicMock()
    default_event = {
        "id": "evt1",
        "summary": "テスト予定",
        "htmlLink": "https://calendar.google.com/event?eid=evt1",
    }
    if reauth:
        client.create_event = AsyncMock(side_effect=ReauthenticationRequiredError("再認証"))
        client.start_authentication = AsyncMock(
            return_value="https://auth.example.com/google_calendar"
        )
    else:
        client.create_event = AsyncMock(return_value=created_event or default_event)
    return client


@pytest.fixture
def mock_cfg() -> google_calendar.GoogleCalendarConfig:
    """個人のカレンダー1件を持つデフォルトのカレンダー設定。"""
    return _make_cfg([{"id": "primary", "friendly_name": "個人のカレンダー"}])


@pytest.fixture
def with_mocked_modules(mock_cfg, monkeypatch: pytest.MonkeyPatch):
    """ツールが読むカレンダー設定（`get_section()`）を差し替える。

    ツールは `get_section` を関数内で遅延 import するため、本物の
    `lilla_core.core.config` モジュールの属性を差し替えれば足りる
    （`sys.modules` のモジュールそのものは入れ替えない）。
    """
    from lilla_core.core import config as config_module

    def fake_get_section(name, model, config=None):
        assert name == "google-calendar"
        assert model is google_calendar.GoogleCalendarConfig
        return mock_cfg

    monkeypatch.setattr(config_module, "get_section", fake_get_section)
    yield


@pytest.fixture
def llm_calendar_create(with_mocked_modules):
    """patch.dict 有効後に llm_calendar_create をロードする。"""
    sys.modules.pop("llm_calendar_create", None)
    spec = importlib.util.spec_from_file_location("llm_calendar_create", str(_TOOL_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop("llm_calendar_create", None)


@pytest.fixture(autouse=True)
def patch_calendar_client(monkeypatch: pytest.MonkeyPatch):
    """カレンダーのクライアントモジュールをモックに差し替えるフィクスチャ。"""
    mock_module = MagicMock()
    monkeypatch.setitem(sys.modules, _CLIENT_MODULE, mock_module)
    return mock_module


class TestSchema:
    """SCHEMA の構造テスト。"""

    def test_function_name(self, llm_calendar_create) -> None:
        """SCHEMA の関数名が create_calendar_event である。"""
        assert llm_calendar_create.SCHEMA["function"]["name"] == "create_calendar_event"

    def test_required_parameters(self, llm_calendar_create) -> None:
        """calendar / summary / start が required に含まれる。"""
        required = llm_calendar_create.SCHEMA["function"]["parameters"]["required"]
        assert set(required) == {"calendar", "summary", "start"}

    def test_optional_parameters_exist(self, llm_calendar_create) -> None:
        """end / description / location / all_day が properties に存在する。"""
        props = llm_calendar_create.SCHEMA["function"]["parameters"]["properties"]
        assert "end" in props
        assert "description" in props
        assert "location" in props
        assert "all_day" in props


class TestBuildSchema:
    """build_schema() のテスト（google_calendar.calendars を参照する）。"""

    def test_injects_friendly_names_as_enum(self, llm_calendar_create) -> None:
        """google_calendar.calendars の friendly_name 一覧が calendar の enum に注入される。"""
        schema = llm_calendar_create.build_schema({})
        calendar_prop = schema["function"]["parameters"]["properties"]["calendar"]
        assert calendar_prop["enum"] == ["個人のカレンダー"]

    def test_does_not_mutate_module_schema(self, llm_calendar_create) -> None:
        """build_schema 呼び出し後もモジュール側 SCHEMA は変更されないこと。"""
        original = llm_calendar_create.SCHEMA["function"]["parameters"]["properties"]["calendar"]
        original_keys = set(original.keys())
        llm_calendar_create.build_schema({})
        assert set(original.keys()) == original_keys
        assert "enum" not in original


class TestBuildSchemaNoCalendars:
    """google_calendar.calendars が空のときの build_schema() テスト。"""

    @pytest.fixture
    def mock_cfg(self) -> google_calendar.GoogleCalendarConfig:
        """google_calendar.calendars が空の設定モック。"""
        return _make_cfg([])

    def test_no_enum_when_calendars_empty(self, llm_calendar_create) -> None:
        """google_calendar.calendars が空なら enum を注入しない。"""
        schema = llm_calendar_create.build_schema({})
        calendar_prop = schema["function"]["parameters"]["properties"]["calendar"]
        assert "enum" not in calendar_prop


class TestExecuteValidation:
    """入力パラメータのバリデーションテスト。"""

    async def test_returns_error_when_calendar_missing(
        self, llm_calendar_create, patch_calendar_client
    ) -> None:
        """calendar が未指定のとき success: False を返す。"""
        result = await llm_calendar_create.execute({"summary": "予定", "start": "2026-04-20"}, {})
        assert result["success"] is False
        assert "calendar" in result["error"]

    async def test_returns_error_when_summary_missing(
        self, llm_calendar_create, patch_calendar_client
    ) -> None:
        """summary が未指定のとき success: False を返す。"""
        result = await llm_calendar_create.execute(
            {"calendar": "個人のカレンダー", "start": "2026-04-20"}, {}
        )
        assert result["success"] is False
        assert "summary" in result["error"]

    async def test_returns_error_when_start_missing(
        self, llm_calendar_create, patch_calendar_client
    ) -> None:
        """start が未指定のとき success: False を返す。"""
        result = await llm_calendar_create.execute(
            {"calendar": "個人のカレンダー", "summary": "予定"}, {}
        )
        assert result["success"] is False
        assert "start" in result["error"]

    async def test_returns_error_for_unknown_calendar(
        self, llm_calendar_create, patch_calendar_client
    ) -> None:
        """google_calendar.calendars に存在しない friendly_name を指定すると success: False を返す。"""
        result = await llm_calendar_create.execute(
            {
                "calendar": "存在しないカレンダー",
                "summary": "予定",
                "start": "2026-04-20",
                "all_day": True,
            },
            {},
        )
        assert result["success"] is False
        assert "存在しないカレンダー" in result["error"]

    async def test_returns_error_when_timed_event_missing_end(
        self, llm_calendar_create, patch_calendar_client
    ) -> None:
        """時間指定イベント（all_day=false）で end が未指定なら success: False を返す。"""
        result = await llm_calendar_create.execute(
            {
                "calendar": "個人のカレンダー",
                "summary": "予定",
                "start": "2026-04-20T10:00:00+09:00",
            },
            {},
        )
        assert result["success"] is False
        assert "end" in result["error"]


class TestExecuteValidationNoCalendars:
    """google_calendar.calendars が空のときの execute() テスト。"""

    @pytest.fixture
    def mock_cfg(self) -> google_calendar.GoogleCalendarConfig:
        """google_calendar.calendars が空の設定モック。"""
        return _make_cfg([])

    async def test_returns_error_when_calendars_empty(
        self, llm_calendar_create, patch_calendar_client
    ) -> None:
        """google_calendar.calendars が空のとき success: False を返す。"""
        result = await llm_calendar_create.execute(
            {
                "calendar": "primary",
                "summary": "予定",
                "start": "2026-04-20",
                "all_day": True,
            },
            {},
        )
        assert result["success"] is False
        assert "google_calendar.calendars" in result["error"]


class TestExecuteAllDay:
    """終日イベント作成の正常系テスト。"""

    async def test_creates_all_day_event(self, llm_calendar_create, patch_calendar_client) -> None:
        """終日イベントの作成が client.create_event に正しい引数で渡される。"""
        client = _make_client()
        patch_calendar_client.get_google_calendar_client.return_value = client

        result = await llm_calendar_create.execute(
            {
                "calendar": "個人のカレンダー",
                "summary": "予定",
                "start": "2026-04-20",
                "all_day": True,
            },
            {},
        )

        assert result["success"] is True
        client.create_event.assert_called_once_with(
            "primary",
            "予定",
            all_day=True,
            start="2026-04-20",
            end="2026-04-20",
            description=None,
            location=None,
        )

    async def test_end_defaults_to_start_when_omitted(
        self, llm_calendar_create, patch_calendar_client
    ) -> None:
        """終日イベントで end 省略時は start と同じ日が使われる。"""
        client = _make_client()
        patch_calendar_client.get_google_calendar_client.return_value = client

        await llm_calendar_create.execute(
            {
                "calendar": "個人のカレンダー",
                "summary": "予定",
                "start": "2026-04-20",
                "all_day": True,
            },
            {},
        )

        kwargs = client.create_event.call_args.kwargs
        assert kwargs["end"] == "2026-04-20"

    async def test_resolves_calendar_id_from_friendly_name(
        self, llm_calendar_create, patch_calendar_client
    ) -> None:
        """calendar の friendly_name が google_calendar.calendars の id に解決される。"""
        client = _make_client()
        patch_calendar_client.get_google_calendar_client.return_value = client

        await llm_calendar_create.execute(
            {
                "calendar": "個人のカレンダー",
                "summary": "予定",
                "start": "2026-04-20",
                "all_day": True,
            },
            {},
        )

        args, _ = client.create_event.call_args
        assert args[0] == "primary"


class TestExecuteTimed:
    """時間指定イベント作成のテスト。"""

    async def test_creates_timed_event_with_datetime_objects(
        self, llm_calendar_create, patch_calendar_client
    ) -> None:
        """時間指定イベントは start / end が datetime に変換されて渡される。"""
        client = _make_client()
        patch_calendar_client.get_google_calendar_client.return_value = client

        await llm_calendar_create.execute(
            {
                "calendar": "個人のカレンダー",
                "summary": "会議",
                "start": "2026-04-20T10:00:00+09:00",
                "end": "2026-04-20T11:00:00+09:00",
            },
            {},
        )

        kwargs = client.create_event.call_args.kwargs
        assert kwargs["all_day"] is False
        assert isinstance(kwargs["start"], datetime)
        assert isinstance(kwargs["end"], datetime)

    async def test_returns_error_on_invalid_datetime_format(
        self, llm_calendar_create, patch_calendar_client
    ) -> None:
        """start / end が ISO8601 形式でないとき success: False を返す。"""
        client = _make_client()
        patch_calendar_client.get_google_calendar_client.return_value = client

        result = await llm_calendar_create.execute(
            {
                "calendar": "個人のカレンダー",
                "summary": "会議",
                "start": "invalid",
                "end": "2026-04-20T11:00:00+09:00",
            },
            {},
        )

        assert result["success"] is False


class TestExecuteOptionalFields:
    """description / location の受け渡しテスト。"""

    async def test_passes_description_and_location(
        self, llm_calendar_create, patch_calendar_client
    ) -> None:
        """description / location が client.create_event に渡される。"""
        client = _make_client()
        patch_calendar_client.get_google_calendar_client.return_value = client

        await llm_calendar_create.execute(
            {
                "calendar": "個人のカレンダー",
                "summary": "予定",
                "start": "2026-04-20",
                "all_day": True,
                "description": "説明",
                "location": "会議室",
            },
            {},
        )

        kwargs = client.create_event.call_args.kwargs
        assert kwargs["description"] == "説明"
        assert kwargs["location"] == "会議室"


class TestExecuteSuccess:
    """成功時のレスポンス内容テスト。"""

    async def test_memory_entry_contains_summary_and_html_link(
        self, llm_calendar_create, patch_calendar_client
    ) -> None:
        """memory_entry に summary と htmlLink が含まれる。"""
        client = _make_client(
            created_event={
                "id": "evt1",
                "summary": "予定",
                "htmlLink": "https://calendar.google.com/event?eid=evt1",
            }
        )
        patch_calendar_client.get_google_calendar_client.return_value = client

        result = await llm_calendar_create.execute(
            {
                "calendar": "個人のカレンダー",
                "summary": "予定",
                "start": "2026-04-20",
                "all_day": True,
            },
            {},
        )

        assert "予定" in result["memory_entry"]
        assert "https://calendar.google.com/event?eid=evt1" in result["memory_entry"]

    async def test_data_contains_created_event(
        self, llm_calendar_create, patch_calendar_client
    ) -> None:
        """data に作成されたイベントがそのまま含まれる。"""
        created = {"id": "evt1", "summary": "予定", "htmlLink": "https://x"}
        client = _make_client(created_event=created)
        patch_calendar_client.get_google_calendar_client.return_value = client

        result = await llm_calendar_create.execute(
            {
                "calendar": "個人のカレンダー",
                "summary": "予定",
                "start": "2026-04-20",
                "all_day": True,
            },
            {},
        )

        assert result["data"] == created


class TestExecuteReauth:
    """再認証フローのテスト。"""

    async def test_returns_needs_auth_on_reauth_error(
        self, llm_calendar_create, patch_calendar_client
    ) -> None:
        """ReauthenticationRequiredError 発生時に needs_auth: True を返す。"""
        client = _make_client(reauth=True)
        patch_calendar_client.get_google_calendar_client.return_value = client

        result = await llm_calendar_create.execute(
            {
                "calendar": "個人のカレンダー",
                "summary": "予定",
                "start": "2026-04-20",
                "all_day": True,
            },
            {},
        )

        assert result["success"] is False
        assert result["needs_auth"] is True
        assert result["needs_auth_list"] == [
            {"auth_service": "google", "auth_url": "https://auth.example.com/google_calendar"}
        ]
