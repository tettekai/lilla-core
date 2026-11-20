"""`lilla_core.extensions.notes.repository`（bare Git リポジトリ）のテスト。"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from lilla_core.extensions.notes.repository import BRANCH_REF, NotesRepository


@pytest.fixture
async def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> NotesRepository:
    """`tmp_path` に作った bare リポジトリ（コミット時刻は 1 回ごとに 1 秒進む）。"""
    ticks = iter(range(1_800_000_000, 1_900_000_000))
    monkeypatch.setattr(NotesRepository, "_now", staticmethod(lambda: next(ticks)))
    repository = NotesRepository(tmp_path / "notes.git")
    await repository.init_repository()
    return repository


def _log(path: Path) -> list[str]:
    """`main` のコミットメッセージを新しい順に返す。"""
    from dulwich.repo import Repo

    git = Repo(str(path))
    return [entry.commit.message.decode() for entry in git.get_walker(include=[git.refs[BRANCH_REF]])]


class TestInitRepository:
    """リポジトリの作成と検査。"""

    async def test_creates_bare_repository_on_main(self, tmp_path: Path) -> None:
        """無ければ `main` を指す bare リポジトリを作る（親ディレクトリも作る）。"""
        path = tmp_path / "data" / "notes.git"
        await NotesRepository(path).init_repository()
        assert (path / "HEAD").read_text().strip() == "ref: refs/heads/main"
        assert (path / "objects").is_dir()

    async def test_uses_empty_directory(self, tmp_path: Path) -> None:
        """空のディレクトリ（ボリュームのマウント先など）にはそのまま作る。"""
        path = tmp_path / "notes.git"
        path.mkdir()
        await NotesRepository(path).init_repository()
        assert (path / "HEAD").is_file()

    async def test_reopens_existing_repository(self, repo: NotesRepository) -> None:
        """作り直さずに既存の内容を読む。"""
        await repo.commit({"a.md": "x"}, "create: a.md")
        reopened = NotesRepository(repo.path)
        await reopened.init_repository()
        assert (await reopened.get("a.md")).content == "x"

    async def test_rejects_file(self, tmp_path: Path) -> None:
        """パスがファイルなら落とす。"""
        path = tmp_path / "notes.git"
        path.write_text("x")
        with pytest.raises(RuntimeError, match="not a directory"):
            await NotesRepository(path).init_repository()

    async def test_rejects_non_git_directory(self, tmp_path: Path) -> None:
        """空でない Git 以外のディレクトリは落とす（中身を壊さない）。"""
        path = tmp_path / "notes.git"
        path.mkdir()
        (path / "keep.txt").write_text("x")
        with pytest.raises(RuntimeError, match="not a Git repository"):
            await NotesRepository(path).init_repository()
        assert (path / "keep.txt").read_text() == "x"

    async def test_rejects_non_bare_repository(self, tmp_path: Path) -> None:
        """作業ツリーのあるリポジトリは落とす。"""
        from dulwich.repo import Repo

        path = tmp_path / "work"
        path.mkdir()
        Repo.init(str(path))
        with pytest.raises(RuntimeError, match="bare"):
            await NotesRepository(path).init_repository()


class TestReadAndWrite:
    """コミットと読み取り。"""

    async def test_empty(self, repo: NotesRepository) -> None:
        """コミットが無くても読める。"""
        assert await repo.get("a.md") is None
        assert await repo.list_under("") == []
        assert await repo.search("", "x", 10) == ([], False)

    async def test_commit_and_get(self, repo: NotesRepository) -> None:
        """1 回の `commit` が 1 コミットになり、作成・更新日時はコミットの時刻。"""
        first = await repo.commit({"diary/a.md": "v1"}, "create: diary/a.md")
        second = await repo.commit({"diary/a.md": "v2"}, "overwrite: diary/a.md")
        assert first.commit_id and second.commit_id and first.commit_id != second.commit_id

        note = await repo.get("diary/a.md")
        assert (note.content, note.size) == ("v2", 2)
        assert note.created_at == datetime.fromtimestamp(1_800_000_000, tz=timezone.utc)
        assert note.updated_at == datetime.fromtimestamp(1_800_000_001, tz=timezone.utc)
        assert _log(repo.path) == ["overwrite: diary/a.md", "create: diary/a.md"]

    async def test_unchanged_content_is_not_committed(self, repo: NotesRepository) -> None:
        """内容が変わらなければコミットしない。"""
        await repo.commit({"a.md": "x"}, "create: a.md")
        assert (await repo.commit({"a.md": "x"}, "overwrite: a.md")).commit_id is None
        assert len(_log(repo.path)) == 1

    async def test_delete(self, repo: NotesRepository) -> None:
        """None は削除になる。"""
        await repo.commit({"a.md": "x", "b.md": "y"}, "create: a.md")
        await repo.commit({"a.md": None}, "delete: a.md")
        assert await repo.get("a.md") is None
        assert [m.path for m in await repo.list_under("")] == ["b.md"]

    async def test_non_ascii_path_and_content(self, repo: NotesRepository) -> None:
        """日本語のパスと本文をそのまま扱う。"""
        await repo.commit({"日記/今日.md": "晴れ"}, "create: 日記/今日.md")
        assert (await repo.get("日記/今日.md")).content == "晴れ"
        assert (await repo.get_meta("日記/今日.md")).size == 2

    async def test_list_and_children(self, repo: NotesRepository) -> None:
        """配下のノートをパス順に返し、配下の有無を判定する。"""
        await repo.commit({"b.md": "x", "diary/x.md": "y", "diary/2026/a.md": "z"}, "create: b.md")
        assert [m.path for m in await repo.list_under("diary")] == ["diary/2026/a.md", "diary/x.md"]
        assert await repo.has_children("diary") is True
        assert await repo.has_children("dia") is False
        assert await repo.get_meta("diary") is None

    async def test_search(self, repo: NotesRepository) -> None:
        """大文字小文字を区別せず探し、起点がファイルならそのファイルだけを見る。"""
        await repo.commit(
            {"a.md": "Hello", "diary/b.md": "hello", "diary/c.md": "nothing"}, "create: a.md"
        )
        found, truncated = await repo.search("", "HELLO", 10)
        assert ([n.path for n in found], truncated) == (["a.md", "diary/b.md"], False)
        assert [n.path for n in (await repo.search("diary", "hello", 10))[0]] == ["diary/b.md"]
        assert [n.path for n in (await repo.search("a.md", "hello", 10))[0]] == ["a.md"]
        assert (await repo.search("", "hello", 1))[1] is True

    async def test_sees_commits_from_another_instance(self, repo: NotesRepository) -> None:
        """別のインスタンス（別プロセス相当）が進めた `main` も読む。"""
        await repo.commit({"a.md": "x"}, "create: a.md")
        assert (await repo.get("a.md")).content == "x"
        other = NotesRepository(repo.path)
        await other.commit({"a.md": "y", "b.md": "z"}, "overwrite: a.md")
        assert (await repo.get("a.md")).content == "y"
        assert [m.path for m in await repo.list_under("")] == ["a.md", "b.md"]

    async def test_rejects_stale_head(self, repo: NotesRepository, monkeypatch) -> None:
        """読んだ後に `main` が動いていたら上書きせずに落とす。"""
        await repo.commit({"a.md": "x"}, "create: a.md")
        other = NotesRepository(repo.path)
        stale = repo._snapshot()
        await other.commit({"a.md": "y"}, "overwrite: a.md")
        monkeypatch.setattr(repo, "_snapshot", lambda: stale)
        with pytest.raises(RuntimeError, match="another writer"):
            await repo.commit({"a.md": "z"}, "overwrite: a.md")


class TestGetVersion:
    """直前の版。"""

    async def test_none_after_create(self, repo: NotesRepository) -> None:
        """最後の変更が作成なら直前の版は無い。"""
        await repo.commit({"a.md": "x"}, "create: a.md")
        assert await repo.get_version("a.md") is None
        assert await repo.get_version("none.md") is None

    async def test_previous_content_and_reason(self, repo: NotesRepository) -> None:
        """最後に変えたコミットの前の内容と、そのコミットの種類・時刻を返す。"""
        await repo.commit({"a.md": "v1"}, "create: a.md")
        await repo.commit({"a.md": "v2"}, "overwrite: a.md")
        await repo.commit({"b.md": "other"}, "create: b.md")
        version = await repo.get_version("a.md")
        assert (version.path, version.content, version.reason) == ("a.md", "v1", "overwrite")
        assert version.saved_at == datetime.fromtimestamp(1_800_000_001, tz=timezone.utc)

    async def test_deleted_file(self, repo: NotesRepository) -> None:
        """削除したファイルは削除前の内容を返す。"""
        await repo.commit({"a.md": "keep"}, "create: a.md")
        await repo.commit({"a.md": None}, "delete: a.md")
        version = await repo.get_version("a.md")
        assert (version.content, version.reason) == ("keep", "delete")
