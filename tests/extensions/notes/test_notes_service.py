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

    async def test_create_overwrite_append(self, notes_repo) -> None:
        """create → overwrite → append の順に書け、作ったかどうかと文字数を返す。"""
        result = await service.write_note("diary/a.md", "abc", "create")
        assert (result.path, result.created, result.size) == ("diary/a.md", True, 3)
        result = await service.write_note("diary/a.md", "xy", "overwrite")
        assert (result.created, result.size) == (False, 2)
        result = await service.write_note("diary/a.md", "z", "append")
        assert (result.created, result.size) == (False, 3)
        assert (await service.read_note("diary/a.md")).content == "xyz"

    async def test_create_existing(self, notes_repo) -> None:
        """create で既にあれば `FileStorageExistsError`。"""
        await service.write_note("user/a.md", "x", "create")
        with pytest.raises(FileStorageExistsError):
            await service.write_note("user/a.md", "y", "create")

    async def test_overwrite_keeps_previous_version(self, notes_repo) -> None:
        """上書きの前の内容は直前の版に残る。"""
        await service.write_note("user/a.md", "old", "create")
        await service.write_note("user/a.md", "new", "overwrite")
        version = await service.get_previous_version("user/a.md")
        assert (version.content, version.reason) == ("old", "overwrite")

    async def test_nfc_paths_are_the_same_file(self, notes_repo) -> None:
        """NFD で書いても NFC の同じファイルになる。"""
        import unicodedata

        await service.write_note("user/がく.md", "a", "create")
        with pytest.raises(FileStorageExistsError):
            await service.write_note(unicodedata.normalize("NFD", "user/がく.md"), "b", "create")

    async def test_size_limit(self, notes_repo) -> None:
        """書いた後の本文が上限を超えれば断る（append は合計で見る）。"""
        with pytest.raises(NotesTooLargeError):
            await service.write_note("user/a.md", "x" * 101, "overwrite")
        await service.write_note("user/a.md", "x" * 100, "overwrite")
        with pytest.raises(NotesTooLargeError):
            await service.write_note("user/a.md", "y", "append")

    async def test_too_large_is_a_file_storage_not_text_error(self, notes_repo) -> None:
        """大きすぎるときはコアの `FileStorageNotTextError` としても捕まえられる。"""
        with pytest.raises(FileStorageNotTextError):
            await service.write_note("user/a.md", "x" * 101, "overwrite")

    async def test_cannot_write_to_a_directory(self, notes_repo) -> None:
        """配下にノートがあるパスには書けない。"""
        await service.write_note("diary/a.md", "x", "create")
        with pytest.raises(FileStoragePathError):
            await service.write_note("diary", "x", "overwrite")

    async def test_cannot_write_under_a_file(self, notes_repo) -> None:
        """親の要素がファイルなら書けない。"""
        await service.write_note("user/memo", "x", "create")
        with pytest.raises(FileStoragePathError, match="user/memo"):
            await service.write_note("user/memo/a.md", "x", "create")

    async def test_rejects_non_string_content(self, notes_repo) -> None:
        """`content` が文字列でなければ断る。"""
        with pytest.raises(NotesError):
            await service.write_note("user/a.md", 1, "overwrite")

    async def test_rejects_unknown_mode(self, notes_repo) -> None:
        """知らない `mode` は `ValueError`。"""
        with pytest.raises(ValueError):
            await service.write_note("user/a.md", "x", "replace")

    async def test_read_missing(self, notes_repo) -> None:
        """無いノートは `FileStorageNotFoundError`。"""
        with pytest.raises(FileStorageNotFoundError):
            await service.read_note("none.md")


