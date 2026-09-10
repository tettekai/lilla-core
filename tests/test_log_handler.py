"""log_handler.py のテスト。"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# pymongo をモックしてから import する
_mock_collection = MagicMock()
_mock_db = MagicMock()
_mock_db.__getitem__ = MagicMock(return_value=_mock_collection)
_mock_client_instance = MagicMock()
_mock_client_instance.__getitem__ = MagicMock(return_value=_mock_db)

with patch("pymongo.MongoClient", return_value=_mock_client_instance):
    from lilla_core.log_handler import MongoDBHandler


TTL_HOURS = {"debug": 72, "info": 240, "warning": 720, "error": 720}


def _make_handler(ttl_hours=None):
    """テスト用の MongoDBHandler を生成する。"""
    _mock_collection.reset_mock()
    with patch("pymongo.MongoClient", return_value=_mock_client_instance):
        return MongoDBHandler(
            uri="mongodb://localhost:27017",
            db_name="testdb",
            ttl_hours=ttl_hours or TTL_HOURS,
        )


class TestMongoDBHandlerInit:
    """MongoDBHandler の初期化テスト。"""

    def test_creates_expires_at_ttl_index(self):
        """expires_at フィールドに TTL インデックスが作成されること。"""
        _mock_collection.reset_mock()
        with patch("pymongo.MongoClient", return_value=_mock_client_instance):
            MongoDBHandler(
                uri="mongodb://localhost:27017",
                db_name="testdb",
                ttl_hours=TTL_HOURS,
            )
        _mock_collection.create_index.assert_called_once()
        call_args = _mock_collection.create_index.call_args
        index_fields = call_args[0][0]
        assert index_fields == [("expires_at", 1)]
        assert call_args[1].get("expireAfterSeconds") == 0

    def test_default_ttl_hours_when_none(self):
        """ttl_hours=None の場合はデフォルト値が使われること。"""
        _mock_collection.reset_mock()
        with patch("pymongo.MongoClient", return_value=_mock_client_instance):
            handler = MongoDBHandler(
                uri="mongodb://localhost:27017",
                db_name="testdb",
                ttl_hours=None,
            )
        assert handler._ttl_hours == {"debug": 72, "info": 240, "warning": 720, "error": 720}


class TestMongoDBHandlerEmit:
    """MongoDBHandler.emit() のテスト。"""

    def _get_inserted_doc(self, handler, levelname: str, msg: str) -> dict:
        """指定レベルのログを emit して挿入されたドキュメントを返す。"""
        _mock_collection.reset_mock()
        record = logging.LogRecord(
            name="test",
            level=getattr(logging, levelname),
            pathname="",
            lineno=0,
            msg=msg,
            args=(),
            exc_info=None,
        )
        record.levelname = levelname
        before = datetime.now(timezone.utc)
        handler.emit(record)
        after = datetime.now(timezone.utc)
        assert _mock_collection.insert_one.called
        doc = _mock_collection.insert_one.call_args[0][0]
        return doc, before, after

    def test_debug_expires_at_72h(self):
        """DEBUG ログの expires_at が created_at + 72h の近似値になること。"""
        handler = _make_handler()
        doc, before, after = self._get_inserted_doc(handler, "DEBUG", "debug message")
        delta = doc["expires_at"] - doc["created_at"]
        assert abs(delta.total_seconds() - 72 * 3600) < 5

    def test_info_expires_at_240h(self):
        """INFO ログの expires_at が created_at + 240h の近似値になること。"""
        handler = _make_handler()
        doc, before, after = self._get_inserted_doc(handler, "INFO", "info message")
        delta = doc["expires_at"] - doc["created_at"]
        assert abs(delta.total_seconds() - 240 * 3600) < 5

    def test_warning_expires_at_720h(self):
        """WARNING ログの expires_at が created_at + 720h の近似値になること。"""
        handler = _make_handler()
        doc, before, after = self._get_inserted_doc(handler, "WARNING", "warning message")
        delta = doc["expires_at"] - doc["created_at"]
        assert abs(delta.total_seconds() - 720 * 3600) < 5

    def test_error_expires_at_720h(self):
        """ERROR ログの expires_at が created_at + 720h の近似値になること。"""
        handler = _make_handler()
        doc, before, after = self._get_inserted_doc(handler, "ERROR", "error message")
        delta = doc["expires_at"] - doc["created_at"]
        assert abs(delta.total_seconds() - 720 * 3600) < 5

    def test_unknown_level_falls_back_to_warning(self):
        """未定義レベル（CRITICAL）の場合、warning の値にフォールバックすること。"""
        handler = _make_handler()
        _mock_collection.reset_mock()
        record = logging.LogRecord(
            name="test",
            level=logging.CRITICAL,
            pathname="",
            lineno=0,
            msg="critical message",
            args=(),
            exc_info=None,
        )
        record.levelname = "CRITICAL"
        handler.emit(record)
        assert _mock_collection.insert_one.called
        doc = _mock_collection.insert_one.call_args[0][0]
        delta = doc["expires_at"] - doc["created_at"]
        assert abs(delta.total_seconds() - 720 * 3600) < 5

    def test_doc_has_required_fields(self):
        """ドキュメントに必要なフィールドが含まれること。"""
        handler = _make_handler()
        doc, _, _ = self._get_inserted_doc(handler, "INFO", "test message")
        assert "asctime" in doc
        assert "levelname" in doc
        assert "message" in doc
        assert "created_at" in doc
        assert "expires_at" in doc
        assert doc["levelname"] == "INFO"
        assert doc["message"] == "test message"


class TestMongoDBHandlerLoopGuard:
    """pymongo / motor ログによる再帰呼び出し防止のテスト。"""

    def test_pymongo_log_is_ignored(self):
        """pymongo から始まるロガーのレコードは無視されること。"""
        handler = _make_handler()
        _mock_collection.reset_mock()
        record = logging.LogRecord(
            name="pymongo.connection",
            level=logging.DEBUG,
            pathname="",
            lineno=0,
            msg="pymongo internal",
            args=(),
            exc_info=None,
        )
        handler.emit(record)
        _mock_collection.insert_one.assert_not_called()

    def test_motor_log_is_ignored(self):
        """motor から始まるロガーのレコードは無視されること。"""
        handler = _make_handler()
        _mock_collection.reset_mock()
        record = logging.LogRecord(
            name="motor.core",
            level=logging.DEBUG,
            pathname="",
            lineno=0,
            msg="motor internal",
            args=(),
            exc_info=None,
        )
        handler.emit(record)
        _mock_collection.insert_one.assert_not_called()

    def test_other_logger_is_not_ignored(self):
        """pymongo / motor 以外のロガーは無視されないこと。"""
        handler = _make_handler()
        _mock_collection.reset_mock()
        record = logging.LogRecord(
            name="myapp.service",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="app log",
            args=(),
            exc_info=None,
        )
        handler.emit(record)
        _mock_collection.insert_one.assert_called_once()
