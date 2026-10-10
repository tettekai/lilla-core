"""logging_setup.setup_logging() のテスト。

`logging.config.dictConfig` をモックし、渡される dict の中身（mongodb ハンドラの
注入有無・root / loggers からの参照除去）を検証する。
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from pymongo.errors import ServerSelectionTimeoutError

from lilla_core.core import logging_setup
from lilla_core.core.logging_setup import setup_logging, stop_logging_listener
from lilla_core.log_handler import MongoDBHandler


def _make_config(config_root: Path) -> MagicMock:
    """setup_logging が参照する AppConfig 相当の MagicMock を作る。"""
    config = MagicMock()
    config.env.config_root = config_root
    config.env.mongodb_uri = "mongodb://localhost:27017"
    config.mongodb.db_name = "lilla"
    config.bot.log_ttl_hours = {"debug": 72, "info": 240, "warning": 720, "error": 720}
    return config


def _write_logging_yaml(config_root: Path, data: dict) -> None:
    (config_root / "logging.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


class TestMongodbHandlerPresent:
    """mongodb ハンドラが定義されているとき、従来どおり接続情報を注入する。"""

    def test_injects_connection_info_and_keeps_references(self, tmp_path: Path) -> None:
        _write_logging_yaml(
            tmp_path,
            {
                "version": 1,
                "handlers": {
                    "console": {"class": "logging.StreamHandler"},
                    "mongodb": {"()": "lilla_core.log_handler.MongoDBHandler"},
                },
                "root": {"level": "DEBUG", "handlers": ["console", "mongodb"]},
                "loggers": {"pymongo": {"level": "WARNING", "handlers": ["mongodb"]}},
            },
        )
        config = _make_config(tmp_path)

        with patch("lilla_core.core.logging_setup.get_config", return_value=config), patch(
            "logging.config.dictConfig"
        ) as mock_dict_config:
            setup_logging()

        passed_config = mock_dict_config.call_args[0][0]
        mongodb_handler = passed_config["handlers"]["mongodb"]
        assert mongodb_handler["uri"] == "mongodb://localhost:27017"
        assert mongodb_handler["db_name"] == "lilla"
        assert mongodb_handler["ttl_hours"] == {
            "debug": 72,
            "info": 240,
            "warning": 720,
            "error": 720,
        }
        assert passed_config["root"]["handlers"] == ["console", "mongodb"]
        assert passed_config["loggers"]["pymongo"]["handlers"] == ["mongodb"]


class TestMongodbHandlerAbsent:
    """mongodb ハンドラが無いとき、注入せず参照も除去する。"""

    def test_no_injection_and_references_removed(self, tmp_path: Path) -> None:
        _write_logging_yaml(
            tmp_path,
            {
                "version": 1,
                "handlers": {"console": {"class": "logging.StreamHandler"}},
                "root": {"level": "DEBUG", "handlers": ["console", "mongodb"]},
                "loggers": {"pymongo": {"level": "WARNING", "handlers": ["console", "mongodb"]}},
            },
        )
        config = _make_config(tmp_path)

        with patch("lilla_core.core.logging_setup.get_config", return_value=config), patch(
            "logging.config.dictConfig"
        ) as mock_dict_config:
            setup_logging()

        passed_config = mock_dict_config.call_args[0][0]
        assert "mongodb" not in passed_config["handlers"]
        assert passed_config["root"]["handlers"] == ["console"]
        assert passed_config["loggers"]["pymongo"]["handlers"] == ["console"]

    def test_no_handlers_section_does_not_crash(self, tmp_path: Path) -> None:
        """`handlers` 自体が無いときは注入も除去もせずそのまま渡す。"""
        _write_logging_yaml(
            tmp_path,
            {"version": 1, "root": {"level": "DEBUG", "handlers": ["console"]}},
        )
        config = _make_config(tmp_path)

        with patch("lilla_core.core.logging_setup.get_config", return_value=config), patch(
            "logging.config.dictConfig"
        ) as mock_dict_config:
            setup_logging()

        passed_config = mock_dict_config.call_args[0][0]
        assert passed_config["root"]["handlers"] == ["console"]


class TestNoLoggingYaml:
    """logging.yaml が存在しないときは basicConfig にフォールバックする（既存挙動）。"""

    def test_falls_back_to_basic_config(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)

        with patch("lilla_core.core.logging_setup.get_config", return_value=config), patch(
            "logging.basicConfig"
        ) as mock_basic_config, patch("logging.config.dictConfig") as mock_dict_config:
            setup_logging()

        mock_basic_config.assert_called_once()
        mock_dict_config.assert_not_called()


@pytest.fixture
def restore_logging():
    """実際に dictConfig を走らせるテストのあと、ルートロガーと listener を元へ戻す。"""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    yield
    stop_logging_listener()
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in saved_handlers:
        root.addHandler(h)
    root.setLevel(saved_level)


def _mongo_client_mock(collection: MagicMock) -> MagicMock:
    """client[db][collection] が collection を返す MongoClient 相当のモックを作る。"""
    db = MagicMock()
    db.__getitem__ = MagicMock(return_value=collection)
    client = MagicMock()
    client.__getitem__ = MagicMock(return_value=db)
    return client


def _write_mongodb_yaml(config_root: Path) -> None:
    """mongodb ハンドラをルートに付けた logging.yaml を書く。"""
    _write_logging_yaml(
        config_root,
        {
            "version": 1,
            "disable_existing_loggers": False,
            "handlers": {
                "mongodb": {"()": "lilla_core.log_handler.MongoDBHandler", "level": "INFO"},
            },
            "root": {"level": "DEBUG", "handlers": ["mongodb"]},
        },
    )


class TestMongodbHandlerViaQueue:
    """mongodb ハンドラは QueueHandler + QueueListener 経由で書き込む。"""

    def test_root_gets_queue_handler_and_insert_runs_off_caller_thread(
        self, tmp_path: Path, restore_logging
    ) -> None:
        """logger.info は insert_one の完了を待たずに戻り、書き込みは別スレッドで行われる。"""
        _write_mongodb_yaml(tmp_path)
        config = _make_config(tmp_path)
        release = threading.Event()
        inserted = threading.Event()
        insert_threads: list[threading.Thread] = []

        def slow_insert(doc):
            insert_threads.append(threading.current_thread())
            release.wait(5)
            inserted.set()

        collection = MagicMock()
        collection.insert_one.side_effect = slow_insert

        with patch("lilla_core.core.logging_setup.get_config", return_value=config), patch(
            "lilla_core.log_handler.MongoClient", return_value=_mongo_client_mock(collection)
        ):
            setup_logging()

        root = logging.getLogger()
        assert not any(isinstance(h, MongoDBHandler) for h in root.handlers)
        assert any(isinstance(h, logging.handlers.QueueHandler) for h in root.handlers)
        assert logging_setup._mongodb_listener is not None

        started = time.monotonic()
        logging.getLogger("lilla_test.queue").info("hello %s", "world")
        assert time.monotonic() - started < 1.0
        assert not inserted.is_set()

        release.set()
        stop_logging_listener()  # 残りを書き切ってから止まる

        assert inserted.is_set()
        assert insert_threads and insert_threads[0] is not threading.current_thread()
        doc = collection.insert_one.call_args[0][0]
        assert doc["message"] == "hello world"
        assert doc["levelname"] == "INFO"
        assert set(doc) == {"asctime", "levelname", "message", "created_at", "expires_at"}

    def test_logger_exception_keeps_traceback(self, tmp_path: Path, restore_logging) -> None:
        """logger.exception の traceback が Queue 経由でも exception フィールドに残る。"""
        _write_mongodb_yaml(tmp_path)
        config = _make_config(tmp_path)
        collection = MagicMock()

        with patch("lilla_core.core.logging_setup.get_config", return_value=config), patch(
            "lilla_core.log_handler.MongoClient", return_value=_mongo_client_mock(collection)
        ):
            setup_logging()

        try:
            raise ValueError("status=400 body=invalid due")
        except ValueError:
            logging.getLogger("lilla_test.queue").exception("Failed to create %s", "task")
        stop_logging_listener()

        doc = collection.insert_one.call_args[0][0]
        assert doc["message"] == "Failed to create task"
        assert doc["levelname"] == "ERROR"
        assert "Traceback (most recent call last)" in doc["exception"]
        assert "ValueError: status=400 body=invalid due" in doc["exception"]

    def test_queue_handler_prepare_formats_exc_info(self) -> None:
        """prepare は traceback を exc_text に整形し、exc_info は持ち越さない。"""
        handler = logging_setup._MongoDBQueueHandler(MagicMock())
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            record = logging.getLogger("lilla_test").makeRecord(
                "lilla_test", logging.ERROR, "", 0, "oops %s", ("x",), sys.exc_info()
            )
        prepared = handler.prepare(record)
        assert prepared.exc_info is None
        assert prepared.message == "oops x"
        assert "RuntimeError: boom" in prepared.exc_text
        assert record.exc_info is not None  # 元レコードは書き換えない

    def test_level_below_handler_is_not_written(self, tmp_path: Path, restore_logging) -> None:
        """mongodb ハンドラのレベル未満のレコードは書き込まれない。"""
        _write_mongodb_yaml(tmp_path)
        config = _make_config(tmp_path)
        collection = MagicMock()

        with patch("lilla_core.core.logging_setup.get_config", return_value=config), patch(
            "lilla_core.log_handler.MongoClient", return_value=_mongo_client_mock(collection)
        ):
            setup_logging()

        logging.getLogger("lilla_test.queue").debug("debug only")
        stop_logging_listener()
        collection.insert_one.assert_not_called()

    def test_insert_failure_does_not_stop_listener(self, tmp_path: Path, restore_logging) -> None:
        """実行中の insert_one 失敗で listener が止まらず、後続のレコードも書き込まれる。"""
        _write_mongodb_yaml(tmp_path)
        config = _make_config(tmp_path)
        collection = MagicMock()
        collection.insert_one.side_effect = [ServerSelectionTimeoutError("down"), None]

        with patch("lilla_core.core.logging_setup.get_config", return_value=config), patch(
            "lilla_core.log_handler.MongoClient", return_value=_mongo_client_mock(collection)
        ), patch.object(MongoDBHandler, "handleError") as mock_handle_error:
            setup_logging()
            logger = logging.getLogger("lilla_test.queue")
            logger.info("first")
            logger.info("second")
            stop_logging_listener()

        assert collection.insert_one.call_count == 2
        mock_handle_error.assert_called_once()


class TestMongodbStartupFailFast:
    """mongodb ハンドラがあるのに MongoDB へ届かなければ起動を失敗させる。"""

    def test_unreachable_mongo_raises(self, tmp_path: Path, restore_logging) -> None:
        _write_mongodb_yaml(tmp_path)
        config = _make_config(tmp_path)
        collection = MagicMock()
        collection.create_index.side_effect = ServerSelectionTimeoutError("refused")

        with patch("lilla_core.core.logging_setup.get_config", return_value=config), patch(
            "lilla_core.log_handler.MongoClient", return_value=_mongo_client_mock(collection)
        ):
            with pytest.raises(ValueError) as exc_info:
                setup_logging()

        cause = exc_info.value.__cause__
        assert isinstance(cause, RuntimeError)
        assert "could not reach MongoDB" in str(cause)
        assert logging_setup._mongodb_listener is None

    def test_no_mongodb_handler_does_not_connect(self, tmp_path: Path, restore_logging) -> None:
        """mongodb ハンドラが無いときは MongoClient を作らない。"""
        _write_logging_yaml(
            tmp_path,
            {
                "version": 1,
                "disable_existing_loggers": False,
                "handlers": {"console": {"class": "logging.StreamHandler"}},
                "root": {"level": "INFO", "handlers": ["console", "mongodb"]},
            },
        )
        config = _make_config(tmp_path)

        with patch("lilla_core.core.logging_setup.get_config", return_value=config), patch(
            "lilla_core.log_handler.MongoClient"
        ) as mock_client_cls:
            setup_logging()

        mock_client_cls.assert_not_called()
        assert logging_setup._mongodb_listener is None