class TestListAndExists:
    """一覧と存在確認。"""

    async def test_list_derives_directories(self, notes_repo) -> None:
        """ディレクトリはパスの接頭辞から導き、名前順に返す。"""
        for path in ("user/b.md", "diary/2026/01.md", "diary/x.md", "user/a.md"):
            await service.write_note(path, "x", "create")

        top = await service.list_entries("")
        assert [(e.path, e.type) for e in top] == [("diary", "dir"), ("user", "dir")]
        user = await service.list_entries("user")
        assert [(e.path, e.type) for e in user] == [
            ("user/a.md", "file"), ("user/b.md", "file"),
        ]
        diary = await service.list_entries("diary")
        assert [(e.path, e.type) for e in diary] == [
            ("diary/2026", "dir"), ("diary/x.md", "file"),
        ]
        assert diary[0].size is None
        assert diary[0].modified_time is not None
        assert diary[1].size == 1

    async def test_list_empty_top(self, notes_repo) -> None:
        """最上位は空でも成功する。"""
        assert await service.list_entries("") == []

    async def test_list_missing_or_file(self, notes_repo) -> None:
        """無いディレクトリは不在、ファイルはパスの誤り。"""
        await service.write_note("user/a.md", "x", "create")
        with pytest.raises(FileStorageNotFoundError):
            await service.list_entries("none")
        with pytest.raises(FileStoragePathError):
            await service.list_entries("user/a.md")

    async def test_exists(self, notes_repo) -> None:
        """ファイルもディレクトリも True、無ければ False。"""
        await service.write_note("diary/a.md", "x", "create")
        assert await service.exists("diary/a.md") is True
        assert await service.exists("diary") is True
        assert await service.exists("") is True
        assert await service.exists("none") is False

    async def test_list_all(self, notes_repo) -> None:
        """すべてのファイルをパス順に返す。"""
        for path in ("user/b/c.md", "user/a.md"):
            await service.write_note(path, "x", "create")
        assert [e.path for e in await service.list_all()] == ["user/a.md", "user/b/c.md"]


class TestSearch:
    """検索。"""

    async def test_matches_lines_case_insensitively(self, notes_repo) -> None:
        """大文字小文字を区別せず、該当行を行番号つきで返す。"""
        await service.write_note("user/a.md", "first\nHello World\nlast", "create")
        await service.write_note("diary/b.md", "hello", "create")
        await service.write_note("user/c.md", "nothing", "create")

        matches, truncated = await service.search("", "hello")
        assert [(m.path, m.line, m.text) for m in matches] == [
            ("diary/b.md", 1, "hello"), ("user/a.md", 2, "Hello World"),
        ]
        assert truncated is False

    async def test_scoped_to_directory(self, notes_repo) -> None:
        """path を指定すると配下だけを探す。"""
        await service.write_note("user/a.md", "hello", "create")
        await service.write_note("diary/b.md", "hello", "create")
        matches, _ = await service.search("diary", "hello")
        assert [m.path for m in matches] == ["diary/b.md"]

    async def test_result_limit(self, notes_repo) -> None:
        """該当行の上限を超えたら `truncated`。"""
        await service.write_note("user/a.md", "x\nx\nx", "create")
        matches, truncated = await service.search("", "x", max_results=2)
        assert len(matches) == 2
        assert truncated is True


class TestDeleteAndRestore:
    """削除と直前の版への復元。"""

    async def test_delete_then_restore(self, notes_repo) -> None:
        """削除しても直前の版から戻せる。"""
        await service.write_note("user/a.md", "keep", "create")
        await service.delete_note("user/a.md")
        with pytest.raises(FileStorageNotFoundError):
            await service.read_note("user/a.md")
        await service.restore_previous_version("user/a.md")
        assert (await service.read_note("user/a.md")).content == "keep"

    async def test_restore_swaps_versions(self, notes_repo) -> None:
        """戻す前の内容は新しい直前の版になる。"""
        await service.write_note("user/a.md", "v1", "create")
        await service.write_note("user/a.md", "v2", "overwrite")
        await service.restore_previous_version("user/a.md")
        assert (await service.read_note("user/a.md")).content == "v1"
        assert (await service.get_previous_version("user/a.md")).content == "v2"

    async def test_missing(self, notes_repo) -> None:
        """無いものの削除・直前の版の取得・復元は不在。"""
        with pytest.raises(FileStorageNotFoundError):
            await service.delete_note("user/a.md")
        with pytest.raises(FileStorageNotFoundError):
            await service.get_previous_version("user/a.md")
        with pytest.raises(FileStorageNotFoundError):
            await service.restore_previous_version("user/a.md")


