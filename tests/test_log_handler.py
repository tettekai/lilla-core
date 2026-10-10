"""log_handler.py のテスト。"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pymongo.errors import ServerSelectionTimeoutError

from lilla_core.log_handler import (
    MAX_EXCEPTION_TEXT_CHARS,
    SERVER_SELECTION_TIMEOUT_MS,
    MongoDBHandler,
)

# MongoClient は lilla_core.log_handler の名前空間で差し替える（実 Mongo へは接続しない）
_mock_collection = MagicMock()
_mock_db = MagicMock()
_mock_db.__getitem__ = MagicMock(return_value=_mock_collection)
_mock_client_instance = MagicMock()
_mock_client_instance.__getitem__ = MagicMock(return_value=_mock_db)


TTL_HOURS = {"debug": 72, "info": 240, "warning": 720, "error": 720}


def _make_handler(ttl_hours=None):
    """テスト用の MongoDBHandler を生成する。"""
    _mock_collection.reset_mock()
    with patch("lilla_core.log_handler.MongoClient", return_value=_mock_client_instance):
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
        with patch("lilla_core.log_handler.MongoClient", return_value=_mock_client_instance):
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
        with patch("lilla_core.log_handler.MongoClient", return_value=_mock_client_instance):
            handler = MongoDBHandler(
                uri="mongodb://localhost:27017",
                db_name="testdb",
                ttl_hours=None,
            )
        assert handler._ttl_hours == {"debug": 72, "info": 240, "warning": 720, "error": 720}

    def test_uses_short_server_selection_timeout(self):
        """既定の約 30 秒ではなく短い serverSelectionTimeoutMS を明示すること。"""
        _mock_collection.reset_mock()
        with patch(
            "lilla_core.log_handler.MongoClient", return_value=_mock_client_instance
        ) as mock_client_cls:
            MongoDBHandler(uri="mongodb://localhost:27017", db_name="testdb")
        assert mock_client_cls.call_args[1]["serverSelectionTimeoutMS"] == SERVER_SELECTION_TIMEOUT_MS
        assert SERVER_SELECTION_TIMEOUT_MS <= 5000

    def test_unreachable_mongo_raises_and_closes_client(self):
        """起動時に MongoDB へ届かなければ RuntimeError で落ち、クライアントを閉じること。"""
        collection = MagicMock()
        collection.create_index.side_effect = ServerSelectionTimeoutError("localhost:27017: refused")
        db = MagicMock()
        db.__getitem__ = MagicMock(return_value=collection)
        client = MagicMock()
        client.__getitem__ = MagicMock(return_value=db)
        with patch("lilla_core.log_handler.MongoClient", return_value=client):
            with pytest.raises(RuntimeError, match="could not reach MongoDB") as exc_info:
                MongoDBHandler(uri="mongodb://user:secret@localhost:27017", db_name="testdb")
        assert isinstance(exc_info.value.__cause__, ServerSelectionTimeoutError)
        assert "secret" not in str(exc_info.value)
        client.close.assert_called_once()


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


class TestMongoDBHandlerException:
    """例外付きレコードの `exception` フィールドのテスト。"""

    @staticmethod
    def _record_with_exception(msg: str, exc: Exception) -> logging.LogRecord:
        """exc を送出・捕捉して exc_info 付きのレコードを作る。"""
        try:
            raise exc
        except Exception:
            exc_info = sys.exc_info()
        return logging.LogRecord(
            name="myapp", level=logging.ERROR, pathname="", lineno=0,
            msg=msg, args=(), exc_info=exc_info,
        )

    def test_exc_info_is_saved_as_exception_field(self):
        """exc_info 付きのレコードは message を変えず、traceback を exception に保存すること。"""
        handler = _make_handler()
        record = self._record_with_exception("タスク作成中にエラー", ValueError("status=400 body=bad"))
        handler.emit(record)
        doc = _mock_collection.insert_one.call_args[0][0]
        assert doc["message"] == "タスク作成中にエラー"
        assert "Traceback (most recent call last)" in doc["exception"]
        assert "ValueError: status=400 body=bad" in doc["exception"]

    def test_preformatted_exc_text_is_used(self):
        """exc_info が無くても exc_text があればそれを保存すること（QueueHandler 経由の形）。"""
        handler = _make_handler()
        record = logging.LogRecord(
            name="myapp", level=logging.ERROR, pathname="", lineno=0,
            msg="failed", args=(), exc_info=None,
        )
        record.exc_text = "Traceback...\nRuntimeError: boom"
        handler.emit(record)
        doc = _mock_collection.insert_one.call_args[0][0]
        assert doc["exception"] == "Traceback...\nRuntimeError: boom"

    def test_long_exception_text_keeps_tail(self):
        """上限を超える例外テキストは先頭を切り詰め、例外の種類とメッセージが載る末尾を残すこと。"""
        handler = _make_handler()
        record = logging.LogRecord(
            name="myapp", level=logging.ERROR, pathname="", lineno=0,
            msg="failed", args=(), exc_info=None,
        )
        record.exc_text = "x" * (MAX_EXCEPTION_TEXT_CHARS * 2) + "\nKeyError: 'id'"
        handler.emit(record)
        doc = _mock_collection.insert_one.call_args[0][0]
        assert doc["exception"].startswith("...[truncated ")
        assert doc["exception"].endswith("KeyError: 'id'")
        assert len(doc["exception"]) < MAX_EXCEPTION_TEXT_CHARS + 50

    def test_no_exception_keeps_document_shape(self):
        """例外なしのレコードは従来どおり exception フィールドを持たないこと。"""
        handler = _make_handler()
        record = logging.LogRecord(
            name="myapp", level=logging.ERROR, pathname="", lineno=0,
            msg="HTTP error 500 POST https://example.com", args=(), exc_info=None,
        )
        handler.emit(record)
        doc = _mock_collection.insert_one.call_args[0][0]
        assert set(doc) == {"asctime", "levelname", "message", "created_at", "expires_at"}
        assert doc["message"] == "HTTP error 500 POST https://example.com"


class TestMongoDBHandlerLoopGuard:
    """pymongo ログによる再帰呼び出し防止のテスト。"""

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

    def test_other_logger_is_not_ignored(self):
        """pymongo 以外のロガーは無視されないこと。"""
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


class TestMongoDBHandlerEmitFailure:
    """実行中の insert_one 失敗のテスト。"""

    def test_insert_failure_is_handled_without_raising(self):
        """insert_one が例外を投げても emit は例外を伝播させず handleError に任せること。"""
        handler = _make_handler()
        _mock_collection.insert_one.side_effect = ServerSelectionTimeoutError("down")
        record = logging.LogRecord(
            name="myapp", level=logging.INFO, pathname="", lineno=0,
            msg="app log", args=(), exc_info=None,
        )
        try:
            with patch.object(handler, "handleError") as mock_handle_error:
                handler.emit(record)
            mock_handle_error.assert_called_once_with(record)
        finally:
            _mock_collection.insert_one.side_effect = None
