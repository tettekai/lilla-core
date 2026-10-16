"""`llm.usage_log` とプロバイダーの単価（#239）の設定検証。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from lilla_core.core.config import LlmConfig, LlmProviderConfig, LlmUsageLogConfig


def _llm(**kw) -> LlmConfig:
    """具体プロバイダー 1 つだけの `llm:` 節を組み立てる。"""
    return LlmConfig(
        default="p",
        providers={"p": {"type": "ollama", "url": "http://u", "model": "m"}},
        **kw,
    )


class TestUsageLogConfig:
    def test_defaults_when_omitted(self) -> None:
        cfg = _llm()
        assert cfg.usage_log.enabled is True
        assert cfg.usage_log.retention_days == 90

    def test_null_is_default(self) -> None:
        assert _llm(usage_log=None).usage_log == LlmUsageLogConfig()

    def test_custom_values(self) -> None:
        cfg = _llm(usage_log={"enabled": False, "retention_days": 7})
        assert cfg.usage_log.enabled is False
        assert cfg.usage_log.retention_days == 7

    @pytest.mark.parametrize("days", [0, -1])
    def test_retention_must_be_positive(self, days) -> None:
        with pytest.raises(ValidationError):
            _llm(usage_log={"retention_days": days})


class TestProviderPrices:
    def test_prices_on_concrete_provider(self) -> None:
        provider = LlmProviderConfig(
            type="openai_compat",
            url="http://u",
            model="m",
            input_price_per_million_tokens=1.25,
            output_price_per_million_tokens=10,
        )
        assert provider.input_price_per_million_tokens == 1.25
        assert provider.output_price_per_million_tokens == 10

    def test_prices_default_to_none(self) -> None:
        provider = LlmProviderConfig(type="ollama", url="http://u", model="m")
        assert provider.input_price_per_million_tokens is None
        assert provider.output_price_per_million_tokens is None

    def test_negative_price_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must not be negative"):
            LlmProviderConfig(
                type="ollama", url="http://u", model="m", output_price_per_million_tokens=-1
            )

    def test_resolver_must_not_set_price(self) -> None:
        with pytest.raises(ValidationError, match="input_price_per_million_tokens"):
            LlmProviderConfig(
                type="resolver", script="r.py", fallback="p", input_price_per_million_tokens=1
            )
