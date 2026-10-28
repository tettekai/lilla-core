"""src/lilla_core/repository/extension_override_repository.py のテスト。

CLAUDE.md のルールに従い、src/repository 配下のソースのテストはテストクラスを作らず
関数として記述する。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from pymongo import ASCENDING

from lilla_core.repository.extension_override_repository import (
    COLLECTION_NAME,
    ExtensionOverrideRepository,
)


def _make_repo(collection: MagicMock) -> ExtensionOverrideRepository:
    """`collection` を返す Mongo クライアントでリポジトリを組み立てる。"""
    client = MagicMock()
    client.__getitem__.return_value.__getitem__.return_value = collection
    with patch(
        "lilla_core.repository.extension_override_repository.create_mongo_client",
        return_value=client,
    ):
        repo = ExtensionOverrideRepository("mongodb://localhost:27017/test", "testdb")
    client.__getitem__.return_value.__getitem__.assert_called_once_with(COLLECTION_NAME)
    return repo


async def test_init_collection_creates_unique_extension_index() -> None:
    """起動時に `extension` のユニークインデックスを作る。"""
    collection = MagicMock()
    collection.create_index = AsyncMock()
    repo = _make_repo(collection)

    await repo.init_collection()

    collection.create_index.assert_awaited_once_with([("extension", ASCENDING)], unique=True)


async def test_find_by_extensions_returns_docs_keyed_by_extension() -> None:
    """指定した拡張名のドキュメントを拡張名をキーにして返す。"""
    docs = [
        {"extension": "diary", "prompt": "上書き"},
        {"extension": "habits", "settings": {}},
    ]
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=docs)
    collection = MagicMock()
    collection.find = MagicMock(return_value=cursor)
    repo = _make_repo(collection)

    result = await repo.find_by_extensions(["diary", "habits", "other"])

    assert result == {"diary": docs[0], "habits": docs[1]}
    collection.find.assert_called_once_with(
        {"extension": {"$in": ["diary", "habits", "other"]}}, {"_id": 0}
    )


async def test_find_by_extensions_skips_db_for_empty_names() -> None:
    """名前が空なら DB を読まずに空の dict を返す。"""
    collection = MagicMock()
    repo = _make_repo(collection)

    assert await repo.find_by_extensions([]) == {}
    collection.find.assert_not_called()
