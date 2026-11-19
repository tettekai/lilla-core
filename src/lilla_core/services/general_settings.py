"""観測用ダッシュボードの設定画面「全般」に出す、コアの今の動作の要約。

`lilla.yaml` が正本の値を画面で確かめるための読み取り専用の要約で、ここから
値を変える経路は持たない（YAML と二重管理にしないため）。秘匿情報（`cfg.env.*`）と、
内部のホスト名を含みうるプロバイダーの URL は載せない。
"""
from __future__ import annotations

from lilla_core.core.config import get_config
from lilla_core.core.extension import extension_display_name, get_extensions


def build_general_settings() -> dict:
    """設定画面「全般」の内容を組み立てる。

    設定は呼び出しのたびに `get_config()` で引く。

    Returns:
        `ui`（`locale` / `timezone`）・`llm`（`default` / `active` / `max_tool_call_iterations` /
        `providers`（`name` / `type` / `model`））・`tools_disabled`・`scheduler_enabled`・
        `dashboard` / `http`（`host` / `port`）・`extensions`（`name` / `display_name` /
        `kind`）を持つ dict。`active` は `!model` で切り替えた今のプロバイダー
        （切り替えていなければ `None`）。
    """
    from lilla_core.core import runtime_state
    from lilla_core.services.extension_overrides import extension_kind
    from lilla_core.services.task_schedules import timezone_label

    cfg = get_config()
    providers = [
        {"name": name, "type": provider.type, "model": provider.model}
        for name, provider in cfg.llm.providers.items()
    ]
    return {
        "ui": {"locale": cfg.ui.locale, "timezone": timezone_label()},
        "llm": {
            "default": cfg.llm.default,
            "active": runtime_state.get_active_llm_name(),
            "max_tool_call_iterations": cfg.llm.max_tool_call_iterations,
            "providers": providers,
        },
        "tools_disabled": runtime_state.is_tools_disabled(),
        "scheduler_enabled": cfg.scheduler.enabled,
        "dashboard": {"host": cfg.dashboard.host, "port": cfg.dashboard.port},
        "http": {"host": cfg.http.host, "port": cfg.http.port},
        "extensions": [
            {
                "name": ext.name,
                "display_name": extension_display_name(ext),
                "kind": extension_kind(ext),
            }
            for ext in get_extensions()
        ],
    }
