"""`files` 拡張のツールのテストで共通の fixture。

`tmp_path` に読み取り専用・書き込み可能・ホワイトリスト外のディレクトリを作り、
ツールが読む設定をそこへ向けて、ツール本体をファイルパスからロードする。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from lilla_core.extensions import files

_TOOLS_DIR = Path(files.__file__).resolve().parent / "tools"


@pytest.fixture
def dirs(tmp_path: Path) -> dict[str, Path]:
    """次の構成のディレクトリを作って返す。

    - `docs`（読み取り専用）/ `readme.md`・`sub/deep.txt`
    - `work`（書き込み可能）/ `memo.txt`・`archive/`（読み取り専用として入れ子）/ `archive/old.txt`
    - `outside`（ホワイトリスト外）/ `secret.txt`
    """
    docs = tmp_path / "docs"
    (docs / "sub").mkdir(parents=True)
    (docs / "readme.md").write_text("# Readme\nhello world\n", encoding="utf-8")
    (docs / "sub" / "deep.txt").write_text("deep line\nHello again\n", encoding="utf-8")

    work = tmp_path / "work"
    (work / "archive").mkdir(parents=True)
    (work / "memo.txt").write_text("memo\n", encoding="utf-8")
    (work / "archive" / "old.txt").write_text("old\n", encoding="utf-8")

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret hello\n", encoding="utf-8")
    return {"docs": docs, "work": work, "outside": outside, "archive": work / "archive"}


@pytest.fixture
def files_config(dirs) -> files.FilesConfig:
    """`docs` と `work/archive` を読み取り専用、`work` を書き込み可能にした設定。"""
    return files.FilesConfig(
        read_only_dirs=[str(dirs["docs"]), str(dirs["archive"])],
        writable_dirs=[str(dirs["work"])],
    )


@pytest.fixture
def use_config(files_config, monkeypatch: pytest.MonkeyPatch) -> files.FilesConfig:
    """ツールが読む設定を `files_config` に差し替え、エラー通知をモックにする。"""
    from lilla_core.extensions.files import tool_support

    monkeypatch.setattr(tool_support, "load_config", lambda: files_config)
    return files_config


@pytest.fixture
def notify(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """`notify_error` をモックにして返す。"""
    from lilla_core.core import error_notify

    mock = AsyncMock()
    monkeypatch.setattr(error_notify, "notify_error", mock)
    return mock


@pytest.fixture
def load_tool():
    """同梱のツールをファイルパスからロードする関数を返す（テスト後に `sys.modules` から外す）。"""
    loaded: list[str] = []

    def _load(stem: str):
        sys.modules.pop(stem, None)
        spec = importlib.util.spec_from_file_location(stem, str(_TOOLS_DIR / f"{stem}.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        loaded.append(stem)
        return module

    yield _load
    for stem in loaded:
        sys.modules.pop(stem, None)