class TestWritableTopLevel:
    """ルート直下と許可されていないトップレベルへの書き込みの拒否。"""

    @pytest.mark.parametrize("path", ["a.md", "other/a.md", "Diary/a.md", "diary", "user"])
    async def test_rejects_outside_allowed_dirs(self, notes_repo, path) -> None:
        """ルート直下のファイル・許可リスト外のトップレベルへは、どの mode でも書けない。"""
        for mode in ("create", "overwrite", "append"):
            with pytest.raises(FileStoragePathError, match="diary/、user/"):
                await service.write_note(path, "x", mode)
        assert await service.list_entries("") == []

    @pytest.mark.parametrize("path", ["diary/2026-11-08.md", "user/a.md", "user/x/y/z.md"])
    async def test_allows_allowed_dirs(self, notes_repo, path) -> None:
        """`diary/`・`user/` の配下は作成・更新できる。"""
        await service.write_note(path, "a", "create")
        await service.write_note(path, "b", "overwrite")
        await service.write_note(path, "c", "append")
        assert (await service.read_note(path)).content == "bc"

    async def test_existing_root_file_can_be_read_and_deleted(self, notes_repo) -> None:
        """ルート直下の既存ファイルは読め、一覧・検索に出て、削除できるが、上書き・復元はできない。"""
        await notes_repo.commit({"old.md": "legacy"}, "create: old.md")
        assert (await service.read_note("old.md")).content == "legacy"
        assert [e.path for e in await service.list_entries("")] == ["old.md"]
        assert [m.path for m in (await service.search("", "legacy"))[0]] == ["old.md"]
        with pytest.raises(FileStoragePathError):
            await service.write_note("old.md", "new", "overwrite")
        await service.delete_note("old.md")
        assert await service.exists("old.md") is False
        with pytest.raises(FileStoragePathError):
            await service.restore_previous_version("old.md")

    async def test_follows_allow_list(self, notes_repo, monkeypatch) -> None:
        """許可リストに名前を足せば、そのトップレベルへも書ける。"""
        from lilla_core.extensions.notes import paths

        monkeypatch.setitem(paths.WRITABLE_TOP_LEVEL_DIRS, "system", {"ja": "x", "en": "x"})
        await service.write_note("system/a.md", "x", "create")
        assert await service.exists("system/a.md") is True

    async def test_storage_rejects_root(self, notes_repo) -> None:
        """保存先 `notes` からもルート直下へは書けず、`diary/` 配下へは書ける。"""
        storage = NotesStorage()
        with pytest.raises(FileStoragePathError):
            await storage.write("a.md", "x", mode="create")
        await storage.write("user/a.md", "x", mode="create")
        await storage.write("diary/a.md", "x", mode="create")


class TestNotesStorage:
    """コアの `FileStorage` としての振る舞い。"""

    async def test_round_trip(self, notes_repo) -> None:
        """write / read / list / exists が service と同じ結果になる。"""
        storage = NotesStorage()
        assert storage.read_only is False
        result = await storage.write("diary/2026-11-08.md", "本文", mode="create")
        assert (result.path, result.created) == ("diary/2026-11-08.md", True)
        assert await storage.read("diary/2026-11-08.md") == "本文"
        entries = await storage.list("")
        assert [(e.path, e.type) for e in entries] == [("diary", "dir")]
        assert await storage.exists("diary/2026-11-08.md") is True

    async def test_errors_are_file_storage_errors(self, notes_repo) -> None:
        """失敗はコアの `FileStorage` の例外で分かる。"""
        storage = NotesStorage()
        with pytest.raises(FileStoragePathError):
            await storage.read("../x")
        with pytest.raises(FileStorageNotFoundError):
            await storage.read("none.md")


