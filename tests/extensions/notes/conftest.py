"""`notes` 拡張のテストで共通の fixture。

MongoDB の代わりに、`NotesRepository` と同じメソッドを持つメモリ上の偽物を
`service._repo` へ差し込む（リポジトリそのものの振る舞いは `test_notes_repository.py`
がコレクションをモックにして確かめる）。
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from lilla_core.extensions import notes

_TOOLS_DIR = Path(notes.__file__).resolve().parent / "tools"


class FakeNotesRepository:
    """`NotesRepository` のメモリ上の偽物（直前の版も 1 パス 1 件で持つ）。"""

    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}
        self.history: dict[str, dict] = {}
        self._tick = 0

    def _now(self) -> datetime:
        """呼ぶたびに 1 秒進む時刻を返す（更新日時の前後を確かめやすくする）。"""
        self._tick += 1
        return datetime(2026, 11, 8, tzinfo=timezone.utc) + timedelta(seconds=self._tick)

    def _meta(self, doc):
        from lilla_core.extensions.notes.repository import NoteMeta

        return NoteMeta(doc["path"], len(doc["content"]), doc["created_at"], doc["updated_at"])

    def _under(self, directory: str) -> list[dict]:
        prefix = directory + "/" if directory else ""
        return [self.docs[p] for p in sorted(self.docs) if p.startswith(prefix)]

    async def get(self, path):
        from lilla_core.extensions.notes.repository import Note

        doc = self.docs.get(path)
        if doc is None:
            return None
        return Note(doc["path"], doc["content"], doc["created_at"], doc["updated_at"])

    async def get_meta(self, path):
        doc = self.docs.get(path)
        return self._meta(doc) if doc else None

    async def has_children(self, directory):
        return bool(self._under(directory))

    async def list_under(self, directory):
        return [self._meta(d) for d in self._under(directory)]

    async def search(self, directory, query, limit):
        from lilla_core.extensions.notes.repository import Note

        docs = [
            d
            for p, d in sorted(self.docs.items())
            if (not directory or p == directory or p.startswith(directory + "/"))
            and query.casefold() in d["content"].casefold()
        ]
        notes_ = [Note(d["path"], d["content"], d["created_at"], d["updated_at"]) for d in docs]
        return notes_[:limit], len(notes_) > limit

    async def create(self, path, content):
        from lilla_core.extensions.notes.repository import NoteExistsError

        if path in self.docs:
            raise NoteExistsError(path)
        now = self._now()
        self.docs[path] = {"path": path, "content": content, "created_at": now, "updated_at": now}

    async def overwrite(self, path, content, history_ttl):
        before = self.docs.get(path)
        now = self._now()
        if before is None:
            self.docs[path] = {"path": path, "content": content, "created_at": now, "updated_at": now}
            return True
        if before["content"] != content:
            self._save(path, before["content"], "overwrite")
        self.docs[path] = {**before, "content": content, "updated_at": now}
        return False

    async def append(self, path, content):
        before = self.docs.get(path)
        now = self._now()
        if before is None:
            self.docs[path] = {"path": path, "content": content, "created_at": now, "updated_at": now}
            return True
        self.docs[path] = {**before, "content": before["content"] + content, "updated_at": now}
        return False

    async def delete(self, path, history_ttl):
        before = self.docs.pop(path, None)
        if before is None:
            return False
        self._save(path, before["content"], "delete")
        return True

    async def get_version(self, path):
        from lilla_core.extensions.notes.repository import NoteVersion

        doc = self.history.get(path)
        if doc is None:
            return None
        return NoteVersion(path, doc["content"], doc["reason"], doc["saved_at"])

    async def restore(self, path, history_ttl):
        version = self.history.get(path)
        if version is None:
            return False
        before = self.docs.get(path)
        await self.overwrite(path, version["content"], history_ttl)
        if before is not None and before["content"] != version["content"]:
            self._save(path, before["content"], "restore")
        else:
            self.history.pop(path, None)
        return True

    def _save(self, path, content, reason):
        self.history[path] = {"content": content, "reason": reason, "saved_at": self._now()}


@pytest.fixture
def notes_config() -> notes.NotesConfig:
    """小さめの上限を持つ設定。"""
    return notes.NotesConfig(max_content_chars=100, max_read_chars=10)


@pytest.fixture
def fake_repo(notes_config, monkeypatch: pytest.MonkeyPatch) -> FakeNotesRepository:
    """`service` が使うリポジトリと設定を差し替える。"""
    from lilla_core.extensions.notes import service

    repo = FakeNotesRepository()
    monkeypatch.setattr(service, "_repo", lambda: repo)
    monkeypatch.setattr(service, "load_config", lambda: notes_config)
    return repo


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
