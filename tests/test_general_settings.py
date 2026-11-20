"""`lilla_core.services.general_settings` のテスト。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from lilla_core.services import general_settings


@pytest.fixture
def cfg(monkeypatch) -> SimpleNamespace:
    """全般に出す項目だけを持つ設定を `get_config()` に差し込む。"""
    cfg = SimpleNamespace(
        ui=SimpleNamespace(locale="ja", timezone="Asia/Tokyo"),
        llm=SimpleNamespace(
            default="grok",
            max_tool_call_iterations=10,
            providers={
                "grok": SimpleNamespace(type="openai_compat", model="grok-4", url="https://secret.example"),
                "pick": SimpleNamespace(type="resolver", model=None, url=None),
            },
        ),
        scheduler=SimpleNamespace(enabled=False),
        dashboard=SimpleNamespace(host="127.0.0.1", port=8765),
        http=SimpleNamespace(host="0.0.0.0", port=8080),
        env=SimpleNamespace(discord_token="secret-token"),
    )
    monkeypatch.setattr(general_settings, "get_config", lambda: cfg)
    monkeypatch.setattr("lilla_core.core.config.get_config", lambda: cfg)
    return cfg


class TestBuildGeneralSettings:
    """設定画面「全般」の内容。"""

    def test_summarizes_core_settings(self, cfg, make_extension, use_extensions, monkeypatch) -> None:
        """コアの設定・実行時の状態・読み込んでいる拡張をまとめる。"""
        monkeypatch.setattr("lilla_core.core.runtime_state.get_active_llm_name", lambda: "pick")
        monkeypatch.setattr("lilla_core.core.runtime_state.is_tools_disabled", lambda: True)
        use_extensions(make_extension("habits", display_name="習慣"))

        data = general_settings.build_general_settings()

        assert data == {
            "ui": {"locale": "ja", "timezone": "Asia/Tokyo"},
            "llm": {
                "default": "grok",
                "active": "pick",
                "max_tool_call_iterations": 10,
                "providers": [
                    {"name": "grok", "type": "openai_compat", "model": "grok-4"},
                    {"name": "pick", "type": "resolver", "model": None},
                ],
            },
            "tools_disabled": True,
            "scheduler_enabled": False,
            "dashboard": {"host": "127.0.0.1", "port": 8765},
            "http": {"host": "0.0.0.0", "port": 8080},
            "extensions": [{"name": "habits", "display_name": "習慣", "kind": "external"}],
        }

    def test_does_not_include_secrets_or_urls(self, cfg, use_extensions) -> None:
        """秘匿情報とプロバイダーの URL は載せない。"""
        use_extensions()

        text = repr(general_settings.build_general_settings())

        assert "secret-token" not in text
        assert "secret.example" not in text
