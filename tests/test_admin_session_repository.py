"""src/repository/admin_session_repository.py のテスト。

CLAUDE.md のルールに従い、src/repository 配下のソースのテストはテストクラスを作らず
関数として記述する。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from pymongo import ASCENDING

from lilla_core.repository.admin_session_repository import AdminSessionRepository

_HASH = "a" * 64


def _make_repo(collection: MagicMock) -> AdminSessionRepository:
    """MongoDB へ接続せずにコレクションだけ差し替えたリポジトリを返す。"""
    repo = AdminSessionRepository.__new__(AdminSessionRepository)
    repo._collection = collection
    return repo


def _make_collection(doc: dict | None = None, deleted_count: int = 1) -> MagicMock:
    """insert_one / find_one / delete_one / create_index をモックしたコレクションを返す。"""
    collection = MagicMock()
    collection.insert_one = AsyncMock()
    collection.find_one = AsyncMock(return_value=doc)
    delete_result = MagicMock()
    delete_result.deleted_count = deleted_count
    collection.delete_one = AsyncMock(return_value=delete_result)
    collection.create_index = AsyncMock()
    return collection


async def test_ensure_indexes_creates_ttl_index() -> None:
    """expires_at に expireAfterSeconds=0 の TTL インデックスを作成する。"""
    collection = _make_collection()
    repo = _make_repo(collection)

    await repo.ensure_indexes()

    keys = collection.create_index.call_args[0][0]
    assert keys == [("expires_at", ASCENDING)]
    assert collection.create_index.call_args[1]["expireAfterSeconds"] == 0


async def test_init_collection_creates_indexes() -> None:
    """init_collection から TTL インデックス作成が呼ばれる。"""
    collection = _make_collection()
    repo = _make_repo(collection)

    await repo.init_collection()

    collection.create_index.assert_awaited_once()


def test_default_expires_at_is_30_days_ahead() -> None:
    """デフォルトの有効期限は 30 日後になる。"""
    repo = _make_repo(_make_collection())

    expires_at = repo.default_expires_at()

    delta = expires_at - datetime.now(timezone.utc)
    assert timedelta(days=29, hours=23) < delta <= timedelta(days=30)


async def test_create_stores_hash_and_expiry() -> None:
    """ハッシュを _id として有効期限つきで保存する。"""
    collection = _make_collection()
    repo = _make_repo(collection)
    expires_at = datetime(2026, 9, 24, tzinfo=timezone.utc)

    await repo.create(_HASH, expires_at)

    doc = collection.insert_one.call_args[0][0]
    assert doc["_id"] == _HASH
    assert doc["session_id_hash"] == _HASH
    assert doc["expires_at"] == expires_at
    assert "created_at" in doc


async def test_find_valid_filters_by_expiry() -> None:
    """expires_at が現在時刻より未来のものだけを検索する。"""
    collection = _make_collection(doc={"_id": _HASH})
    repo = _make_repo(collection)

    result = await repo.find_valid(_HASH)

    assert result == {"_id": _HASH}
    query = collection.find_one.call_args[0][0]
    assert query["_id"] == _HASH
    assert "$gt" in query["expires_at"]


async def test_find_valid_returns_none_when_expired() -> None:
    """期限切れ（該当ドキュメント無し）の場合は None を返す。"""
    repo = _make_repo(_make_collection(doc=None))

    assert await repo.find_valid(_HASH) is None


async def test_delete_removes_session() -> None:
    """ログアウト時にセッションを削除する。"""
    collection = _make_collection(deleted_count=1)
    repo = _make_repo(collection)

    assert await repo.delete(_HASH) is True
    collection.delete_one.assert_awaited_once_with({"_id": _HASH})


async def test_delete_returns_false_when_missing() -> None:
    """対象が存在しなければ False を返す。"""
    repo = _make_repo(_make_collection(deleted_count=0))

    assert await repo.delete(_HASH) is False
