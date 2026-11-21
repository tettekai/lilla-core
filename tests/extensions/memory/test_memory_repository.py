"""`lilla_core.extensions.memory.repository` のテスト。

remember のリポジトリは `src/lilla_core/repository` 配下ではないため、CLAUDE.md のルールに従い
テストクラスにまとめる。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lilla_core.extensions.memory.repository import RememberRepository


def _make_repo(collection: MagicMock) -> RememberRepository:
    """MongoDB へ接続せずにコレクションだけ差し替えたリポジトリを返す。"""
    repo = RememberRepository.__new__(RememberRepository)
    repo._collection = collection
    return repo


@pytest.fixture
def collection() -> MagicMock:
    """`find_one` / `update_one` を持つコレクションのモック。"""
    col = MagicMock()
    col.find_one = AsyncMock(return_value={"_id": "2026-09-28", "entries": []})
    col.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    return col


class TestRememberRepository:
    """`remember` コレクションの読み書き。"""

    def test_uses_remember_collection(self) -> None:
        """コレクション名は `remember`。"""
        client = MagicMock()
        with patch(
            "lilla_core.extensions.memory.repository.create_mongo_client", return_value=client
        ):
            RememberRepository("mongodb://x", "lilla")
        client.__getitem__.return_value.__getitem__.assert_called_once_with("remember")

    async def test_get(self, collection: MagicMock) -> None:
        """日付を `_id` にして 1 件取る。"""
        await _make_repo(collection).get("2026-09-28")
        collection.find_one.assert_awaited_once_with({"_id": "2026-09-28"})

    async def test_append_if_absent(self, collection: MagicMock) -> None:
        """ドキュメントを `$setOnInsert` で用意し、同じ本文が無いときだけ `$push` する。"""
        assert await _make_repo(collection).append_if_absent("2026-09-28", "a") is True

        first, second = collection.update_one.await_args_list
        assert first.args[0] == {"_id": "2026-09-28"}
        assert set(first.args[1]) == {"$setOnInsert"}
        assert first.kwargs == {"upsert": True}
        assert second.args[0] == {"_id": "2026-09-28", "entries.content": {"$ne": "a"}}
        assert second.args[1]["$push"]["entries"]["content"] == "a"

    async def test_duplicate_is_not_appended(self, collection: MagicMock) -> None:
        """同じ本文があって更新されなければ False。"""
        collection.update_one.return_value = MagicMock(modified_count=0)
        assert await _make_repo(collection).append_if_absent("2026-09-28", "a") is False
