from __future__ import annotations

import atexit
import copy
import logging
import logging.config
import logging.handlers
import queue
import sys

import yaml

from lilla_core.core.config import get_config

# logging.yaml で MongoDB ハンドラに使う名前（接続情報の注入先でもある）
MONGODB_HANDLER_NAME = "mongodb"

# 起動中の MongoDB 書き込み用 QueueListener（mongodb ハンドラが無ければ None）
_mongodb_listener: logging.handlers.QueueListener | None = None
_atexit_registered = False


class _MongoDBQueueHandler(logging.handlers.QueueHandler):
    """MongoDB 書き込みを別スレッドへ渡すための QueueHandler。

    標準の `prepare()` は `format()` の結果（例外のトレースバックを含む）で
    `msg` を置き換えるため、ログ文書の `message` が従来の `getMessage()` と
    変わってしまう。ここでは `getMessage()` の結果だけを載せ、ドキュメント形を保つ。
    """

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        """Queue へ積むためにレコードを複製し、メッセージを確定させる。"""
        record = copy.copy(record)
        record.msg = record.getMessage()
        record.message = record.msg
        record.args = None
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return record


def stop_logging_listener() -> None:
    """MongoDB 書き込み用の QueueListener を止める（残っているレコードは書き切る）。

    listener が起動していなければ何もしない。プロセス終了時に atexit からも呼ばれる。
    """
    global _mongodb_listener
    listener = _mongodb_listener
    _mongodb_listener = None
    if listener is not None:
        listener.stop()


def _route_mongodb_through_queue(logger_names: list[str | None]) -> None:
    """dictConfig 済みの mongodb ハンドラを QueueHandler + QueueListener 経由に付け替える。

    指定したロガー（None はルート）に付いた mongodb ハンドラを 1 つの QueueHandler に
    置き換え、実際の `MongoDBHandler` は QueueListener の宛先だけにする。
    mongodb ハンドラがどのロガーにも付いていなければ何もしない。
    """
    global _mongodb_listener, _atexit_registered
    mongodb_handler: logging.Handler | None = None
    queue_handler: logging.handlers.QueueHandler | None = None
    for name in logger_names:
        target = logging.getLogger(name)
        for h in list(target.handlers):
            if h.get_name() != MONGODB_HANDLER_NAME:
                continue
            if mongodb_handler is None:
                mongodb_handler = h
                queue_handler = _MongoDBQueueHandler(queue.SimpleQueue())
                # Queue へ積む前に絞り、MongoDB に書かないレベルを積まない
                queue_handler.setLevel(h.level)
            target.removeHandler(h)
            target.addHandler(queue_handler)

    if mongodb_handler is None or queue_handler is None:
        return

    _mongodb_listener = logging.handlers.QueueListener(
        queue_handler.queue, mongodb_handler, respect_handler_level=True
    )
    _mongodb_listener.start()
    if not _atexit_registered:
        atexit.register(stop_logging_listener)
        _atexit_registered = True


def setup_logging() -> None:
    """config/logging.yaml を読み込んでロギングを初期化する。

    MongoDB ハンドラへの接続情報は AppConfig から取得して注入する。
    mongodb ハンドラがあるときは、イベントループを Mongo 往復で塞がないよう
    ロガーには QueueHandler を付け、実際の書き込みは QueueListener の別スレッドで行う。
    起動時に MongoDB へ届かなければ例外がそのまま伝播する（起動 fail-fast）。
    mongodb ハンドラが無いときは MongoDB に接続しない。
    YAML ファイルが存在しない場合は basicConfig にフォールバックする。
    """
    # 再初期化に備え、前回の listener を止めてから組み立て直す
    stop_logging_listener()
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
    has_mongodb = isinstance(handlers, dict) and MONGODB_HANDLER_NAME in handlers
    if has_mongodb:
        # AppConfig から MongoDB 接続情報を注入
        handlers[MONGODB_HANDLER_NAME]["uri"] = config.env.mongodb_uri
        handlers[MONGODB_HANDLER_NAME]["db_name"] = config.mongodb.db_name
        handlers[MONGODB_HANDLER_NAME]["ttl_hours"] = dict(config.bot.log_ttl_hours)
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

    if has_mongodb:
        loggers = log_config.get("loggers")
        logger_names: list[str | None] = [None]
        if isinstance(loggers, dict):
            logger_names.extend(loggers.keys())
        _route_mongodb_through_queue(logger_names)
