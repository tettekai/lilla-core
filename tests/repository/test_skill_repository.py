"""src/lilla_core/repository/skill_repository.py のテスト。

CLAUDE.md のルールに従い、src/repository 配下のソースのテストはテストクラスを作らず
関数として記述する。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from pymongo import ASCENDING

from lilla_core.repository.skill_repository import _PROJECTION, SkillRepository


async def test_get_enabled_queries_enabled_skills_sorted_by_name() -> None:
    """`get_enabled` は `enabled` が False でないものだけを名前の昇順で取得する。"""
    docs = [{"name": "alpha"}, {"name": "beta"}]
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=docs)
    collection = MagicMock()
    collection.find = MagicMock(return_value=cursor)
    client = MagicMock()
    client.__getitem__.return_value.__getitem__.return_value = collection

    with patch(
        "lilla_core.repository.skill_repository.create_mongo_client", return_value=client
    ):
        repo = SkillRepository("mongodb://localhost:27017/test", "testdb")

    assert await repo.get_enabled() == docs
    collection.find.assert_called_once_with({"enabled": {"$ne": False}}, _PROJECTION)
    cursor.sort.assert_called_once_with("name", ASCENDING)
    cursor.to_list.assert_awaited_once_with(length=None)


async def test_get_llm_callable_queries_enabled_and_llm_callable_skills() -> None:
    """`get_llm_callable` は有効かつ `llm_callable` が True のものだけを名前の昇順で取得する。"""
    docs = [{"name": "alpha"}]
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=docs)
    collection = MagicMock()
    collection.find = MagicMock(return_value=cursor)
    client = MagicMock()
    client.__getitem__.return_value.__getitem__.return_value = collection

    with patch(
        "lilla_core.repository.skill_repository.create_mongo_client", return_value=client
    ):
        repo = SkillRepository("mongodb://localhost:27017/test", "testdb")

    assert await repo.get_llm_callable() == docs
    collection.find.assert_called_once_with(
        {"enabled": {"$ne": False}, "llm_callable": True}, _PROJECTION
    )
    cursor.sort.assert_called_once_with("name", ASCENDING)


async def test_add_stores_llm_callable() -> None:
    """`add` は `llm_callable` を保存し、省略時は False（手動専用）にする。"""
    collection = MagicMock()
    collection.insert_one = AsyncMock(return_value=MagicMock(inserted_id="id"))
    client = MagicMock()
    client.__getitem__.return_value.__getitem__.return_value = collection

    with patch(
        "lilla_core.repository.skill_repository.create_mongo_client", return_value=client
    ):
        repo = SkillRepository("mongodb://localhost:27017/test", "testdb")

    default_doc = await repo.add("a", "", "p")
    callable_doc = await repo.add("b", "", "p", llm_callable=True)

    assert default_doc["llm_callable"] is False
    assert callable_doc["llm_callable"] is True
    assert "llm_callable" in _PROJECTION
