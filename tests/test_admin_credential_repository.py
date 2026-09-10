"""src/repository/admin_credential_repository.py のテスト。

CLAUDE.md のルールに従い、src/repository 配下のソースのテストはテストクラスを作らず
関数として記述する。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from lilla_core.repository.admin_credential_repository import (
    CREDENTIAL_ID,
    AdminCredentialRepository,
)


def _make_repo(collection: MagicMock) -> AdminCredentialRepository:
    """MongoDB へ接続せずにコレクションだけ差し替えたリポジトリを返す。"""
    repo = AdminCredentialRepository.__new__(AdminCredentialRepository)
    repo._collection = collection
    return repo


def _make_collection(doc: dict | None = None, upserted_id: object = None) -> MagicMock:
    """find_one / update_one をモックしたコレクションを返す。"""
    collection = MagicMock()
    collection.find_one = AsyncMock(return_value=doc)
    update_result = MagicMock()
    update_result.upserted_id = upserted_id
    collection.update_one = AsyncMock(return_value=update_result)
    return collection


async def test_exists_returns_false_when_empty() -> None:
    """レコードが無ければ exists は False を返す。"""
    repo = _make_repo(_make_collection(doc=None))

    assert await repo.exists() is False


async def test_exists_returns_true_when_registered() -> None:
    """レコードがあれば exists は True を返す。"""
    repo = _make_repo(_make_collection(doc={"_id": CREDENTIAL_ID}))

    assert await repo.exists() is True


async def test_get_password_hash_returns_bytes() -> None:
    """保存済みのハッシュを bytes で返す。"""
    repo = _make_repo(_make_collection(doc={"password_hash": b"$2b$12$hash"}))

    assert await repo.get_password_hash() == b"$2b$12$hash"


async def test_get_password_hash_returns_none_when_empty() -> None:
    """レコードが無ければ None を返す。"""
    repo = _make_repo(_make_collection(doc=None))

    assert await repo.get_password_hash() is None


async def test_save_password_hash_inserts_with_fixed_id() -> None:
    """固定 _id で $setOnInsert により登録する。"""
    collection = _make_collection(upserted_id=CREDENTIAL_ID)
    repo = _make_repo(collection)

    result = await repo.save_password_hash(b"$2b$12$hash")

    assert result is True
    filter_arg, update_arg = collection.update_one.call_args[0]
    assert filter_arg == {"_id": CREDENTIAL_ID}
    assert update_arg["$setOnInsert"]["password_hash"] == b"$2b$12$hash"
    assert collection.update_one.call_args[1]["upsert"] is True


async def test_save_password_hash_does_not_overwrite() -> None:
    """既に登録済みなら上書きせず False を返す。"""
    collection = _make_collection(upserted_id=None)
    repo = _make_repo(collection)

    assert await repo.save_password_hash(b"$2b$12$other") is False