class TestLargeDeletionAlert:
    """大量削除の通知。"""

    @pytest.fixture
    def notes_config(self):
        """3 行以上消えたら通知する設定。"""
        from lilla_core.extensions.notes import NotesConfig

        return NotesConfig(max_content_chars=10_000, deletion_alert_lines=3)

    @pytest.fixture(autouse=True)
    def plain_messages(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """文言はキーと引数をそのまま並べた形にする（カタログの中身に依らず確かめるため）。"""
        from lilla_core.ui import messages

        monkeypatch.setattr(
            messages,
            "t",
            lambda key, **params: " ".join([key, *(f"{k}={v}" for k, v in sorted(params.items()))]),
        )

    async def test_overwrite_removing_many_lines(self, notes_repo, alert) -> None:
        """消えた行が閾値以上なら、行数・コミット・パッチを添えて通知する（書き込みは成功）。"""
        await service.write_note("user/a.md", "1\n2\n3\n4\n", "create")
        result = await service.write_note("user/a.md", "1\nnew\n", "overwrite")
        assert result.size == 6
        assert (await service.read_note("user/a.md")).content == "1\nnew\n"

        alert.assert_awaited_once()
        message = alert.await_args.args[1]
        assert message.startswith("notes.alert.large_deletion ")
        assert "path=user/a.md" in message and "removed=3" in message and "added=1" in message
        assert f"commit={alert.await_args.kwargs['attachment_name'][6:18]}" in message
        kwargs = alert.await_args.kwargs
        assert kwargs["attachment_name"].startswith("notes-") and kwargs["attachment_name"].endswith(".patch")
        assert "-2\n" in kwargs["attachment_text"] and "+new\n" in kwargs["attachment_text"]

    async def test_below_threshold_or_append(self, notes_repo, alert) -> None:
        """閾値未満・追記・作成では通知しない。"""
        await service.write_note("user/a.md", "1\n2\n3\n", "create")
        await service.write_note("user/a.md", "1\n2\n", "overwrite")
        await service.write_note("user/a.md", "x\ny\nz\n", "append")
        alert.assert_not_awaited()

    async def test_delete_counts_all_lines(self, notes_repo, alert) -> None:
        """ファイルの削除は全行を消えた行として数える。"""
        await service.write_note("user/a.md", "1\n2\n3\n", "create")
        await service.delete_note("user/a.md")
        alert.assert_awaited_once()
        assert "removed=3" in alert.await_args.args[1]

    async def test_restore_can_alert(self, notes_repo, alert) -> None:
        """直前の版へ戻して行が消えるときも通知する。"""
        await service.write_note("user/a.md", "1\n", "create")
        await service.write_note("user/a.md", "1\n2\n3\n4\n", "overwrite")
        await service.restore_previous_version("user/a.md")
        alert.assert_awaited_once()

    async def test_alert_failure_does_not_fail_write(self, notes_repo, alert) -> None:
        """通知に失敗しても書き込みは成功させる。"""
        alert.side_effect = RuntimeError("discord down")
        await service.write_note("user/a.md", "1\n2\n3\n", "create")
        await service.delete_note("user/a.md")
        assert await service.exists("user/a.md") is False

    async def test_long_patch_is_truncated(self, notes_repo, alert, monkeypatch) -> None:
        """パッチが長すぎれば切り詰める。"""
        monkeypatch.setattr(service, "MAX_ALERT_PATCH_CHARS", 10)
        await service.write_note("user/a.md", "line\n" * 5, "create")
        await service.delete_note("user/a.md")
        text = alert.await_args.kwargs["attachment_text"]
        assert text.endswith("notes.alert.patch_truncated\n")
        assert len(text) == 10 + len("\nnotes.alert.patch_truncated\n")


class TestDiffStats:
    """差分の行数。"""

    def test_counts(self) -> None:
        """追加・削除の行数を数える（ヘッダーは含めない）。"""
        stats = service.diff_stats("a.md", "1\n2\n3\n", "1\nx\n")
        assert (stats.added, stats.removed) == (1, 2)
        assert stats.patch.startswith("--- a/a.md\n+++ b/a.md\n")

    def test_missing_trailing_newline(self) -> None:
        """末尾に改行の無い行も 1 行と数え、パッチは行ごとに改行で終わる。"""
        stats = service.diff_stats("a.md", "1\n2", "")
        assert (stats.added, stats.removed) == (0, 2)
        assert "+++ /dev/null\n" in stats.patch
        assert stats.patch.endswith("\n")
