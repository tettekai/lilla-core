"""`lilla_core.extensions.notes.service` と `NotesStorage` のテスト。"""
from __future__ import annotations

import pytest

from lilla_core.core.file_storage import (
    FileStorageExistsError,
    FileStorageNotFoundError,
    FileStorageNotTextError,
    FileStoragePathError,
)
from lilla_core.extensions.notes import service
from lilla_core.extensions.notes.errors import NotesError, NotesTooLargeError
from lilla_core.extensions.notes.storage import NotesStorage


class TestWriteAndRead:
    """書き込みと読み取り。"""

    async def test_create_overwrite_append(self, fake_repo) -> None:
        """create → overwrite → append の順に書け、作ったかどうかと文字数を返す。"""
        result = await service.write_note("diary/a.md", "abc", "create")
        assert (result.path, result.created, result.size) == ("diary/a.md", True, 3)
        result = await service.write_note("diary/a.md", "xy", "overwrite")
        assert (result.created, result.size) == (False, 2)
        result = await service.write_note("diary/a.md", "z", "append")
        assert (result.created, result.size) == (False, 3)
        assert (await service.read_note("diary/a.md")).content == "xyz"

    async def test_create_existing(self, fake_repo) -> None:
        """create で既にあれば `FileStorageExistsError`。"""
        await service.write_note("a.md", "x", "create")
        with pytest.raises(FileStorageExistsError):
            await service.write_note("a.md", "y", "create")

    async def test_overwrite_keeps_previous_version(self, fake_repo) -> None:
        """上書きの前の内容は直前の版に残る。"""
        await service.write_note("a.md", "old", "create")
        await service.write_note("a.md", "new", "overwrite")
        version = await service.get_previous_version("a.md")
        assert (version.content, version.reason) == ("old", "overwrite")

    async def test_nfc_paths_are_the_same_file(self, fake_repo) -> None:
        """NFD で書いても NFC の同じファイルになる。"""
        import unicodedata

        await service.write_note("がく.md", "a", "create")
        with pytest.raises(FileStorageExistsError):
            await service.write_note(unicodedata.normalize("NFD", "がく.md"), "b", "create")

    async def test_size_limit(self, fake_repo) -> None:
        """書いた後の本文が上限を超えれば断る（append は合計で見る）。"""
        with pytest.raises(NotesTooLargeError):
            await service.write_note("a.md", "x" * 101, "overwrite")
        await service.write_note("a.md", "x" * 100, "overwrite")
        with pytest.raises(NotesTooLargeError):
            await service.write_note("a.md", "y", "append")

    async def test_too_large_is_a_file_storage_not_text_error(self, fake_repo) -> None:
        """大きすぎるときはコアの `FileStorageNotTextError` としても捕まえられる。"""
        with pytest.raises(FileStorageNotTextError):
            await service.write_note("a.md", "x" * 101, "overwrite")

    async def test_cannot_write_to_a_directory(self, fake_repo) -> None:
        """配下にノートがあるパスには書けない。"""
        await service.write_note("diary/a.md", "x", "create")
        with pytest.raises(FileStoragePathError):
            await service.write_note("diary", "x", "overwrite")

    async def test_cannot_write_under_a_file(self, fake_repo) -> None:
        """親の要素がファイルなら書けない。"""
        await service.write_note("diary", "x", "create")
        with pytest.raises(FileStoragePathError, match="diary"):
            await service.write_note("diary/a.md", "x", "create")

    async def test_rejects_non_string_content(self, fake_repo) -> None:
        """`content` が文字列でなければ断る。"""
        with pytest.raises(NotesError):
            await service.write_note("a.md", 1, "overwrite")

    async def test_rejects_unknown_mode(self, fake_repo) -> None:
        """知らない `mode` は `ValueError`。"""
        with pytest.raises(ValueError):
            await service.write_note("a.md", "x", "replace")

    async def test_read_missing(self, fake_repo) -> None:
        """無いノートは `FileStorageNotFoundError`。"""
        with pytest.raises(FileStorageNotFoundError):
            await service.read_note("none.md")


