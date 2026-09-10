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

    # AppConfig から MongoDB 接続情報を注入
    log_config["handlers"]["mongodb"]["uri"] = config.env.mongodb_uri
    log_config["handlers"]["mongodb"]["db_name"] = config.mongodb.db_name
    log_config["handlers"]["mongodb"]["ttl_hours"] = dict(config.bot.log_ttl_hours)

    logging.config.dictConfig(log_config)
