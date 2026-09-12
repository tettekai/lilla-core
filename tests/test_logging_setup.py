"""logging_setup.setup_logging() のテスト。

`logging.config.dictConfig` をモックし、渡される dict の中身（mongodb ハンドラの
注入有無・root / loggers からの参照除去）を検証する。
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from lilla_core.core.logging_setup import setup_logging


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
