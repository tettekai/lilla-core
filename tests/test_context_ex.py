"""context_ex.py（`ContextEx`）のテスト。"""
from unittest.mock import AsyncMock

import pytest

from lilla_core.tool_support.context_ex import ContextEx
from lilla_core.tool_support.tool_response_ex import ToolResponseEx


@pytest.fixture
def call_tool():
    """`call_tool` として注入する AsyncMock を返す。"""
    return AsyncMock(return_value={"success": True, "data": {"count": 1}})


@pytest.fixture
def context(call_tool):
    """`call_tool` が注入された最小のツール実行 context を返す。"""
    return {
        "call_tool": call_tool,
        "client_type": "discord",
        "params": {"key": "value"},
    }


class TestContextExConstruction:
    """生成時の検証。"""

    def test_accepts_context_with_call_tool(self, context):
        """call_tool が注入されていれば生成できること。"""
        assert isinstance(ContextEx(context), ContextEx)

    def test_of_creates_instance(self, context):
        """of() でも同じように生成できること。"""
        assert isinstance(ContextEx.of(context), ContextEx)

    def test_raises_when_call_tool_missing(self):
        """call_tool が無い context は ValueError になること。"""
        with pytest.raises(ValueError, match="call_tool is not injected into context"):
            ContextEx({"client_type": "discord"})

    def test_raises_when_call_tool_is_none(self):
        """call_tool が None の context も ValueError になること。"""
        with pytest.raises(ValueError, match="call_tool is not injected into context"):
            ContextEx({"call_tool": None})


class TestContextExAccess:
    """context の値の読み出し。"""

    def test_get_returns_value(self, context):
        """get() が context の値を返すこと。"""
        assert ContextEx.of(context).get("client_type") == "discord"

    def test_get_returns_default_for_missing_key(self, context):
        """get() がキー不在時に default を返すこと。"""
        assert ContextEx.of(context).get("missing", "fallback") == "fallback"

    def test_get_default_is_none(self, context):
        """get() の default を省略したキー不在は None を返すこと。"""
        assert ContextEx.of(context).get("missing") is None

    def test_getitem_returns_value(self, context):
        """添字アクセスが context の値を返すこと。"""
        assert ContextEx.of(context)["params"] == {"key": "value"}

    def test_getitem_raises_for_missing_key(self, context):
        """添字アクセスはキー不在で KeyError になること。"""
        with pytest.raises(KeyError):
            ContextEx.of(context)["missing"]

    def test_call_tool_property_returns_injected_callable(self, context, call_tool):
        """call_tool プロパティが注入された関数そのものを返すこと。"""
        assert ContextEx.of(context).call_tool is call_tool


class TestContextExCallTool:
    """`call_tool` 経由のツール呼び出し。"""

    async def test_call_tool_invokes_injected_callable(self, context, call_tool):
        """call_tool() が注入された関数を引数そのままで呼ぶこと。"""
        ctx = ContextEx.of(context)

        result = await ctx.call_tool("get_events", {"date_range": "today"})

        call_tool.assert_awaited_once_with("get_events", {"date_range": "today"})
        assert result == {"success": True, "data": {"count": 1}}

    async def test_call_tool_and_wrap_returns_tool_response_ex(self, context, call_tool):
        """call_tool_and_wrap() が結果を ToolResponseEx でラップして返すこと。"""
        ctx = ContextEx.of(context)

        response = await ctx.call_tool_and_wrap("get_events", {"date_range": "today"})

        call_tool.assert_awaited_once_with("get_events", {"date_range": "today"})
        assert isinstance(response, ToolResponseEx)
        assert response.success is True
        assert response.data("count") == 1

    async def test_call_tool_and_wrap_keeps_failure(self, context, call_tool):
        """失敗レスポンスもそのままラップして読めること。"""
        call_tool.return_value = {
            "success": False,
            "error": "HTTP 500",
            "data": None,
        }
        ctx = ContextEx.of(context)

        response = await ctx.call_tool_and_wrap("get_events", {})

        assert response.success is False
        assert response.error == "HTTP 500"
        assert response.data() == {}

    async def test_call_tool_and_wrap_propagates_exception(self, context, call_tool):
        """呼び出し先の例外はそのまま伝播すること。"""
        call_tool.side_effect = RuntimeError("boom")
        ctx = ContextEx.of(context)

        with pytest.raises(RuntimeError, match="boom"):
            await ctx.call_tool_and_wrap("get_events", {})
