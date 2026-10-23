"""`llm.on_tool_call_limit`（#255）の設定検証。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from lilla_core.core.config import LlmConfig


def _llm(**kw) -> LlmConfig:
    """具体プロバイダー 1 つだけの `llm:` 節を組み立てる。"""
    return LlmConfig(
        default="p",
        providers={"p": {"type": "ollama", "url": "http://u", "model": "m"}},
        **kw,
    )


class TestOnToolCallLimitConfig:
    def test_default_is_summarize(self) -> None:
        """省略時はツールなしのまとめを返す `summarize`。"""
        assert _llm().on_tool_call_limit == "summarize"

    @pytest.mark.parametrize("mode", ["summarize", "message"])
    def test_accepts_known_modes(self, mode: str) -> None:
        """`summarize` / `message` を受け付ける。"""
        assert _llm(on_tool_call_limit=mode).on_tool_call_limit == mode

    @pytest.mark.parametrize("mode", ["", "Summarize", "abort", None])
    def test_rejects_unknown_modes(self, mode) -> None:
        """それ以外の値は起動時に落とす。"""
        with pytest.raises(ValidationError):
            _llm(on_tool_call_limit=mode)
