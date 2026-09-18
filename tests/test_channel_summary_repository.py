"""src/repository/channel_summary_repository.py のテスト。

CLAUDE.md のルールに従い、src/repository 配下のソースのテストはテストクラスを作らず
関数として記述する。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from lilla_core.repository.channel_summary_repository import ChannelSummaryRepository


def _make_repo(collection: MagicMock) -> ChannelSummaryRepository:
    """MongoDB へ接続せずにコレクションだけ差し替えたリポジトリを返す。"""
    repo = ChannelSummaryRepository.__new__(ChannelSummaryRepository)
    repo._collection = collection
    return repo


def _make_collection() -> MagicMock:
    """update_one / find_one / create_index をモックしたコレクションを返す。"""
    collection = MagicMock()
    collection.update_one = AsyncMock()
    collection.find_one = AsyncMock(return_value=None)
    collection.create_index = AsyncMock()
    return collection


async def test_ensure_indexes_creates_unique_channel_index() -> None:
    """discord_channel_id にユニークインデックスを張る。"""
    collection = _make_collection()
    repo = _make_repo(collection)

    await repo.ensure_indexes()

    collection.create_index.assert_awaited_once()
    assert collection.create_index.call_args.args[0] == [("discord_channel_id", 1)]
    assert collection.create_index.call_args.kwargs["unique"] is True


async def test_init_collection_ensures_indexes() -> None:
    """init_collection は ensure_indexes を呼ぶ。"""
    collection = _make_collection()
    repo = _make_repo(collection)

    await repo.init_collection()

    collection.create_index.assert_awaited_once()


async def test_upsert_writes_all_fields() -> None:
    """upsert が discord_channel_id をキーに全フィールドを書き込む。"""
    collection = _make_collection()
    repo = _make_repo(collection)

    await repo.upsert(
        discord_channel_id=100,
        channel_name="dev",
        summary="- 決めたこと",
        summary_date="2026-09-15",
    )

    filter_, update = collection.update_one.call_args.args
    assert filter_ == {"discord_channel_id": 100}
    assert collection.update_one.call_args.kwargs["upsert"] is True
    doc = update["$set"]
    assert doc["discord_channel_id"] == 100
    assert doc["channel_name"] == "dev"
    assert doc["summary"] == "- 決めたこと"
    assert doc["summary_date"] == "2026-09-15"
    assert "updated_at" in doc


async def test_find_by_channel_id_returns_document() -> None:
    """保存済みのドキュメントを返す（_id は含めない）。"""
    collection = _make_collection()
    collection.find_one = AsyncMock(return_value={"summary": "note"})
    repo = _make_repo(collection)

    result = await repo.find_by_channel_id(100)

    assert result == {"summary": "note"}
    assert collection.find_one.call_args.args[0] == {"discord_channel_id": 100}
    assert collection.find_one.call_args.args[1] == {"_id": 0}


async def test_find_by_channel_id_returns_none_when_missing() -> None:
    """該当が無ければ None を返す。"""
    collection = _make_collection()
    repo = _make_repo(collection)

    assert await repo.find_by_channel_id(100) is None
