"""src/lilla_core/repository/extension_settings_repository.py のテスト。

CLAUDE.md のルールに従い、src/repository 配下のソースのテストはテストクラスを作らず
関数として記述する。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from pymongo import ASCENDING

from lilla_core.repository.extension_settings_repository import (
    COLLECTION_NAME,
    ExtensionSettingsRepository,
)


def _make_repo(collection: MagicMock) -> ExtensionSettingsRepository:
    """`collection` を返す Mongo クライアントでリポジトリを組み立てる。"""
    client = MagicMock()
    client.__getitem__.return_value.__getitem__.return_value = collection
    with patch(
        "lilla_core.repository.extension_settings_repository.create_mongo_client",
        return_value=client,
    ):
        repo = ExtensionSettingsRepository("mongodb://localhost:27017/test", "testdb")
    client.__getitem__.return_value.__getitem__.assert_called_once_with(COLLECTION_NAME)
    return repo


async def test_init_collection_creates_unique_extension_index() -> None:
    """起動時に `extension` のユニークインデックスを作る。"""
    collection = MagicMock()
    collection.create_index = AsyncMock()
    repo = _make_repo(collection)

    await repo.init_collection()

    collection.create_index.assert_awaited_once_with([("extension", ASCENDING)], unique=True)


async def test_find_one_reads_single_extension_without_id() -> None:
    """1 つの拡張のドキュメントを `_id` を除いて返す。"""
    collection = MagicMock()
    collection.find_one = AsyncMock(return_value=None)
    repo = _make_repo(collection)

    assert await repo.find_one("diary") is None
    collection.find_one.assert_awaited_once_with({"extension": "diary"}, {"_id": 0})


async def test_save_replaces_whole_set_with_utc_timestamp() -> None:
    """一式を 1 回の `replace_one` で置き換え、UTC の `updated_at` を書いて upsert する。"""
    collection = MagicMock()
    collection.replace_one = AsyncMock()
    repo = _make_repo(collection)

    await repo.save("diary", {"template": None, "day_rollover": "04:00"})

    collection.replace_one.assert_awaited_once()
    query, doc = collection.replace_one.await_args.args
    assert query == {"extension": "diary"}
    assert doc["extension"] == "diary"
    assert doc["values"] == {"template": None, "day_rollover": "04:00"}
    assert doc["updated_at"].utcoffset().total_seconds() == 0
    assert collection.replace_one.await_args.kwargs == {"upsert": True}
