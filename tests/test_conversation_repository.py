"""src/repository/conversation_repository.py のテスト。

CLAUDE.md のルールに従い、src/repository 配下のソースのテストはテストクラスを作らず
関数として記述する。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bson import ObjectId
from pymongo import DESCENDING

from lilla_core.core.exceptions import ConversationNotFoundError
from lilla_core.repository.conversation_repository import (
    ConversationRepository,
    SavedConversation,
)

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


# ---------------------------------------------------------------------------
# list_for_client / save_entry
# ---------------------------------------------------------------------------

_T0 = datetime(2026, 10, 30, 0, 0, 0, tzinfo=timezone.utc)


def _match(doc: dict, query: dict) -> bool:
    """list_for_client が組み立てる範囲のクエリだけを評価する簡易マッチャ。"""
    for key, cond in query.items():
        if key == "$and":
            if not all(_match(doc, sub) for sub in cond):
                return False
        elif key == "$or":
            if not any(_match(doc, sub) for sub in cond):
                return False
        elif isinstance(cond, dict):
            value = doc.get(key)
            for op, operand in cond.items():
                if op == "$gt" and not value > operand:
                    return False
                if op == "$gte" and not value >= operand:
                    return False
        elif doc.get(key) != cond:
            return False
    return True


class _FakeCursor:
    """sort / skip / limit / to_list だけを持つメモリ上のカーソル。"""

    def __init__(self, docs: list[dict]) -> None:
        self._docs = docs
        self._skip = 0
        self._limit = 0

    def sort(self, keys: list[tuple[str, int]]) -> "_FakeCursor":
        for key, direction in reversed(keys):
            self._docs.sort(key=lambda d: d[key], reverse=direction == DESCENDING)
        return self

    def skip(self, n: int) -> "_FakeCursor":
        self._skip = n
        return self

    def limit(self, n: int) -> "_FakeCursor":
        self._limit = n
        return self

    async def to_list(self, length: int | None = None) -> list[dict]:
        docs = self._docs[self._skip:]
        return docs[: self._limit] if self._limit else docs


class _FakeCollection:
    """insert_one / find / find_one を持つメモリ上のコレクション。

    BSON の日時がミリ秒精度で保存されるのを真似て、`time` はミリ秒に切り捨てて持つ。
    """

    def __init__(self) -> None:
        self.docs: list[dict] = []

    async def insert_one(self, doc: dict) -> MagicMock:
        stored = dict(doc)
        stored["_id"] = ObjectId()
        t = stored["time"]
        stored["time"] = t.replace(microsecond=t.microsecond // 1000 * 1000)
        self.docs.append(stored)
        return MagicMock(inserted_id=stored["_id"])

    def find(self, query: dict, projection: dict | None = None) -> _FakeCursor:
        return _FakeCursor([dict(d) for d in self.docs if _match(d, query)])

    async def find_one(self, query: dict, projection: dict | None = None) -> dict | None:
        for doc in self.docs:
            if _match(doc, query):
                return dict(doc)
        return None


def _add(collection: _FakeCollection, content: str, time: datetime, oid: str) -> str:
    """指定の time・_id でドキュメントを直接入れ、_id の文字列を返す。"""
    collection.docs.append(
        {"_id": ObjectId(oid), "time": time, "message": {"role": "assistant", "content": content}}
    )
    return oid


def _contents(result: list[dict]) -> list[str]:
    """list_for_client の結果から content だけを取り出す。"""
    return [item["message"]["content"] for item in result]


def _same_time_collection() -> tuple[_FakeCollection, dict[str, str]]:
    """古い順に a(T0) / b,c,d(T0+1s。_id 昇順) / e(T0+2s) を、挿入順を崩して入れる。"""
    collection = _FakeCollection()
    t1 = _T0 + timedelta(seconds=1)
    ids = {
        "a": _add(collection, "a", _T0, "65f0000000000000000000a0"),
        "d": _add(collection, "d", t1, "65f0000000000000000000d0"),
        "b": _add(collection, "b", t1, "65f0000000000000000000b0"),
        "e": _add(collection, "e", _T0 + timedelta(seconds=2), "65f0000000000000000000e0"),
        "c": _add(collection, "c", t1, "65f0000000000000000000c0"),
    }
    return collection, ids


async def test_list_for_client_default_is_newest_first_with_offset_limit() -> None:
    """引数なしは従来どおり新しい順で offset 件飛ばして limit 件返す。"""
    collection, _ = _same_time_collection()
    repo = _make_repo(collection)

    assert _contents(await repo.list_for_client(10, 0)) == ["e", "d", "c", "b", "a"]
    assert _contents(await repo.list_for_client(2, 1)) == ["d", "c"]


async def test_list_for_client_returns_id_time_message() -> None:
    """戻り値の各要素は _id（str）・time・message を持つ。"""
    collection, ids = _same_time_collection()
    repo = _make_repo(collection)

    first = (await repo.list_for_client(1, 0))[0]

    assert first == {
        "_id": ids["e"],
        "time": _T0 + timedelta(seconds=2),
        "message": {"role": "assistant", "content": "e"},
    }


async def test_list_for_client_sorts_by_time_and_id() -> None:
    """並び替えは (time, _id) の複合キーで、既定は両方とも降順・絞り込みなし。"""
    collection = _make_collection()
    cursor = _make_cursor([])
    cursor.skip = MagicMock(return_value=cursor)
    collection.find = MagicMock(return_value=cursor)
    repo = _make_repo(collection)

    await repo.list_for_client(10, 0)

    assert collection.find.call_args[0][0] == {}
    cursor.sort.assert_called_once_with([("time", DESCENDING), ("_id", DESCENDING)])


async def test_list_for_client_same_time_order_is_stable_across_pages() -> None:
    """同じ time のドキュメントは _id 順に並び、ページの境界で重複も欠けも出ない。"""
    collection, _ = _same_time_collection()
    repo = _make_repo(collection)

    pages = [_contents(await repo.list_for_client(2, offset)) for offset in (0, 2, 4)]

    assert pages == [["e", "d"], ["c", "b"], ["a"]]
    assert _contents(await repo.list_for_client(10, 0)) == _contents(
        await repo.list_for_client(10, 0)
    )


async def test_list_for_client_after_id_returns_newer_oldest_first() -> None:
    """after_id 指定時はそれより新しいものだけを古い順に返し、自身は含めない。"""
    collection, ids = _same_time_collection()
    repo = _make_repo(collection)

    assert _contents(await repo.list_for_client(10, 0, after_id=ids["a"])) == ["b", "c", "d", "e"]
    assert await repo.list_for_client(10, 0, after_id=ids["e"]) == []


async def test_list_for_client_after_id_keeps_same_time_followers() -> None:
    """after_id と同じ time の後続（_id が大きいもの）も取りこぼさない。"""
    collection, ids = _same_time_collection()
    repo = _make_repo(collection)

    assert _contents(await repo.list_for_client(10, 0, after_id=ids["b"])) == ["c", "d", "e"]
    assert _contents(await repo.list_for_client(10, 0, after_id=ids["c"])) == ["d", "e"]


async def test_list_for_client_after_id_paging_with_last_id() -> None:
    """limit で切った最後の _id を次の after_id に渡すと続きが欠けなく取れる。"""
    collection, ids = _same_time_collection()
    repo = _make_repo(collection)

    first = await repo.list_for_client(2, 0, after_id=ids["a"])
    second = await repo.list_for_client(2, 0, after_id=first[-1]["_id"])

    assert _contents(first) == ["b", "c"]
    assert _contents(second) == ["d", "e"]


@pytest.mark.parametrize(
    "bad_id", ["", "not-an-object-id", "65f0000000000000000000zz", "abcdefghijkl"]
)
async def test_list_for_client_after_id_invalid_format_raises_value_error(bad_id: str) -> None:
    """after_id が ObjectId として不正な形式なら ValueError。"""
    collection, _ = _same_time_collection()
    repo = _make_repo(collection)

    with pytest.raises(ValueError):
        await repo.list_for_client(10, 0, after_id=bad_id)


async def test_list_for_client_after_id_missing_raises_not_found() -> None:
    """after_id が存在せず since も無ければ ConversationNotFoundError。"""
    collection, _ = _same_time_collection()
    repo = _make_repo(collection)

    with pytest.raises(ConversationNotFoundError):
        await repo.list_for_client(10, 0, after_id="65f0000000000000000000ff")


async def test_list_for_client_after_id_missing_falls_back_to_since() -> None:
    """after_id が存在しなくても since があれば since だけで絞り込む。"""
    collection, _ = _same_time_collection()
    repo = _make_repo(collection)

    result = await repo.list_for_client(
        10, 0, after_id="65f0000000000000000000ff", since=_T0 + timedelta(seconds=1),
    )

    assert _contents(result) == ["b", "c", "d", "e"]


async def test_list_for_client_since_includes_boundary_oldest_first() -> None:
    """since は境界ちょうどの time を含み、古い順に返す。"""
    collection, _ = _same_time_collection()
    repo = _make_repo(collection)

    at_boundary = await repo.list_for_client(10, 0, since=_T0 + timedelta(seconds=1))
    after_boundary = await repo.list_for_client(
        10, 0, since=_T0 + timedelta(seconds=1, milliseconds=1),
    )

    assert _contents(at_boundary) == ["b", "c", "d", "e"]
    assert _contents(after_boundary) == ["e"]


async def test_list_for_client_after_id_and_since_are_combined() -> None:
    """after_id と since を両方渡すと、両方を満たすものだけを返す。"""
    collection, ids = _same_time_collection()
    repo = _make_repo(collection)

    result = await repo.list_for_client(
        10, 0, after_id=ids["a"], since=_T0 + timedelta(seconds=2),
    )

    assert _contents(result) == ["e"]


async def test_ensure_indexes_creates_time_id_index() -> None:
    """ensure_indexes が (time, _id) の複合インデックスを作り、time の TTL も維持する。"""
    collection = _make_collection()
    collection.create_index = AsyncMock()
    repo = _make_repo(collection)

    await repo.ensure_indexes()

    calls = collection.create_index.call_args_list
    assert [("time", 1), ("_id", 1)] in [call.args[0] for call in calls]
    ttl = [call for call in calls if call.args[0] == [("time", 1)]]
    assert ttl[0].kwargs["expireAfterSeconds"] == 72 * 3600


async def test_save_entry_returns_inserted_id_and_stored_time() -> None:
    """save_entry の id / time は採番された _id と insert_one に渡した time に一致する。"""
    collection = _make_collection()
    repo = _make_repo(collection)
    now = datetime(2026, 10, 30, 1, 2, 3, 456789, tzinfo=timezone.utc)

    with patch("lilla_core.repository.conversation_repository.utc_now", return_value=now):
        saved = await repo.save_entry({"role": "user", "content": "hello"}, tags=["dirty"])

    doc = collection.insert_one.call_args[0][0]
    assert isinstance(saved, SavedConversation)
    assert saved.id == _OID
    assert saved.time == doc["time"]
    # ミリ秒に切り捨てて保存する
    assert doc["time"] == datetime(2026, 10, 30, 1, 2, 3, 456000, tzinfo=timezone.utc)
    assert saved.time.tzinfo is not None
    assert doc["tags"] == ["dirty"]


async def test_save_returns_str_id_via_save_entry() -> None:
    """save は save_entry と同じ内容を保存し、_id の文字列だけを返す。"""
    collection = _make_collection()
    repo = _make_repo(collection)

    result = await repo.save({"role": "user", "content": "hello"}, discord_channel_id=1)

    assert result == _OID
    assert isinstance(result, str)
    assert collection.insert_one.call_args[0][0]["discord_channel_id"] == 1


async def test_save_entry_matches_list_for_client_entry() -> None:
    """マイクロ秒を含む現在時刻でも、save_entry の id / time が読み出した値と一致する。"""
    collection = _FakeCollection()
    repo = _make_repo(collection)
    now = datetime(2026, 10, 30, 1, 2, 3, 999999, tzinfo=timezone.utc)

    with patch("lilla_core.repository.conversation_repository.utc_now", return_value=now):
        saved = await repo.save_entry({"role": "assistant", "content": "x"})

    listed = (await repo.list_for_client(1, 0))[0]
    assert listed["_id"] == saved.id
    assert listed["time"] == saved.time


async def test_save_entry_values_as_after_id_and_since() -> None:
    """返した id を after_id に渡すと自身を含まず、time を since に渡すと自身を含む。"""
    collection = _FakeCollection()
    repo = _make_repo(collection)
    now = datetime(2026, 10, 30, 1, 2, 3, 500700, tzinfo=timezone.utc)

    with patch("lilla_core.repository.conversation_repository.utc_now", return_value=now):
        first = await repo.save_entry({"role": "assistant", "content": "block1"})
        await repo.save_entry({"role": "assistant", "content": "block2"})

    assert _contents(await repo.list_for_client(10, 0, after_id=first.id)) == ["block2"]
    assert _contents(await repo.list_for_client(10, 0, since=first.time)) == ["block1", "block2"]
