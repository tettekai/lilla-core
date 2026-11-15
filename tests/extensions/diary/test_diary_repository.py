"""lilla_core/extensions/diary/repository.py のテスト。

日記ドラフトのリポジトリは `src/repository` 配下ではないため、CLAUDE.md のルールに従い
テストクラスにまとめる。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lilla_core.extensions.diary.repository import DiaryDraftRepository


def _make_repo(collection: MagicMock) -> DiaryDraftRepository:
    """MongoDB へ接続せずにコレクションだけ差し替えたリポジトリを返す。"""
    repo = DiaryDraftRepository.__new__(DiaryDraftRepository)
    repo._collection = collection
    return repo


@pytest.fixture
def collection() -> MagicMock:
    """`find_one` / `update_one` を持つコレクションのモック。"""
    col = MagicMock()
    col.find_one = AsyncMock(return_value={"_id": "2026-09-28", "content": "c"})
    col.update_one = AsyncMock()
    return col


class TestDiaryDraftRepository:
    """`diary_drafts` コレクションの読み書き。"""

    def test_uses_diary_drafts_collection(self) -> None:
        """コレクション名は従来どおり `diary_drafts`（データ移行なし）。"""
        client = MagicMock()
        with patch("lilla_core.extensions.diary.repository.create_mongo_client", return_value=client):
            DiaryDraftRepository("mongodb://x", "lilla")
        client.__getitem__.assert_called_once_with("lilla")
        client.__getitem__.return_value.__getitem__.assert_called_once_with("diary_drafts")

    async def test_get(self, collection: MagicMock) -> None:
        """日付を `_id` にして 1 件取る。"""
        doc = await _make_repo(collection).get("2026-09-28")

        assert doc["content"] == "c"
        collection.find_one.assert_awaited_once_with({"_id": "2026-09-28"})

    async def test_create_if_absent_uses_set_on_insert(self, collection: MagicMock) -> None:
        """既存のドラフトを上書きしないよう `$setOnInsert` の upsert で作る。"""
        await _make_repo(collection).create_if_absent("2026-09-28", "new")

        query, update = collection.update_one.await_args.args
        assert query == {"_id": "2026-09-28"}
        assert set(update) == {"$setOnInsert"}
        assert update["$setOnInsert"]["content"] == "new"
        assert collection.update_one.await_args.kwargs == {"upsert": True}

    async def test_set_content_replaces_body(self, collection: MagicMock) -> None:
        """本文は `$set` で丸ごと置き換え、作成時刻は新規作成時だけ入れる。"""
        await _make_repo(collection).set_content("2026-09-28", "body")

        _, update = collection.update_one.await_args.args
        assert update["$set"]["content"] == "body"
        assert "created_at" in update["$setOnInsert"]
