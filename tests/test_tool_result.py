"""tool_result.py（LLM ツールの標準結果辞書ヘルパー）のテスト。"""
from unittest.mock import AsyncMock

import pytest

from lilla_core.tool_support.tool_result import (
    tool_error,
    tool_needs_auth,
    tool_reauth_required,
    tool_success,
)

_RESULT_KEYS = {
    "success",
    "tool_name",
    "memory_entry",
    "needs_auth",
    "needs_auth_list",
    "data",
    "error",
}


class TestToolSuccess:
    """`tool_success` が成功時の標準形式を返すこと。"""

    def test_returns_standard_keys(self):
        """標準の 7 キーだけを持つ辞書を返すこと。"""
        result = tool_success("get_events", "3 件取得", {"items": []})
        assert set(result.keys()) == _RESULT_KEYS

    def test_success_fields(self):
        """成功フラグ・ツール名・memory_entry・data がそのまま入ること。"""
        data = {"items": [1, 2, 3]}
        result = tool_success("get_events", "3 件取得", data)
        assert result["success"] is True
        assert result["tool_name"] == "get_events"
        assert result["memory_entry"] == "3 件取得"
        assert result["data"] is data
        assert result["error"] is None

    def test_no_auth_required(self):
        """成功時は認証要求のフィールドが空になること。"""
        result = tool_success("get_events", "ok", None)
        assert result["needs_auth"] is False
        assert result["needs_auth_list"] == []


class TestToolError:
    """`tool_error` が失敗時（認証不要）の標準形式を返すこと。"""

    def test_returns_standard_keys(self):
        """標準の 7 キーだけを持つ辞書を返すこと。"""
        result = tool_error("get_events", "取得に失敗", "HTTP 500")
        assert set(result.keys()) == _RESULT_KEYS

    def test_error_fields(self):
        """失敗フラグ・エラーメッセージが入り data が None になること。"""
        result = tool_error("get_events", "取得に失敗", "HTTP 500")
        assert result["success"] is False
        assert result["tool_name"] == "get_events"
        assert result["memory_entry"] == "取得に失敗"
        assert result["error"] == "HTTP 500"
        assert result["data"] is None

    def test_no_auth_required(self):
        """認証不要の失敗では needs_auth が立たないこと。"""
        result = tool_error("get_events", "取得に失敗", "HTTP 500")
        assert result["needs_auth"] is False
        assert result["needs_auth_list"] == []


class TestToolNeedsAuth:
    """`tool_needs_auth` が再認証要求の標準形式を返すこと。"""

    def test_returns_standard_keys(self):
        """標準の 7 キーだけを持つ辞書を返すこと。"""
        result = tool_needs_auth("get_events", "https://example.com/auth", "要認証")
        assert set(result.keys()) == _RESULT_KEYS

    def test_needs_auth_fields(self):
        """needs_auth が立ち、message が memory_entry と error の両方に入ること。"""
        result = tool_needs_auth("get_events", "https://example.com/auth", "要認証")
        assert result["success"] is False
        assert result["needs_auth"] is True
        assert result["memory_entry"] == "要認証"
        assert result["error"] == "要認証"
        assert result["data"] is None

    def test_default_auth_service(self):
        """auth_service の既定値が "google" になること。"""
        result = tool_needs_auth("get_events", "https://example.com/auth", "要認証")
        assert result["needs_auth_list"] == [
            {"auth_service": "google", "auth_url": "https://example.com/auth"}
        ]

    def test_custom_auth_service(self):
        """auth_service を指定すればそれが needs_auth_list に入ること。"""
        result = tool_needs_auth(
            "get_tasks", "https://example.com/auth", "要認証", auth_service="notion"
        )
        assert result["needs_auth_list"] == [
            {"auth_service": "notion", "auth_url": "https://example.com/auth"}
        ]


class TestToolReauthRequired:
    """`tool_reauth_required` が認証フローを開始して結果を組み立てること。"""

    async def test_starts_authentication_and_builds_result(self):
        """client.start_authentication() の URL で needs_auth 辞書を組み立てること。"""
        client = AsyncMock()
        client.start_authentication.return_value = "https://example.com/authorize"

        result = await tool_reauth_required(client, "get_events", "再認証が必要")

        client.start_authentication.assert_awaited_once_with()
        assert result["success"] is False
        assert result["needs_auth"] is True
        assert result["tool_name"] == "get_events"
        assert result["needs_auth_list"] == [
            {"auth_service": "google", "auth_url": "https://example.com/authorize"}
        ]

    async def test_passes_auth_service(self):
        """auth_service がそのまま needs_auth_list に渡ること。"""
        client = AsyncMock()
        client.start_authentication.return_value = "https://example.com/authorize"

        result = await tool_reauth_required(
            client, "get_tasks", "再認証が必要", auth_service="notion"
        )

        assert result["needs_auth_list"][0]["auth_service"] == "notion"

    async def test_propagates_authentication_error(self):
        """認証フローの開始が失敗したら例外をそのまま伝播すること。"""
        client = AsyncMock()
        client.start_authentication.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            await tool_reauth_required(client, "get_events", "再認証が必要")
