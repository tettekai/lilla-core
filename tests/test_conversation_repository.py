"""src/repository/conversation_repository.py のテスト。

CLAUDE.md のルールに従い、src/repository 配下のソースのテストはテストクラスを作らず
関数として記述する。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from bson import ObjectId

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
