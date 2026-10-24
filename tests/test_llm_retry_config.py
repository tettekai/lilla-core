"""プロバイダーの再試行の設定（#253）の検証。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from lilla_core.core.config import LlmProviderConfig


def _concrete(provider_type: str = "openai_compat", **kw) -> LlmProviderConfig:
    """具体プロバイダーの設定を組み立てる。"""
    return LlmProviderConfig(type=provider_type, url="http://u", model="m", **kw)


class TestProviderRetryConfig:
    @pytest.mark.parametrize("provider_type", ["ollama", "openai_compat"])
    def test_defaults(self, provider_type) -> None:
        provider = _concrete(provider_type)
        assert provider.max_retries == 2
        assert provider.retry_backoff_seconds == 1.0
        assert provider.retry_max_wait_seconds == 30.0
        assert provider.retry_on_timeout is False

    def test_explicit_values(self) -> None:
        provider = _concrete(
            max_retries=0,
            retry_backoff_seconds=0.5,
            retry_max_wait_seconds=10,
            retry_on_timeout=True,
        )
        assert provider.max_retries == 0
        assert provider.retry_backoff_seconds == 0.5
        assert provider.retry_max_wait_seconds == 10.0
        assert provider.retry_on_timeout is True

    def test_negative_max_retries_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="max_retries must not be negative"):
            _concrete(max_retries=-1)

    @pytest.mark.parametrize("value", [True, 1.5, "2"])
    def test_non_int_max_retries_is_rejected(self, value) -> None:
        with pytest.raises(ValidationError):
            _concrete(max_retries=value)

    @pytest.mark.parametrize("name", ["retry_backoff_seconds", "retry_max_wait_seconds"])
    @pytest.mark.parametrize("value", [0, -1])
    def test_non_positive_seconds_are_rejected(self, name, value) -> None:
        with pytest.raises(ValidationError, match=f"{name} must be positive"):
            _concrete(**{name: value})

    def test_non_bool_retry_on_timeout_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _concrete(retry_on_timeout="yes")

    @pytest.mark.parametrize(
        ("name", "value"),
        [
            ("max_retries", 0),
            ("retry_backoff_seconds", 1.0),
            ("retry_max_wait_seconds", 5.0),
            ("retry_on_timeout", False),
        ],
    )
    def test_resolver_must_not_set(self, name, value) -> None:
        with pytest.raises(ValidationError, match=f"must not set: {name}"):
            LlmProviderConfig(type="resolver", script="r.py", fallback="p", **{name: value})

    def test_resolver_keeps_retry_fields_unset(self) -> None:
        provider = LlmProviderConfig(type="resolver", script="r.py", fallback="p")
        assert provider.max_retries is None
        assert provider.retry_on_timeout is None
