"""ツール開発者向けの Context 便利ラッパー。

execute(input: dict, context: dict) -> dict という既存の契約は変えず、
ツール本体の実装の中でだけ、任意に使う薄いラッパークラス（opt-in）。
context(dict) をそのまま内包するだけで、フィールドを固定しない。

ローダー側（execute_tool_call など）の dict ベースの処理には一切影響しない。
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from lilla_core.tool_support.tool_response_ex import ToolResponseEx


class ContextEx:
    """execute() の context(dict) を便利に扱うための薄いラッパー。"""

    def __init__(self, context: dict) -> None:
        """context dictを内包する。

        Parameters
        ----------
        context : dict
            call_tool などの実行コンテキストを含む辞書

        Raises
        ------
        ValueError
            context に call_tool が注入されていない場合
        """
        if context.get("call_tool") is None:
            raise ValueError("call_tool is not injected into context")
        self._context = context

    @classmethod
    def of(cls, context: dict) -> "ContextEx":
        """dict の context から ContextEx を作る。"""
        return cls(context)

    def get(self, key: str, default: Any = None) -> Any:
        """context からキーを取得する（dict.get と同じ挙動）。"""
        return self._context.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self._context[key]

    @property
    def call_tool(self) -> Callable[[str, dict], Awaitable[dict]]:
        """context["call_tool"] をそのまま返す。await ctx.call_tool(...) の形で使う。"""
        return self._context["call_tool"]

    async def call_tool_and_wrap(self, tool_name: str, tool_input: dict) -> ToolResponseEx:
        """call_tool() を呼び、結果を ToolResponseEx でラップして返す。"""
        result = await self.call_tool(tool_name, tool_input)
        return ToolResponseEx.of(result)