class TestListAndExists:
    """一覧と存在確認。"""

    async def test_list_derives_directories(self, fake_repo) -> None:
        """ディレクトリはパスの接頭辞から導き、名前順に返す。"""
        for path in ("b.md", "diary/2026/01.md", "diary/x.md", "a.md"):
            await service.write_note(path, "x", "create")

        top = await service.list_entries("")
        assert [(e.path, e.type) for e in top] == [
            ("a.md", "file"), ("b.md", "file"), ("diary", "dir"),
        ]
        diary = await service.list_entries("diary")
        assert [(e.path, e.type) for e in diary] == [
            ("diary/2026", "dir"), ("diary/x.md", "file"),
        ]
        assert diary[0].size is None
        assert diary[0].modified_time is not None
        assert diary[1].size == 1

    async def test_list_empty_top(self, fake_repo) -> None:
        """最上位は空でも成功する。"""
        assert await service.list_entries("") == []

    async def test_list_missing_or_file(self, fake_repo) -> None:
        """無いディレクトリは不在、ファイルはパスの誤り。"""
        await service.write_note("a.md", "x", "create")
        with pytest.raises(FileStorageNotFoundError):
            await service.list_entries("none")
        with pytest.raises(FileStoragePathError):
            await service.list_entries("a.md")

    async def test_exists(self, fake_repo) -> None:
        """ファイルもディレクトリも True、無ければ False。"""
        await service.write_note("diary/a.md", "x", "create")
        assert await service.exists("diary/a.md") is True
        assert await service.exists("diary") is True
        assert await service.exists("") is True
        assert await service.exists("none") is False

    async def test_list_all(self, fake_repo) -> None:
        """すべてのファイルをパス順に返す。"""
        for path in ("b/c.md", "a.md"):
            await service.write_note(path, "x", "create")
        assert [e.path for e in await service.list_all()] == ["a.md", "b/c.md"]


class TestSearch:
    """検索。"""

    async def test_matches_lines_case_insensitively(self, fake_repo) -> None:
        """大文字小文字を区別せず、該当行を行番号つきで返す。"""
        await service.write_note("a.md", "first\nHello World\nlast", "create")
        await service.write_note("diary/b.md", "hello", "create")
        await service.write_note("c.md", "nothing", "create")

        matches, truncated = await service.search("", "hello")
        assert [(m.path, m.line, m.text) for m in matches] == [
            ("a.md", 2, "Hello World"), ("diary/b.md", 1, "hello"),
        ]
        assert truncated is False

    async def test_scoped_to_directory(self, fake_repo) -> None:
        """path を指定すると配下だけを探す。"""
        await service.write_note("a.md", "hello", "create")
        await service.write_note("diary/b.md", "hello", "create")
        matches, _ = await service.search("diary", "hello")
        assert [m.path for m in matches] == ["diary/b.md"]

    async def test_result_limit(self, fake_repo) -> None:
        """該当行の上限を超えたら `truncated`。"""
        await service.write_note("a.md", "x\nx\nx", "create")
        matches, truncated = await service.search("", "x", max_results=2)
        assert len(matches) == 2
        assert truncated is True


class TestDeleteAndRestore:
    """削除と直前の版への復元。"""

    async def test_delete_then_restore(self, fake_repo) -> None:
        """削除しても直前の版から戻せる。"""
        await service.write_note("a.md", "keep", "create")
        await service.delete_note("a.md")
        with pytest.raises(FileStorageNotFoundError):
            await service.read_note("a.md")
        await service.restore_previous_version("a.md")
        assert (await service.read_note("a.md")).content == "keep"

    async def test_restore_swaps_versions(self, fake_repo) -> None:
        """戻す前の内容は新しい直前の版になる。"""
        await service.write_note("a.md", "v1", "create")
        await service.write_note("a.md", "v2", "overwrite")
        await service.restore_previous_version("a.md")
        assert (await service.read_note("a.md")).content == "v1"
        assert (await service.get_previous_version("a.md")).content == "v2"

    async def test_missing(self, fake_repo) -> None:
        """無いものの削除・直前の版の取得・復元は不在。"""
        with pytest.raises(FileStorageNotFoundError):
            await service.delete_note("a.md")
        with pytest.raises(FileStorageNotFoundError):
            await service.get_previous_version("a.md")
        with pytest.raises(FileStorageNotFoundError):
            await service.restore_previous_version("a.md")


class TestNotesStorage:
    """コアの `FileStorage` としての振る舞い。"""

    async def test_round_trip(self, fake_repo) -> None:
        """write / read / list / exists が service と同じ結果になる。"""
        storage = NotesStorage()
        assert storage.read_only is False
        result = await storage.write("diary/2026-11-08.md", "本文", mode="create")
        assert (result.path, result.created) == ("diary/2026-11-08.md", True)
        assert await storage.read("diary/2026-11-08.md") == "本文"
        entries = await storage.list("")
        assert [(e.path, e.type) for e in entries] == [("diary", "dir")]
        assert await storage.exists("diary/2026-11-08.md") is True

    async def test_errors_are_file_storage_errors(self, fake_repo) -> None:
        """失敗はコアの `FileStorage` の例外で分かる。"""
        storage = NotesStorage()
        with pytest.raises(FileStoragePathError):
            await storage.read("../x")
        with pytest.raises(FileStorageNotFoundError):
            await storage.read("none.md")
