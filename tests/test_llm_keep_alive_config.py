"""プロバイダーの `keep_alive`（#261）の設定検証。"""
from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from lilla_core.core.config import LlmProviderConfig


def _ollama(**kw) -> LlmProviderConfig:
    """`type: ollama` のプロバイダー設定を組み立てる。"""
    return LlmProviderConfig(type="ollama", url="http://u", model="m", **kw)


class TestProviderKeepAlive:
    def test_defaults_to_none(self) -> None:
        assert _ollama().keep_alive is None

    @pytest.mark.parametrize("value", ["30m", "1h", -1, 0, 300])
    def test_accepts_string_or_int(self, value) -> None:
        assert _ollama(keep_alive=value).keep_alive == value

    def test_yaml_duration_is_kept_as_string(self) -> None:
        data = yaml.safe_load("type: ollama\nurl: http://u\nmodel: m\nkeep_alive: 30m\n")
        assert LlmProviderConfig(**data).keep_alive == "30m"

    @pytest.mark.parametrize("value", [True, False, 1.5, ["30m"], {"m": 30}])
    def test_rejects_non_string_non_int(self, value) -> None:
        with pytest.raises(ValidationError):
            _ollama(keep_alive=value)

    def test_openai_compat_must_not_set(self) -> None:
        with pytest.raises(ValidationError, match="must not set: keep_alive"):
            LlmProviderConfig(type="openai_compat", url="http://u", model="m", keep_alive="30m")

    def test_resolver_must_not_set(self) -> None:
        with pytest.raises(ValidationError, match="must not set: keep_alive"):
            LlmProviderConfig(type="resolver", script="r.py", fallback="p", keep_alive=-1)
