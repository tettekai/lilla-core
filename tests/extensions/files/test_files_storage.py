"""`lilla_core.extensions.files.storage` の `FilesStorage` と共有の読み書きのテスト。"""
from __future__ import annotations

from pathlib import Path

import pytest

from lilla_core.core.file_storage import (
    FileStorage,
    FileStorageExistsError,
    FileStorageNotFoundError,
    FileStorageNotTextError,
    FileStoragePathError,
    FileStoragePermissionError,
)
from lilla_core.extensions import files


@pytest.fixture
def dirs(tmp_path: Path) -> dict[str, Path]:
    """読み取り専用 `data` と書き込み可能 `out` を用意する。"""
    data = tmp_path / "data"
    out = tmp_path / "out"
    data.mkdir()
    out.mkdir()
    (data / "a.md").write_text("hello", encoding="utf-8")
    (data / "sub").mkdir()
    (data / "bin.dat").write_bytes(b"\0\1\2")
    return {"root": tmp_path, "data": data, "out": out}


def _compose(dirs: dict[str, Path], writable: bool = True):
    """`extensions.files` を書いた `lilla.yaml` で拡張を登録するコンテキストを返す。"""
    from lilla_core.testing import use_extensions, write_minimal_lilla_yaml

    config_root = dirs["root"] / "config"
    config_root.mkdir(exist_ok=True)
    section = {"read_only_dirs": [str(dirs["data"])]}
    if writable:
        section["writable_dirs"] = [str(dirs["out"])]
    write_minimal_lilla_yaml(config_root, extra={"extensions": {"files": section}})
    return use_extensions(files.extension, config_root=config_root)


class TestFilesStorageDeclaration:
    """拡張からの申告。"""

    def test_declares_files_storage(self) -> None:
        """`file_storages()` が `files` という名前で `FileStorage` を返す。"""
        storages = files.extension.file_storages()
        assert list(storages) == ["files"]
        assert isinstance(storages["files"], FileStorage)

    def test_registered_via_core(self, dirs) -> None:
        """登録後は `get_file_storage("files")` で引ける。"""
        from lilla_core.core.extension import get_file_storage, list_file_storages

        with _compose(dirs):
            assert isinstance(get_file_storage("files"), FileStorage)
            assert "files" in list_file_storages()


class TestFilesStorage:
    """`FilesStorage` の読み書き。"""

    async def test_read(self, dirs) -> None:
        """テキストファイルを読める。"""
        with _compose(dirs):
            storage = files.extension.file_storages()["files"]
            assert await storage.read("data/a.md") == "hello"

    async def test_read_missing(self, dirs) -> None:
        """無いファイルは `FileStorageNotFoundError`。"""
        with _compose(dirs):
            storage = files.extension.file_storages()["files"]
            with pytest.raises(FileStorageNotFoundError):
                await storage.read("data/none.md")

    async def test_read_unknown_root(self, dirs) -> None:
        """名前の分からないディレクトリは `FileStorageNotFoundError`。"""
        with _compose(dirs):
            storage = files.extension.file_storages()["files"]
            with pytest.raises(FileStorageNotFoundError):
                await storage.read("nope/a.md")

    async def test_read_binary(self, dirs) -> None:
        """バイナリは `FileStorageNotTextError`。"""
        with _compose(dirs):
            storage = files.extension.file_storages()["files"]
            with pytest.raises(FileStorageNotTextError):
                await storage.read("data/bin.dat")

    @pytest.mark.parametrize("path", ["/etc/passwd", "data/../x", "data/\0"])
    async def test_invalid_path(self, dirs, path: str) -> None:
        """不正なパスは `FileStoragePathError`。"""
        with _compose(dirs):
            storage = files.extension.file_storages()["files"]
            with pytest.raises(FileStoragePathError):
                await storage.read(path)

    async def test_write_create_overwrite_append(self, dirs) -> None:
        """create → overwrite → append の順に書ける。親ディレクトリは作る。"""
        with _compose(dirs):
            storage = files.extension.file_storages()["files"]
            result = await storage.write("out/diary/2026-11-08.md", "a", mode="create")
            assert result.path == "out/diary/2026-11-08.md"
            assert result.created is True
            with pytest.raises(FileStorageExistsError):
                await storage.write("out/diary/2026-11-08.md", "x", mode="create")
            result = await storage.write("out/diary/2026-11-08.md", "b")
            assert result.created is False
            await storage.write("out/diary/2026-11-08.md", "c", mode="append")
        assert (dirs["out"] / "diary" / "2026-11-08.md").read_text(encoding="utf-8") == "bc"

    async def test_write_read_only(self, dirs) -> None:
        """読み取り専用ディレクトリへの書き込みは `FileStoragePermissionError`。"""
        with _compose(dirs):
            storage = files.extension.file_storages()["files"]
            with pytest.raises(FileStoragePermissionError):
                await storage.write("data/new.md", "x")

    async def test_write_invalid_mode(self, dirs) -> None:
        """知らない `mode` は `ValueError`。"""
        with _compose(dirs):
            storage = files.extension.file_storages()["files"]
            with pytest.raises(ValueError):
                await storage.write("out/a.md", "x", mode="replace")

    async def test_list(self, dirs) -> None:
        """空文字はホワイトリストの一覧、それ以外は直下のエントリ。"""
        with _compose(dirs):
            storage = files.extension.file_storages()["files"]
            top = await storage.list("")
            entries = await storage.list("data")
        assert [(e.path, e.type) for e in top] == [("data", "dir"), ("out", "dir")]
        assert [(e.path, e.type) for e in entries] == [
            ("data/a.md", "file"),
            ("data/bin.dat", "file"),
            ("data/sub", "dir"),
        ]
        assert entries[0].size == 5
        assert entries[0].modified_time is not None
        assert entries[2].size is None

    async def test_list_missing(self, dirs) -> None:
        """無いディレクトリは `FileStorageNotFoundError`。"""
        with _compose(dirs):
            storage = files.extension.file_storages()["files"]
            with pytest.raises(FileStorageNotFoundError):
                await storage.list("data/none")

    async def test_exists(self, dirs) -> None:
        """あれば True、無い・ホワイトリスト外は False。"""
        with _compose(dirs):
            storage = files.extension.file_storages()["files"]
            assert await storage.exists("data/a.md") is True
            assert await storage.exists("data/sub") is True
            assert await storage.exists("data/none.md") is False
            assert await storage.exists("nope/a.md") is False

    def test_read_only_flag(self, dirs) -> None:
        """書き込み可能なディレクトリが無ければ `read_only`。"""
        with _compose(dirs):
            assert files.extension.file_storages()["files"].read_only is False
        with _compose(dirs, writable=False):
            assert files.extension.file_storages()["files"].read_only is True
