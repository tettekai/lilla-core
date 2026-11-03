"""tool_support/mongodata.py のテスト。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from lilla_core.tool_support.mongodata import (
    CollectionNotAllowedError,
    KeyFieldMissingError,
    MongoDataError,
    save_document,
)


@pytest.fixture
def col(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """get_config と create_mongo_client を差し替え、書き込み先のコレクションのモックを返す。"""
    from lilla_core.core import config as core_config
    from lilla_core.repository import mongo_client as mongo_client_module

    config = MagicMock()
    config.commands.mongodata.allowed_collections = ["asken_daily"]
    config.env.mongodb_uri = "mongodb://localhost:27017"
    config.mongodb.db_name = "lilla"
    monkeypatch.setattr(core_config, "get_config", lambda: config)

    collection = MagicMock()
    collection.update_one = AsyncMock()
    collection.insert_one = AsyncMock()
    db = MagicMock()
    db.__getitem__.side_effect = lambda name: collection if name == "asken_daily" else MagicMock()
    client = MagicMock()
    client.__getitem__.side_effect = lambda name: db if name == "lilla" else MagicMock()
    monkeypatch.setattr(mongo_client_module, "create_mongo_client", lambda *a, **kw: client)
    return collection


class TestSaveDocument:
    """save_document の書き込みと検証。"""

    async def test_upsert_with_key(self, col: MagicMock) -> None:
        """key があれば {key: 値} を条件に $set で upsert する。"""
        doc = {"date": "2026-11-02", "summary": {"calories": 1300}}
        await save_document("asken_daily", doc, key="date")
        col.update_one.assert_awaited_once_with(
            {"date": "2026-11-02"}, {"$set": doc}, upsert=True
        )
        col.insert_one.assert_not_awaited()

    async def test_insert_without_key(self, col: MagicMock) -> None:
        """key が無ければ insert する。"""
        await save_document("asken_daily", {"a": 1})
        col.insert_one.assert_awaited_once_with({"a": 1})
        col.update_one.assert_not_awaited()

    async def test_strips_id_without_mutating_input(self, col: MagicMock) -> None:
        """_id を取り除いて書き、呼び出し元の dict は変えない。"""
        doc = {"_id": "x", "date": "2026-11-02"}
        await save_document("asken_daily", doc, key="date")
        assert col.update_one.await_args.args[1] == {"$set": {"date": "2026-11-02"}}
        assert doc == {"_id": "x", "date": "2026-11-02"}

    @pytest.mark.parametrize("name", ["other", "", "  "])
    async def test_rejects_disallowed_collection(self, col: MagicMock, name: str) -> None:
        """allowed_collections に無い・空のコレクション名は書かずに例外にする。"""
        with pytest.raises(CollectionNotAllowedError):
            await save_document(name, {"date": "2026-11-02"}, key="date")
        col.update_one.assert_not_awaited()

    async def test_strips_collection_name(self, col: MagicMock) -> None:
        """コレクション名の前後の空白は除いて扱う。"""
        await save_document(" asken_daily ", {"a": 1})
        col.insert_one.assert_awaited_once()

    async def test_key_missing(self, col: MagicMock) -> None:
        """key のフィールドが無ければ書かずに例外にする（MongoDataError の派生）。"""
        with pytest.raises(KeyFieldMissingError) as exc:
            await save_document("asken_daily", {"a": 1}, key="date")
        assert exc.value.key == "date"
        assert isinstance(exc.value, MongoDataError)
        col.update_one.assert_not_awaited()

    async def test_payload_must_be_dict(self, col: MagicMock) -> None:
        """payload が dict でなければ TypeError にする。"""
        with pytest.raises(TypeError):
            await save_document("asken_daily", ["x"])  # type: ignore[arg-type]

    async def test_db_error_propagates(self, col: MagicMock) -> None:
        """MongoDB の例外はそのまま伝える。"""
        col.insert_one.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError):
            await save_document("asken_daily", {"a": 1})
