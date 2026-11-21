"""長期記憶の LLM ツール（`llm_remember_append` / `llm_remember_get`）のテスト。"""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

import pytest

from lilla_core.extensions.memory import service
from lilla_core.extensions.memory.service import RememberEntry, RememberError
from lilla_core.extensions.memory.tools import llm_remember_append, llm_remember_get


class TestRememberAppend:
    """`llm_remember_append`。"""

    async def test_appended(self, monkeypatch) -> None:
        """追記できたら `appended: true`。"""
        mock = AsyncMock(return_value=True)
        monkeypatch.setattr(llm_remember_append, "append_entry", mock)

        result = await llm_remember_append.execute({"date": "2026-09-28", "content": "a"}, {})

        assert result["success"] is True
        assert result["data"] == {"date": "2026-09-28", "appended": True}
        mock.assert_awaited_once_with(date(2026, 9, 28), "a")

    async def test_duplicate(self, monkeypatch) -> None:
        """同じ本文があれば `appended: false` と理由を返す（失敗ではない）。"""
        monkeypatch.setattr(llm_remember_append, "append_entry", AsyncMock(return_value=False))

        result = await llm_remember_append.execute({"date": "2026-09-28", "content": "a"}, {})

        assert result["success"] is True
        assert result["data"]["appended"] is False
        assert result["data"]["reason"]

    async def test_remember_error_is_returned(self, monkeypatch) -> None:
        """RememberError はその文言のまま失敗にする。"""
        monkeypatch.setattr(
            llm_remember_append, "append_entry", AsyncMock(side_effect=RememberError("outside"))
        )
        result = await llm_remember_append.execute({"date": "2026-09-28", "content": "a"}, {})
        assert result["success"] is False
        assert "outside" in result["error"]

    async def test_invalid_date(self) -> None:
        """日付が無ければ失敗。"""
        result = await llm_remember_append.execute({"content": "a"}, {})
        assert result["success"] is False
        assert "YYYY-MM-DD" in result["error"]

    async def test_unexpected_error(self, monkeypatch) -> None:
        """想定外の例外も失敗にする。"""
        monkeypatch.setattr(
            llm_remember_append, "append_entry", AsyncMock(side_effect=RuntimeError("db"))
        )
        result = await llm_remember_append.execute({"date": "2026-09-28", "content": "a"}, {})
        assert result["success"] is False


class TestRememberGet:
    """`llm_remember_get`。"""

    async def test_returns_entries(self, monkeypatch) -> None:
        """その日の記録を追記順に返す。"""
        monkeypatch.setattr(
            llm_remember_get,
            "get_entries",
            AsyncMock(return_value=[RememberEntry("a", "t1"), RememberEntry("b", None)]),
        )
        result = await llm_remember_get.execute({"date": "2026-09-28"}, {})

        assert result["success"] is True
        assert result["data"] == {
            "date": "2026-09-28",
            "entries": [
                {"content": "a", "created_at": "t1"},
                {"content": "b", "created_at": None},
            ],
        }

    @pytest.mark.parametrize("value", [None, "28/09/2026"])
    async def test_invalid_date(self, value) -> None:
        """日付が不正なら失敗。"""
        result = await llm_remember_get.execute({"date": value}, {})
        assert result["success"] is False


def test_service_is_shared() -> None:
    """ツールは service の関数をそのまま使う（ロジックを二重に持たない）。"""
    assert llm_remember_append.append_entry is service.append_entry
    assert llm_remember_get.get_entries is service.get_entries
