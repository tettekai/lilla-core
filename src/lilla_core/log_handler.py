from __future__ import annotations

import logging
from datetime import timedelta

from pymongo import MongoClient, ASCENDING

from lilla_core.utils.datetime_utils import utc_now


class MongoDBHandler(logging.Handler):
    """logging.Handler の実装。ログレコードを MongoDB に書き込む。

    ドキュメントの構造:
        asctime    : フォーマット済みの日時文字列
        levelname  : ログレベル名 (INFO / WARNING / ERROR ...)
        message    : ログ本文
        created_at : UTC datetime（作成日時）
        expires_at : UTC datetime（TTL インデックス用。レベル別 TTL から計算）
    """

    def __init__(
        self,
        uri: str,
        db_name: str,
        ttl_hours: dict[str, int] | None = None,
        collection_name: str = "logs",
    ) -> None:
        """MongoDBHandler を初期化する。

        :param uri: MongoDB 接続 URI
        :param db_name: データベース名
        :param ttl_hours: ログレベル別の TTL 時間 dict。None の場合はデフォルト値を使用する。
        :param collection_name: ログを保存するコレクション名
        """
        super().__init__()
        if ttl_hours is None:
            ttl_hours = {"debug": 72, "info": 240, "warning": 720, "error": 720}
        self._ttl_hours = ttl_hours
        client = MongoClient(uri)
        self._collection = client[db_name][collection_name]
        self._collection.create_index(
            [("expires_at", ASCENDING)],
            expireAfterSeconds=0,
            background=True,
        )

    def emit(self, record: logging.LogRecord) -> None:
        """ログレコードを MongoDB に書き込む。

        pymongo / motor の内部ログによる再帰呼び出しを防ぐため、
        これらのロガーからのレコードは無視する。
        TTL はレベル別に設定し、該当レベルが存在しない場合は warning の値にフォールバックする。
        """
        # pymongo / motor の内部ログによる再帰呼び出しを防ぐ
        if record.name.startswith(("pymongo", "motor")):
            return
        try:
            level_key = record.levelname.lower()
            fallback = self._ttl_hours.get("warning", 720)
            hours = self._ttl_hours.get(level_key, fallback)
            now = utc_now()
            formatter = self.formatter or logging.Formatter()
            self._collection.insert_one(
                {
                    "asctime": formatter.formatTime(record),
                    "levelname": record.levelname,
                    "message": record.getMessage(),
                    "created_at": now,
                    "expires_at": now + timedelta(hours=hours),
                }
            )
        except Exception:
            self.handleError(record)
