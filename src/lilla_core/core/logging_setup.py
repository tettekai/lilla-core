from __future__ import annotations

import logging
import logging.config
import sys

import yaml

from lilla_core.core.config import get_config


def setup_logging() -> None:
    """config/logging.yaml を読み込んでロギングを初期化する。

    MongoDB ハンドラへの接続情報は AppConfig から取得して注入する。
    YAML ファイルが存在しない場合は basicConfig にフォールバックする。
    """
    config = get_config()
    log_config_path = config.env.config_root / "logging.yaml"

    if not log_config_path.exists():
        logging.basicConfig(
            level=logging.INFO,
            stream=sys.stdout,
            format="%(asctime)s - %(levelname)s - %(message)s",
        )
        return

    with open(log_config_path, "r", encoding="utf-8") as f:
        log_config = yaml.safe_load(f)

    handlers = log_config.get("handlers")
    if isinstance(handlers, dict) and "mongodb" in handlers:
        # AppConfig から MongoDB 接続情報を注入
        handlers["mongodb"]["uri"] = config.env.mongodb_uri
        handlers["mongodb"]["db_name"] = config.mongodb.db_name
        handlers["mongodb"]["ttl_hours"] = dict(config.bot.log_ttl_hours)
    elif isinstance(handlers, dict):
        # mongodb ハンドラが定義されていない場合は注入せず、
        # root / 各 logger からの参照も外す（残すと dictConfig が KeyError で落ちる）。
        root = log_config.get("root")
        if isinstance(root, dict) and isinstance(root.get("handlers"), list):
            root["handlers"] = [h for h in root["handlers"] if h != "mongodb"]

        loggers = log_config.get("loggers")
        if isinstance(loggers, dict):
            for logger_config in loggers.values():
                if isinstance(logger_config, dict) and isinstance(logger_config.get("handlers"), list):
                    logger_config["handlers"] = [
                        h for h in logger_config["handlers"] if h != "mongodb"
                    ]

    logging.config.dictConfig(log_config)
