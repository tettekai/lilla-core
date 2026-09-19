"""拡張リポジトリ向けのテストヘルパー（pytest 非依存）。

拡張を別リポジトリで開発すると、どのリポジトリも「自分の `Extension` を
`set_extensions()` で登録し、`compose_config()` で設定を合成し、テストが終わったら
元へ戻す」という同じセットアップを書くことになる。そこだけを肩代わりするのが
このモジュールで、`lilla_core` の本番依存だけで動く（pytest には触らない）。

pytest の fixture として使いたい場合は `lilla_core.testing.pytest_plugin` を
利用側の `conftest.py` で `pytest_plugins` に並べること（自動登録はしない）。
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:  # 実行時にコアの設定モジュールを引っ張らないよう、型のみ import する
    from lilla_core.core.config import AppConfig
    from lilla_core.core.extension import Extension

__all__ = ["use_extensions", "write_minimal_lilla_yaml"]


@contextmanager
def use_extensions(
    *extensions: "Extension", config_root: Path | None = None
) -> "Iterator[AppConfig]":
    """拡張を登録して設定を合成し、抜けるときにプロセスの状態を元へ戻す。

    登録内容（`core/extension.py`）と設定インスタンス（`core/config.py` の
    `_config_instance`）はどちらもプロセス全体で共有されるモジュール状態のため、
    テストごとに退避・復元しないと後続のテストへ漏れる。合成が書き換える
    OS 変数名のレジストリ（`_extra_env_var_names`）と既定設定のキャッシュも
    まとめて戻す。

    `get_config()` は未設定でも既定の `AppConfig` を返してしまうので、退避は
    `get_config()` ではなくモジュール変数を直接読む。

    Args:
        extensions: ロード順に並べた `Extension` インスタンス。
        config_root: `lilla.yaml` を置いたディレクトリ。渡すと合成の間だけ
            `CONFIG_ROOT` をここへ向ける（`None` なら現在の値のまま）。

    Yields:
        合成済みの `AppConfig`（`get_config()` が返すものと同一インスタンス）。

    Raises:
        ValueError: 拡張の登録が衝突検査に引っかかった場合。
        pydantic.ValidationError: 合成後のモデルで設定の検証に失敗した場合。
    """
    # 他のテストが `sys.modules` からコアのモジュールを取り除くことがあるため、
    # import 時に束縛せず呼び出しのたびに解決する。
    from lilla_core.core import config as config_module
    from lilla_core.core import extension as extension_module

    saved_extensions = extension_module.get_extensions()
    saved_config = config_module._config_instance
    saved_var_names = dict(config_module._extra_env_var_names)
    saved_config_root = os.environ.get("CONFIG_ROOT")

    try:
        if config_root is not None:
            os.environ["CONFIG_ROOT"] = str(config_root)
        extension_module.set_extensions(list(extensions))
        composed = config_module.compose_config(
            extension_module.get_config_models(),
            extension_module.get_env_fields(),
        )
        config_module.set_config(composed)
        yield composed
    finally:
        if saved_extensions:
            extension_module.set_extensions(saved_extensions)
        else:
            extension_module.reset_extensions()
        if saved_config is None:
            config_module._config_instance = None
        else:
            config_module.set_config(saved_config)
        config_module._extra_env_var_names.clear()
        config_module._extra_env_var_names.update(saved_var_names)
        config_module._default_config.cache_clear()
        if saved_config_root is None:
            os.environ.pop("CONFIG_ROOT", None)
        else:
            os.environ["CONFIG_ROOT"] = saved_config_root


def _deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """dict を再帰的にマージした新しい dict を返す（`overrides` が勝つ）。

    どちらの値も dict のキーだけ潜り、それ以外は `overrides` の値で置き換える。
    """
    merged = dict(base)
    for key, value in overrides.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def write_minimal_lilla_yaml(
    directory: Path,
    *,
    my_user_id: str = "123456789",
    llm_name: str = "dummy",
    extra: dict[str, Any] | None = None,
) -> Path:
    """テスト用の最小構成 `lilla.yaml` を書き出してそのパスを返す。

    コアが必須にしている項目（`discord.my_user_id` と、`llm.default` に対応する
    `llm.providers.<名前>`）だけを書く。`ui.timezone` は「人間側の今日 / いま」が
    実行環境の OS タイムゾーンに左右されないよう `Asia/Tokyo` で固定する。

    Args:
        directory: 書き出し先ディレクトリ（`CONFIG_ROOT` に指定する想定）。
        my_user_id: `discord.my_user_id` に書く値。
        llm_name: `llm.default` と `llm.providers` のキーに使う名前。
        extra: 追加で書くセクション。同じネストで深いマージをするので、拡張が
            必須にしているセクション（`{"habits": {"channel": "x"}}` など）を足せる。

    Returns:
        書き出した `lilla.yaml` のパス。
    """
    content: dict[str, Any] = {
        "discord": {"my_user_id": my_user_id},
        "llm": {
            "default": llm_name,
            "providers": {
                llm_name: {
                    "type": "ollama",
                    "url": "http://localhost:11434",
                    "model": "dummy",
                },
            },
        },
        "ui": {"timezone": "Asia/Tokyo"},
    }
    if extra:
        content = _deep_merge(content, extra)

    path = Path(directory) / "lilla.yaml"
    path.write_text(
        yaml.safe_dump(content, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return path
