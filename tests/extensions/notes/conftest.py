"""`notes` 拡張のテストで共通の fixture。

`tmp_path` に本物の bare Git リポジトリを作り、`service._repo` と `service.load_config` を
差し替える（MongoDB のような外部サービスは要らない）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from lilla_core.extensions import notes

_TOOLS_DIR = Path(notes.__file__).resolve().parent / "tools"


@pytest.fixture
def notes_config() -> notes.NotesConfig:
    """小さめの上限を持つ設定。"""
    return notes.NotesConfig(max_content_chars=100, max_read_chars=10)


@pytest.fixture
async def notes_repo(notes_config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`tmp_path` の bare リポジトリを作り、`service` が使うリポジトリと設定を差し替える。"""
    from lilla_core.extensions.notes import service
    from lilla_core.extensions.notes.repository import NotesRepository

    repo = NotesRepository(tmp_path / "notes.git")
    await repo.init_repository()
    monkeypatch.setattr(service, "_repo", lambda: repo)
    monkeypatch.setattr(service, "load_config", lambda: notes_config)
    return repo


@pytest.fixture
def alert(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """大量削除の通知（`notify_warning`）をモックにして返す。"""
    from lilla_core.core import error_notify

    mock = AsyncMock()
    monkeypatch.setattr(error_notify, "notify_warning", mock)
    return mock


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


@pytest.fixture
def declare_notes_dirs():
    """notes 拡張と、指定したディレクトリを宣言するテスト用の拡張を登録する関数を返す。

    登録内容はテストの終わりに元へ戻す。
    """
    from lilla_core.core import extension as ext_module
    from lilla_core.core.extension import Extension

    previous = ext_module.get_extensions()

    def _declare(dirs: dict[str, dict[str, str]], name: str = "journal-ext") -> None:
        class _Declaring(Extension):
            def notes_directories(self):
                return dirs

        declaring = _Declaring()
        declaring.name = name
        ext_module.set_extensions([notes.extension, declaring])

    yield _declare
    ext_module.set_extensions(previous)


@pytest.fixture
def journal_dir(declare_notes_dirs) -> None:
    """拡張が `journal/` を宣言した状態にする。"""
    declare_notes_dirs({"journal": {"ja": "日誌用", "en": "for the journal"}})
