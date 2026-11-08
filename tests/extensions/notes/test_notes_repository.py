"""`lilla_core.extensions.notes.repository` のテスト（コレクションはモック）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pymongo.errors import DuplicateKeyError

TTL = timedelta(days=30)


@pytest.fixture
def repo():
    """コレクションをモックにした `NotesRepository` を返す。"""
    from lilla_core.extensions.notes.repository import NotesRepository

    collections = {"notes": MagicMock(), "notes_history": MagicMock()}
    for collection in collections.values():
        for name in (
            "create_index", "find_one", "insert_one", "find_one_and_update",
            "find_one_and_delete", "replace_one", "delete_one",
        ):
            setattr(collection, name, AsyncMock())
    db = MagicMock()
    db.__getitem__.side_effect = collections.__getitem__
    client = MagicMock()
    client.__getitem__.return_value = db
    with patch("lilla_core.extensions.notes.repository.create_mongo_client", return_value=client):
        instance = NotesRepository("mongodb://example", "db")
    instance.notes_mock = collections["notes"]
    instance.history_mock = collections["notes_history"]
    return instance


class TestNotesRepository:
    """書き込みと直前の版。"""

    async def test_indexes(self, repo) -> None:
        """path のユニーク・updated_at・直前の版の TTL を張る。"""
        await repo.init_collection()
        notes_calls = [c.args[0] for c in repo.notes_mock.create_index.call_args_list]
        assert [("path", 1)] in notes_calls
        assert [("updated_at", -1)] in notes_calls
        ttl = [c for c in repo.history_mock.create_index.call_args_list if c.args[0] == [("expire_at", 1)]]
        assert ttl and ttl[0].kwargs["expireAfterSeconds"] == 0

    async def test_create_duplicate(self, repo) -> None:
        """重複キーは `NoteExistsError`。"""
        from lilla_core.extensions.notes.repository import NoteExistsError

        repo.notes_mock.insert_one.side_effect = DuplicateKeyError("dup")
        with pytest.raises(NoteExistsError):
            await repo.create("a.md", "x")

    async def test_overwrite_new(self, repo) -> None:
        """前が無ければ作ったことを返し、直前の版は残さない。"""
        repo.notes_mock.find_one_and_update.return_value = None
        assert await repo.overwrite("a.md", "x", TTL) is True
        repo.history_mock.replace_one.assert_not_awaited()

    async def test_overwrite_saves_previous(self, repo) -> None:
        """内容が変われば前の内容を直前の版へ残す。"""
        repo.notes_mock.find_one_and_update.return_value = {"path": "a.md", "content": "old"}
        assert await repo.overwrite("a.md", "new", TTL) is False
        saved = repo.history_mock.replace_one.await_args.args[1]
        assert (saved["content"], saved["reason"]) == ("old", "overwrite")
        assert saved["expire_at"] - saved["saved_at"] == TTL

    async def test_overwrite_same_content(self, repo) -> None:
        """内容が同じなら直前の版を書き換えない。"""
        repo.notes_mock.find_one_and_update.return_value = {"path": "a.md", "content": "same"}
        await repo.overwrite("a.md", "same", TTL)
        repo.history_mock.replace_one.assert_not_awaited()

    async def test_delete(self, repo) -> None:
        """消したら前の内容を残し、無ければ False。"""
        repo.notes_mock.find_one_and_delete.return_value = {"path": "a.md", "content": "c"}
        assert await repo.delete("a.md", TTL) is True
        assert repo.history_mock.replace_one.await_args.args[1]["reason"] == "delete"
        repo.notes_mock.find_one_and_delete.return_value = None
        assert await repo.delete("b.md", TTL) is False

    async def test_expired_version_is_hidden(self, repo) -> None:
        """TTL の掃除前でも、期限切れの版は返さない。"""
        past = datetime.now(timezone.utc) - timedelta(seconds=1)
        repo.history_mock.find_one.return_value = {
            "path": "a.md", "content": "c", "reason": "delete", "saved_at": past, "expire_at": past,
        }
        assert await repo.get_version("a.md") is None

    async def test_append_uses_pipeline(self, repo) -> None:
        """追記はパイプラインの更新で行い、作ったかどうかを返す。"""
        repo.notes_mock.find_one_and_update.return_value = None
        assert await repo.append("a.md", "x") is True
        update = repo.notes_mock.find_one_and_update.await_args.args[1]
        assert isinstance(update, list)
