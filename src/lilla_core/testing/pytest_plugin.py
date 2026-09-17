"""拡張リポジトリ向けの pytest fixture。

利用側のルート `conftest.py` に次の 1 行を書いて読み込む（`pytest11` entry point に
よる自動登録はしない。利用側の既存 conftest と黙って干渉しないよう opt-in にしている）::

    pytest_plugins = ["lilla_core.testing.pytest_plugin"]

pytest に依存するのはこのモジュールだけで、`lilla_core.testing` 本体（pytest 非依存）
から分けてある。
"""
from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lilla_core.testing import use_extensions, write_minimal_lilla_yaml

if TYPE_CHECKING:  # 型のみ（`lilla_core.testing` と同じく実行時 import を避ける）
    from lilla_core.core.config import AppConfig
    from lilla_core.core.extension import Extension

_DUMMY_DISCORD_TOKEN = "dummy-token-for-testing"


@pytest.fixture
def lilla_config_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """最小構成の `lilla.yaml` を置いたテンポラリの `CONFIG_ROOT` を用意する。

    開発者のシェルに `CONFIG_ROOT` が設定されていると実ファイルの設定を読んで
    しまうため、`setdefault` ではなく常に上書きする（`DISCORD_TOKEN` は
    `EnvConfig` の必須フィールドなので、未設定のときだけダミー値を入れる）。

    Returns:
        `lilla.yaml` を書き出したディレクトリ。
    """
    write_minimal_lilla_yaml(tmp_path)
    monkeypatch.setenv("CONFIG_ROOT", str(tmp_path))
    if "DISCORD_TOKEN" not in os.environ:
        monkeypatch.setenv("DISCORD_TOKEN", _DUMMY_DISCORD_TOKEN)
    return tmp_path


@pytest.fixture
def lilla_extensions(
    lilla_config_root: Path,
) -> "Iterator[Callable[..., AppConfig]]":
    """拡張を登録して合成済みの設定を返す呼び出し可能オブジェクトを渡す。

    `register(*extensions) -> AppConfig` は内部で `use_extensions()` に入り、
    テストの teardown でまとめて抜ける（テスト中に複数回呼んだ場合は後入れ先出しで
    戻す）::

        def test_config_section_is_composed(lilla_extensions):
            cfg = lilla_extensions(extension)
            assert cfg.my_section.value == "..."
    """
    with ExitStack() as stack:
        def register(*extensions: "Extension") -> "AppConfig":
            """拡張を登録し、合成済みの `AppConfig` を返す。"""
            return stack.enter_context(
                use_extensions(*extensions, config_root=lilla_config_root)
            )

        yield register
