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


async def test_find_one_reads_single_extension_without_id() -> None:
    """1 つの拡張のドキュメントを `_id` を除いて返す。"""
    collection = MagicMock()
    collection.find_one = AsyncMock(return_value={"extension": "diary", "prompt": "x"})
    repo = _make_repo(collection)

    assert await repo.find_one("diary") == {"extension": "diary", "prompt": "x"}
    collection.find_one.assert_awaited_once_with({"extension": "diary"}, {"_id": 0})


async def test_update_overrides_sets_and_unsets_only_given_paths_in_one_update() -> None:
    """指定したパスだけを 1 回の `update_one` で更新し、`updated_at` を書いて upsert する。"""
    collection = MagicMock()
    collection.update_one = AsyncMock()
    repo = _make_repo(collection)

    await repo.update_overrides(
        "diary", {"prompt": "", "tasks.task_a.schedule": "0 9 * * *"}, ["tasks.task_a.enabled"]
    )

    collection.update_one.assert_awaited_once()
    query, update = collection.update_one.await_args.args
    assert query == {"extension": "diary"}
    assert update["$set"]["prompt"] == ""
    assert update["$set"]["tasks.task_a.schedule"] == "0 9 * * *"
    assert update["$set"]["updated_at"].tzinfo is not None
    assert update["$unset"] == {"tasks.task_a.enabled": ""}
    assert collection.update_one.await_args.kwargs == {"upsert": True}


async def test_update_overrides_unset_only_does_not_create_document() -> None:
    """消すだけの更新はドキュメントを作らず、`updated_at` も書かない。"""
    collection = MagicMock()
    collection.update_one = AsyncMock()
    repo = _make_repo(collection)

    await repo.update_overrides("diary", {}, ["prompt"])

    query, update = collection.update_one.await_args.args
    assert update == {"$unset": {"prompt": ""}}
    assert collection.update_one.await_args.kwargs == {"upsert": False}


async def test_update_overrides_with_nothing_does_not_touch_db() -> None:
    """何も変えない呼び出しでは DB を読み書きしない。"""
    collection = MagicMock()
    collection.update_one = AsyncMock()
    repo = _make_repo(collection)

    await repo.update_overrides("diary", {}, [])

    collection.update_one.assert_not_awaited()


async def test_update_overrides_rejects_unsafe_paths() -> None:
    """空の要素・`$` 始まりの要素・set と unset の重複は書き込まずに拒否する。"""
    import pytest

    collection = MagicMock()
    collection.update_one = AsyncMock()
    repo = _make_repo(collection)

    for set_fields, unset_fields in (
        ({"tasks..schedule": "x"}, []),
        ({"$where": "x"}, []),
        ({}, ["tasks.$x.enabled"]),
        ({"prompt": "x"}, ["prompt"]),
    ):
        with pytest.raises(ValueError):
            await repo.update_overrides("diary", set_fields, unset_fields)
    collection.update_one.assert_not_awaited()
