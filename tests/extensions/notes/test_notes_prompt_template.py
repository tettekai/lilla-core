"""プロンプトのテンプレートの関数 `notes()` を、本物の保存先 `notes` で確かめるテスト。

`tmp_path` に本物の bare Git リポジトリを作り（`notes_repo` fixture）、保存先 `notes` として
`NotesStorage` を登録する。notes の外（`..`・絶対パス）を指すパスが展開の失敗になり、
外のファイルの本文が出てこないことを `notes()` の経路で固定する。
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from lilla_core.core import extension as core_extension
from lilla_core.extensions.notes.storage import NotesStorage
from lilla_core.services.prompt_template import (
    PromptTemplateError,
    render_or_raw,
    render_template,
)

_SECRET = "OUTSIDE-SECRET"


@pytest.fixture
def notes_storage(notes_repo, monkeypatch: pytest.MonkeyPatch):
    """保存先 `notes` として `NotesStorage` を登録し、リポジトリを返す。"""
    monkeypatch.setitem(core_extension._file_storages, "notes", NotesStorage())
    return notes_repo


@pytest.fixture
def outside_file(tmp_path: Path) -> Path:
    """notes のリポジトリの外（`tmp_path` の直下）に置いたファイル。"""
    path = tmp_path / "outside.md"
    path.write_text(_SECRET, encoding="utf-8")
    return path


async def _write(repo, files: dict[str, str | None]) -> None:
    """リポジトリへ直接コミットする（書き込める場所の規則を通さずに置く）。"""
    async with repo.write_lock:
        await repo.commit(files, "create: test")


def _outside_paths(outside_file: Path) -> list[str]:
    """notes の外を指すパスの例。"""
    return [
        "../outside.md",
        "memory/../../outside.md",
        "./outside.md",
        str(outside_file),
        "/etc/passwd",
    ]


class TestNotesFunctionWithRealStorage:
    """本物の保存先 `notes` で `notes()` を展開する。"""

    async def test_reads_note(self, notes_storage) -> None:
        """リポジトリにあるノートの本文を展開する。"""
        await _write(notes_storage, {"memory/core.md": "覚えたこと\n"})
        assert await render_template('[{{ notes("memory/core.md") }}]') == "[覚えたこと\n]"

    async def test_missing_note_is_empty(self, notes_storage) -> None:
        """無いノートは空文字。"""
        assert await render_template('[{{ notes("memory/core.md") }}]') == "[]"

    async def test_directory_is_empty_with_warning(self, notes_storage, caplog) -> None:
        """ディレクトリを指すと空文字になり、誤りに気づけるよう WARNING を出す。"""
        await _write(notes_storage, {"memory/core.md": "x"})
        with caplog.at_level(logging.WARNING, logger="lilla_core.services.prompt_template"):
            assert await render_template('[{{ notes("memory") }}]') == "[]"
        assert any("is a directory" in r.getMessage() for r in caplog.records)

    async def test_missing_file_has_no_warning(self, notes_storage, caplog) -> None:
        """無いファイルは空文字で、WARNING は出さない。"""
        with caplog.at_level(logging.WARNING, logger="lilla_core.services.prompt_template"):
            assert await render_template('[{{ notes("memory/core.md") }}]') == "[]"
        assert not any("is a directory" in r.getMessage() for r in caplog.records)

    async def test_outside_paths_fail(self, notes_storage, outside_file: Path) -> None:
        """notes の外を指すパスは展開の失敗にし、外のファイルの本文を出さない。"""
        for path in _outside_paths(outside_file):
            with pytest.raises(PromptTemplateError) as excinfo:
                await render_template("{{ notes(%r) }}" % path)
            assert _SECRET not in str(excinfo.value), path

    async def test_outside_paths_fall_back_to_raw_text(
        self, notes_storage, outside_file: Path, caplog
    ) -> None:
        """`render_or_raw()` では WARNING を出して展開前の本文に戻り、外の本文は混ざらない。"""
        for path in _outside_paths(outside_file):
            text = "前 {{ notes(%r) }} 後" % path
            caplog.clear()
            with caplog.at_level(logging.WARNING):
                result = await render_or_raw(text, "prompt.system")
            assert result == text, path
            assert _SECRET not in result
            assert "prompt.system" in caplog.text
