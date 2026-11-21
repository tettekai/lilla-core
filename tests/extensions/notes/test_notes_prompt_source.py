"""`load_text_resources` の `$notes` の指定（notes をプロンプトの読み元にする）のテスト。

`tmp_path` に本物の bare Git リポジトリを作り（`notes_repo` fixture）、保存先 `notes` として
`NotesStorage` を登録して読む。
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from lilla_core.core import extension as core_extension
from lilla_core.core.config import resolve_llm_provider_prompt_spec
from lilla_core.core.file_storage import FileStoragePathError
from lilla_core.extensions.notes.storage import NotesStorage
from lilla_core.utils.resource_loader import load_text_resources, notes_logical_path


@pytest.fixture
def notes_storage(notes_repo, monkeypatch: pytest.MonkeyPatch):
    """保存先 `notes` として `NotesStorage` を登録し、リポジトリを返す。"""
    monkeypatch.setitem(core_extension._file_storages, "notes", NotesStorage())
    return notes_repo


async def _write(repo, files: dict[str, str | None]) -> None:
    """リポジトリへ直接コミットする（書き込める場所の規則を通さずに置く）。"""
    async with repo.write_lock:
        await repo.commit(files, "create: test")


class TestNotesLogicalPath:
    """`$notes` の判定のテスト。"""

    @pytest.mark.parametrize(
        ("rest", "expected"),
        [
            ("$notes", ""),
            ("$notes/", ""),
            ("$notes/memory/core.md", "memory/core.md"),
            (" $notes/memory/core.md", "memory/core.md"),
            ("$notesx/a.md", None),
            ("/app/$notes/a.md", None),
            ("/app/config/a.md", None),
        ],
    )
    def test_detects_placeholder(self, rest: str, expected: str | None) -> None:
        """先頭の `$notes` / `$notes/` だけを notes とみなし、前の空白は無視する。"""
        assert notes_logical_path(rest) == expected


class TestLoadFromNotes:
    """notes から読むときのテスト。"""

    async def test_reads_file(self, notes_storage, tmp_path: Path) -> None:
        """`file:$notes/...` でノートの本文を読む。"""
        await _write(notes_storage, {"memory/core.md": "core\n"})
        assert await load_text_resources("file:$notes/memory/core.md", tmp_path) == "core"

    async def test_space_after_prefix_is_allowed(self, notes_storage, tmp_path: Path) -> None:
        """プレフィックスの直後に空白があっても同じに読む。"""
        await _write(notes_storage, {"memory/core.md": "core"})
        assert await load_text_resources("file: $notes/memory/core.md", tmp_path) == "core"

    async def test_dir_joins_direct_md_and_txt_in_name_order(
        self, notes_storage, tmp_path: Path
    ) -> None:
        """`dir:` は直下の `.md` / `.txt` だけをファイル名昇順で結合し、再帰しない。"""
        await _write(notes_storage, {
            "memory/personal/02_b.txt": "B\n",
            "memory/personal/01_a.md": "A",
            "memory/personal/ignore.json": "nope",
            "memory/personal/sub/deep.md": "deep",
        })
        assert await load_text_resources("dir:$notes/memory/personal", tmp_path) == "A\nB"

    async def test_mixes_disk_and_notes_in_written_order(
        self, notes_storage, tmp_path: Path
    ) -> None:
        """ディスクの指定と混在でき、書いた順に結合する。"""
        disk = tmp_path / "base.md"
        disk.write_text("disk", encoding="utf-8")
        await _write(notes_storage, {"memory/core.md": "notes"})
        result = await load_text_resources(
            ["file:$notes/memory/core.md", f"file:{disk}", "file:$notes/memory/core.md"],
            tmp_path,
        )
        assert result == "notes\ndisk\nnotes"

    async def test_reads_latest_each_call(self, notes_storage, tmp_path: Path) -> None:
        """呼ぶたびに最新を読む（書き込みの次の呼び出しから反映される）。"""
        await _write(notes_storage, {"memory/core.md": "v1"})
        assert await load_text_resources("file:$notes/memory/core.md", tmp_path) == "v1"
        await _write(notes_storage, {"memory/core.md": "v2"})
        assert await load_text_resources("file:$notes/memory/core.md", tmp_path) == "v2"

    async def test_missing_file_is_skipped(self, notes_storage, tmp_path: Path) -> None:
        """無いノートは、ディスクの `file:` と同じくその要素を空にする。"""
        await _write(notes_storage, {"memory/core.md": "core"})
        result = await load_text_resources(
            ["file:$notes/memory/missing.md", "file:$notes/memory/core.md"], tmp_path
        )
        assert result == "core"

    async def test_missing_dir_is_empty(self, notes_storage, tmp_path: Path) -> None:
        """無いディレクトリは空にする。"""
        assert await load_text_resources("dir:$notes/memory/nope", tmp_path) == ""

    async def test_dir_pointing_to_file_is_empty(self, notes_storage, tmp_path: Path) -> None:
        """`dir:` がファイルを指すときも、ディスクの `dir:` と同じく空にする。"""
        await _write(notes_storage, {"memory/core.md": "core"})
        assert await load_text_resources("dir:$notes/memory/core.md", tmp_path) == ""

    async def test_file_pointing_to_dir_is_skipped(self, notes_storage, tmp_path: Path) -> None:
        """`file:` がディレクトリを指すときは、無いファイルと同じく空にする。"""
        await _write(notes_storage, {"memory/core.md": "core"})
        assert await load_text_resources("file:$notes/memory", tmp_path) == ""

    async def test_dir_root(self, notes_storage, tmp_path: Path) -> None:
        """`dir:$notes` は最上位の直下を読む。"""
        await _write(notes_storage, {"top.md": "top", "memory/core.md": "core"})
        assert await load_text_resources("dir:$notes", tmp_path) == "top"

    @pytest.mark.parametrize(
        "spec",
        [
            "file:$notes/../secret.md",
            "file:$notes//memory/core.md",
            "file:$notes/memory/./core.md",
            "file:$notes/memory/core.md/",
            "file:$notes",
            "dir:$notes/../memory",
            "dir:$notes/memory//personal",
        ],
    )
    async def test_invalid_path_raises(self, notes_storage, tmp_path: Path, spec: str) -> None:
        """notes のパス規則に合わない指定は、スキップせずに失敗する。"""
        with pytest.raises(FileStoragePathError):
            await load_text_resources(spec, tmp_path)

    async def test_unregistered_storage_is_empty_with_warning(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog
    ) -> None:
        """保存先 `notes` が登録されていなければ、WARNING を出して空にする。"""
        monkeypatch.delitem(core_extension._file_storages, "notes", raising=False)
        with caplog.at_level(logging.WARNING, logger="lilla_core.utils.resource_loader"):
            assert await load_text_resources("file:$notes/memory/core.md", tmp_path) == ""
        assert "not registered" in caplog.text

    async def test_read_failure_is_empty_with_warning(
        self, notes_storage, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog
    ) -> None:
        """リポジトリを開けないなど読み取り自体の失敗は、WARNING を出して空にする。"""
        async def broken(self, path):
            raise RuntimeError("cannot open repository")

        monkeypatch.setattr(NotesStorage, "read", broken)
        disk = tmp_path / "base.md"
        disk.write_text("disk", encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="lilla_core.utils.resource_loader"):
            result = await load_text_resources(
                ["file:$notes/memory/core.md", f"file:{disk}"], tmp_path
            )
        assert result == "disk"
        assert "Failed to read prompt source file:$notes/memory/core.md" in caplog.text


class TestProviderPromptSpec:
    """LLM プロバイダーの `prompt` の解決で `$notes` を書き換えないことのテスト。"""

    def test_notes_spec_is_not_made_absolute(self) -> None:
        """`$notes` はディスクの相対パスではないため、`config_root` を前に付けない。"""
        resolved = resolve_llm_provider_prompt_spec(
            ["file: $notes/memory/core.md", "dir:rel/dir"], "/cfg"
        )
        assert resolved == ["file:$notes/memory/core.md", "dir:/cfg/rel/dir"]
