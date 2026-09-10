"""ツール実行結果（success/tool_name/data/error形式のdict）を便利に扱うための薄いラッパー。

ContextEx と同じ位置づけ（opt-in）。execute() の戻り値の契約は変えず、
ツール本体の実装の中でだけ、任意に使う。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


class ToolResponseEx:
    """call_tool() などの戻り値(dict)を便利に扱うための薄いラッパー。"""

    def __init__(self, response: dict) -> None:
        """レスポンスdictを内包する。

        Parameters
        ----------
        response : dict
            success/tool_name/data/error などを含むツール実行結果の辞書
        """
        self._response = response

    @classmethod
    def of(cls, response: dict) -> "ToolResponseEx":
        """dict のレスポンスから ToolResponseEx を作る。"""
        return cls(response)

    @property
    def success(self) -> bool:
        """レスポンスが成功しているかどうかを返す。"""
        return bool(self._response.get("success"))

    @property
    def error(self) -> str | None:
        """エラーメッセージを返す（無ければ None）。"""
        return self._response.get("error")

    def data(self, key: str | None = None, default: Any = None) -> Any:
        """data全体、または data[key] を取得する。

        失敗時・キー不在時に加え、キーは存在するが値が None の場合も default を返す。
        """
        data = self._response.get("data") or {}
        if key is None:
            return data
        value = data.get(key, default)
        return default if value is None else value

    def map_data(self, transform: Callable[[dict], dict]) -> dict:
        """成功時のみ transform(data) で data を差し替えたレスポンスdictを返す。

        失敗時は元のレスポンスをそのまま返す（Optional.mapと同じ発想）。
        """
        if not self.success:
            return self._response
        return {**self._response, "data": transform(self.data())}

    def unwrap(self) -> dict:
        """元のdictをそのまま返す（executeの戻り値として使う場合など）。"""
        return self._response
