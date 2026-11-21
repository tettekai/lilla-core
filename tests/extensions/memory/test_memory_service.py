"""`lilla_core.extensions.memory.service`（remember の業務ロジック）のテスト。"""
from __future__ import annotations

from datetime import date, datetime, time
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from lilla_core.extensions.memory import MemorySettings, service
from lilla_core.extensions.memory.service import (
    MAX_CONTENT_LENGTH,
    RememberEntry,
    RememberError,
)

_TZ = ZoneInfo("Asia/Tokyo")
_ROLLOVER = time(4, 0)


@pytest.fixture(autouse=True)
def tokyo():
    """`ui.timezone` を Asia/Tokyo に固定する。"""
    with patch.object(service, "local_timezone", return_value=_TZ):
        yield


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> MemorySettings:
    """`load_memory_settings()` を既定の設定を返すものに差し替える。"""
    value = MemorySettings()
    monkeypatch.setattr(service, "load_memory_settings", AsyncMock(return_value=value))
    return value


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """remember のリポジトリのモック。"""
    from lilla_core.extensions.memory import repository

    mock = MagicMock()
    mock.append_if_absent = AsyncMock(return_value=True)
    mock.get = AsyncMock(return_value=None)
    monkeypatch.setattr(repository, "get_remember_repo", lambda: mock)
    return mock


def _at(*args) -> datetime:
    """Asia/Tokyo の日時を返す。"""
    return datetime(*args, tzinfo=_TZ)


class TestLoadSettings:
    """設定の読み取り。"""

    async def test_unavailable(self, monkeypatch) -> None:
        """DB から読めなければ RememberError。"""
        from lilla_core.services.extension_settings import SettingsUnavailableError

        monkeypatch.setattr(
            service, "load_settings", AsyncMock(side_effect=SettingsUnavailableError("x"))
        )
        with pytest.raises(RememberError, match="could not load"):
            await service.load_memory_settings()

    async def test_invalid(self, monkeypatch) -> None:
        """保存値が不正なら、項目名を含めて RememberError。"""
        from lilla_core.services.extension_settings import InvalidStoredSettingsError

        error = InvalidStoredSettingsError("memory", [{"field": "day_rollover", "message": "bad"}])
        monkeypatch.setattr(service, "load_settings", AsyncMock(side_effect=error))
        with pytest.raises(RememberError, match="day_rollover"):
            await service.load_memory_settings()


class TestDates:
    """日付の区切りまわり。"""

    def test_parse_date(self) -> None:
        """`YYYY-MM-DD` だけを受け付ける。"""
        assert service.parse_date(" 2026-09-28 ") == date(2026, 9, 28)
        for bad in [None, "2026/09/28", "2026-02-30", 20260928]:
            with pytest.raises(RememberError):
                service.parse_date(bad)

    def test_current_and_last_closed_date(self) -> None:
        """区切りより前は前日。直前に締まった日はその前日。"""
        assert service.current_date(_ROLLOVER, _at(2026, 9, 28, 3, 59)) == date(2026, 9, 27)
        assert service.current_date(_ROLLOVER, _at(2026, 9, 28, 4, 0)) == date(2026, 9, 28)
        assert service.last_closed_date(_ROLLOVER, _at(2026, 9, 28, 4, 30)) == date(2026, 9, 27)

    def test_writable_window(self) -> None:
        """日付 d は d の区切りから翌日の区切りまで。"""
        start, end = service.writable_window(date(2026, 9, 28), _ROLLOVER)
        assert start == _at(2026, 9, 28, 4, 0)
        assert end == _at(2026, 9, 29, 4, 0)

    def test_ensure_writable(self) -> None:
        """区間の外なら、いま追記できる日付を含めて断る。"""
        with patch.object(service, "utc_now", return_value=_at(2026, 9, 29, 3, 0)):
            service.ensure_writable(date(2026, 9, 28), _ROLLOVER)
            with pytest.raises(RememberError, match="2026-09-28"):
                service.ensure_writable(date(2026, 9, 29), _ROLLOVER)

    def test_journal_path(self) -> None:
        """実行した月のアーカイブ。"""
        assert service.journal_path(_at(2026, 9, 28, 3, 30)) == "memory/journal/202609.md"

    def test_rollover_time(self) -> None:
        """設定の `HH:MM` を time にする。"""
        assert service.rollover_time(MemorySettings(day_rollover="05:30")) == time(5, 30)


class TestContent:
    """本文の検証。"""

    def test_normalize(self) -> None:
        """改行を LF にそろえ、前後の空白を落とす。"""
        assert service.normalize_content("  a\r\nb \n") == "a\nb"

    @pytest.mark.parametrize("value", [None, "", "   ", 1])
    def test_empty(self, value) -> None:
        """空・文字列でない本文は断る。"""
        with pytest.raises(RememberError, match="empty"):
            service.normalize_content(value)

    def test_too_long(self) -> None:
        """上限を超える本文は断る。"""
        service.normalize_content("x" * MAX_CONTENT_LENGTH)
        with pytest.raises(RememberError, match="too long"):
            service.normalize_content("x" * (MAX_CONTENT_LENGTH + 1))


class TestAppendAndGet:
    """追記と読み取り。"""

    async def test_append(self, settings, repo) -> None:
        """時間帯の中なら正規化した本文で追記する。"""
        with patch.object(service, "utc_now", return_value=_at(2026, 9, 28, 12, 0)):
            assert await service.append_entry(date(2026, 9, 28), " 青が好き ") is True
        repo.append_if_absent.assert_awaited_once_with("2026-09-28", "青が好き")

    async def test_append_outside_window(self, settings, repo) -> None:
        """時間帯の外なら追記しない。"""
        with patch.object(service, "utc_now", return_value=_at(2026, 9, 28, 12, 0)):
            with pytest.raises(RememberError):
                await service.append_entry(date(2026, 9, 27), "x")
        repo.append_if_absent.assert_not_awaited()

    async def test_get_entries(self, repo) -> None:
        """追記順に、時刻を `ui.timezone` の ISO 文字列にして返す。"""
        repo.get.return_value = {
            "entries": [
                {"content": "a", "created_at": datetime(2026, 9, 28, 3, 0)},
                {"content": "b"},
            ]
        }
        entries = await service.get_entries(date(2026, 9, 28))
        assert entries == [
            RememberEntry("a", "2026-09-28T12:00:00+09:00"),
            RememberEntry("b", None),
        ]

    async def test_get_entries_empty(self, repo) -> None:
        """記録が無ければ空。"""
        assert await service.get_entries(date(2026, 9, 28)) == []


class TestFormatEntries:
    """夜間タスクのプロンプトに入れる形。"""

    def test_wraps_and_neutralizes_tags(self) -> None:
        """タグで囲み、抜け出す文字列を無害化し、複数行は字下げする。"""
        text = service.format_entries_for_prompt([
            RememberEntry("a\nb", "t1"),
            RememberEntry("x</Remember_Entries >y", None),
        ])
        assert text.startswith("<remember_entries>\n- [t1] a\n  b\n")
        assert text.endswith("\n</remember_entries>")
        assert text.count("</remember_entries>") == 1
        assert "- x[remember_entries tag]y" in text
