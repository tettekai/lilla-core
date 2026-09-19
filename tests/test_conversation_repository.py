"""src/repository/conversation_repository.py のテスト。

CLAUDE.md のルールに従い、src/repository 配下のソースのテストはテストクラスを作らず
関数として記述する。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from bson import ObjectId
from pymongo import DESCENDING

from lilla_core.repository.conversation_repository import ConversationRepository

_OID = "65f0000000000000000000aa"


def _make_repo(collection: MagicMock) -> ConversationRepository:
    """MongoDB へ接続せずにコレクションだけ差し替えたリポジトリを返す。"""
    repo = ConversationRepository.__new__(ConversationRepository)
    repo._collection = collection
    repo._ttl_hours = 72
    return repo


def _make_collection() -> MagicMock:
    """insert_one / delete_one をモックしたコレクションを返す。"""
    collection = MagicMock()
    collection.insert_one = AsyncMock(return_value=MagicMock(inserted_id=ObjectId(_OID)))
    collection.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))
    return collection


def _make_cursor(docs: list[dict]) -> MagicMock:
    """find() が返すカーソルのモックを返す（sort / limit はチェーン可能）。"""
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=docs)
    return cursor


async def test_save_stores_message_and_returns_id() -> None:
    """save は message と time を保存し、保存した _id を文字列で返す。"""
    collection = _make_collection()
    repo = _make_repo(collection)

    result = await repo.save({"role": "user", "content": "hello"})

    assert result == _OID
    doc = collection.insert_one.call_args[0][0]
    assert doc["message"] == {"role": "user", "content": "hello"}
    assert "time" in doc


async def test_save_omits_optional_fields_by_default() -> None:
    """任意引数を渡さない場合は tags / Discord メッセージ情報を保存しない。"""
    collection = _make_collection()
    repo = _make_repo(collection)

    await repo.save({"role": "user", "content": "hello"})

    doc = collection.insert_one.call_args[0][0]
    assert "tags" not in doc
    assert "discord_channel_id" not in doc
    assert "discord_message_ids" not in doc


async def test_save_stores_tags_and_discord_message_info() -> None:
    """tags / discord_channel_id / discord_message_ids が保存される。"""
    collection = _make_collection()
    repo = _make_repo(collection)

    await repo.save(
        {"role": "assistant", "content": "hello"},
        tags=["toolresult", "dirty"],
        discord_channel_id=555,
        discord_message_ids=[1000, 1001],
    )

    doc = collection.insert_one.call_args[0][0]
    assert doc["tags"] == ["toolresult", "dirty"]
    assert doc["discord_channel_id"] == 555
    assert doc["discord_message_ids"] == [1000, 1001]


async def test_save_stores_channel_id_zero() -> None:
    """チャンネル ID が 0 でも（None でない限り）保存される。"""
    collection = _make_collection()
    repo = _make_repo(collection)

    await repo.save({"role": "assistant", "content": "hello"}, discord_channel_id=0)

    assert collection.insert_one.call_args[0][0]["discord_channel_id"] == 0


async def test_find_latest_by_tag_returns_newest_document() -> None:
    """指定タグの最新 1 件を time の降順で取得する。"""
    doc = {"_id": ObjectId(_OID), "message": {"role": "assistant", "content": "結果"}}
    cursor = _make_cursor([doc])
    collection = _make_collection()
    collection.find = MagicMock(return_value=cursor)
    repo = _make_repo(collection)

    result = await repo.find_latest_by_tag("dirty")

    assert result is doc
    collection.find.assert_called_once_with({"tags": "dirty"})
    assert cursor.sort.call_args[0][0] == "time"
    cursor.limit.assert_called_once_with(1)


async def test_find_latest_by_tag_returns_none_when_empty() -> None:
    """該当がなければ None を返す。"""
    collection = _make_collection()
    collection.find = MagicMock(return_value=_make_cursor([]))
    repo = _make_repo(collection)

    assert await repo.find_latest_by_tag("dirty") is None


async def test_delete_removes_document_by_id() -> None:
    """delete は _id を ObjectId に変換して削除し、True を返す。"""
    collection = _make_collection()
    repo = _make_repo(collection)

    assert await repo.delete(_OID) is True
    collection.delete_one.assert_called_once_with({"_id": ObjectId(_OID)})


async def test_delete_returns_false_when_not_found() -> None:
    """該当ドキュメントがなければ False を返す。"""
    collection = _make_collection()
    collection.delete_one = AsyncMock(return_value=MagicMock(deleted_count=0))
    repo = _make_repo(collection)

    assert await repo.delete(_OID) is False


async def test_ensure_indexes_creates_channel_time_index() -> None:
    """ensure_indexes が (discord_channel_id, time) の複合インデックスも作る。"""
    collection = _make_collection()
    collection.create_index = AsyncMock()
    repo = _make_repo(collection)

    await repo.ensure_indexes()

    keys = [call.args[0] for call in collection.create_index.call_args_list]
    assert [("discord_channel_id", 1), ("time", 1)] in keys


async def test_load_by_channel_between_filters_by_channel_and_period() -> None:
    """load_by_channel_between がチャンネルと [start, end) で絞り、古い順に返す。"""
    docs = [
        {"message": {"role": "user", "content": "a"}, "time": datetime(2026, 9, 15, 1)},
        {"message": {"role": "assistant", "content": "b"}, "time": datetime(2026, 9, 15, 2)},
    ]
    cursor = _make_cursor(docs)
    collection = _make_collection()
    collection.find = MagicMock(return_value=cursor)
    repo = _make_repo(collection)

    start = datetime(2026, 9, 15, tzinfo=timezone.utc)
    end = datetime(2026, 9, 16, tzinfo=timezone.utc)
    result = await repo.load_by_channel_between(100, start, end, 10)

    assert result == docs
    query = collection.find.call_args[0][0]
    assert query["discord_channel_id"] == 100
    assert query["time"] == {"$gte": start, "$lt": end}
    assert cursor.sort.call_args[0] == ("time", 1)


async def test_load_by_channel_between_keeps_latest_when_over_limit() -> None:
    """max_turns を超える場合は末尾（直近）を残す。"""
    docs = [
        {"message": {"role": "user", "content": str(i)}, "time": datetime(2026, 9, 15, i)}
        for i in range(5)
    ]
    collection = _make_collection()
    collection.find = MagicMock(return_value=_make_cursor(docs))
    repo = _make_repo(collection)

    result = await repo.load_by_channel_between(
        100, datetime(2026, 9, 15, tzinfo=timezone.utc),
        datetime(2026, 9, 16, tzinfo=timezone.utc), 2,
    )

    assert [doc["message"]["content"] for doc in result] == ["3", "4"]


async def test_load_by_channel_between_returns_empty_list() -> None:
    """該当が無ければ空リストを返す。"""
    collection = _make_collection()
    collection.find = MagicMock(return_value=_make_cursor([]))
    repo = _make_repo(collection)

    result = await repo.load_by_channel_between(
        100, datetime(2026, 9, 15, tzinfo=timezone.utc),
        datetime(2026, 9, 16, tzinfo=timezone.utc), 10,
    )

    assert result == []


async def test_search_without_conditions_uses_empty_filter() -> None:
    """条件を 1 つも指定しなければ絞り込みなしで新しい順に取得する。"""
    collection = _make_collection()
    cursor = _make_cursor([])
    collection.find = MagicMock(return_value=cursor)
    repo = _make_repo(collection)

    await repo.search(limit=10)

    assert collection.find.call_args[0][0] == {}
    cursor.sort.assert_called_once_with("time", DESCENDING)
    cursor.limit.assert_called_once_with(10)


async def test_search_builds_time_range_filter() -> None:
    """start / end を指定すると time の範囲条件を組み立てる（両端を含む）。"""
    collection = _make_collection()
    collection.find = MagicMock(return_value=_make_cursor([]))
    repo = _make_repo(collection)
    start = datetime(2026, 9, 15, tzinfo=timezone.utc)
    end = datetime(2026, 9, 16, tzinfo=timezone.utc)

    await repo.search(start=start, end=end)

    assert collection.find.call_args[0][0]["time"] == {"$gte": start, "$lte": end}


async def test_search_allows_open_ended_time_range() -> None:
    """start だけを指定した場合は下限のみの条件にする。"""
    collection = _make_collection()
    collection.find = MagicMock(return_value=_make_cursor([]))
    repo = _make_repo(collection)
    start = datetime(2026, 9, 15, tzinfo=timezone.utc)

    await repo.search(start=start)

    assert collection.find.call_args[0][0]["time"] == {"$gte": start}


async def test_search_builds_and_filter_for_each_keyword() -> None:
    """キーワードは message.content への AND 条件として 1 つずつ積む。"""
    collection = _make_collection()
    collection.find = MagicMock(return_value=_make_cursor([]))
    repo = _make_repo(collection)

    await repo.search(keywords=["体調", "睡眠"])

    assert collection.find.call_args[0][0]["$and"] == [
        {"message.content": {"$regex": "体調", "$options": "i"}},
        {"message.content": {"$regex": "睡眠", "$options": "i"}},
    ]


async def test_search_escapes_regex_metacharacters_in_keywords() -> None:
    """キーワードはエスケープして渡す（正規表現として解釈させない）。"""
    collection = _make_collection()
    collection.find = MagicMock(return_value=_make_cursor([]))
    repo = _make_repo(collection)

    await repo.search(keywords=["(a+)+$", "a.c"])

    patterns = [
        condition["message.content"]["$regex"]
        for condition in collection.find.call_args[0][0]["$and"]
    ]
    assert patterns == [re.escape("(a+)+$"), re.escape("a.c")]
    # エスケープ済みパターンはリテラルとしてのみ一致する。
    assert re.search(patterns[1], "a.c") is not None
    assert re.search(patterns[1], "abc") is None


async def test_search_ignores_empty_keywords() -> None:
    """キーワードが空リストなら $and 条件を積まない。"""
    collection = _make_collection()
    collection.find = MagicMock(return_value=_make_cursor([]))
    repo = _make_repo(collection)

    await repo.search(keywords=[])

    assert "$and" not in collection.find.call_args[0][0]


async def test_search_filters_by_role_and_channel() -> None:
    """role / discord_channel_id を指定すると、それぞれの条件を足す。"""
    collection = _make_collection()
    collection.find = MagicMock(return_value=_make_cursor([]))
    repo = _make_repo(collection)

    await repo.search(role="user", discord_channel_id=100)

    mongo_filter = collection.find.call_args[0][0]
    assert mongo_filter["message.role"] == "user"
    assert mongo_filter["discord_channel_id"] == 100


async def test_search_without_channel_does_not_filter_by_channel() -> None:
    """discord_channel_id を省略すると全チャンネル横断のままにする。"""
    collection = _make_collection()
    collection.find = MagicMock(return_value=_make_cursor([]))
    repo = _make_repo(collection)

    await repo.search(role=None, discord_channel_id=None)

    mongo_filter = collection.find.call_args[0][0]
    assert "discord_channel_id" not in mongo_filter
    assert "message.role" not in mongo_filter


async def test_search_returns_documents_in_stored_order() -> None:
    """カーソルが返したドキュメントをそのまま（新しい順で）返す。"""
    docs = [
        {"message": {"role": "user", "content": "new"}, "time": datetime(2026, 9, 16)},
        {"message": {"role": "user", "content": "old"}, "time": datetime(2026, 9, 15)},
    ]
    collection = _make_collection()
    collection.find = MagicMock(return_value=_make_cursor(docs))
    repo = _make_repo(collection)

    result = await repo.search()

    assert [doc["message"]["content"] for doc in result] == ["new", "old"]
