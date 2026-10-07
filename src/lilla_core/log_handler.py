from __future__ import annotations

import logging
from datetime import timedelta

from pymongo import MongoClient, ASCENDING
from pymongo.errors import PyMongoError

from lilla_core.utils.datetime_utils import utc_now

# 起動時の接続確認で待つ上限（ミリ秒）。pymongo 既定の約 30 秒は待たない
SERVER_SELECTION_TIMEOUT_MS = 5000

# ログ文書の `exception` に残す例外テキストの上限（文字数）。
# 例外メッセージに機微情報が混ざりうるため、長い traceback を丸ごとは残さない
MAX_EXCEPTION_TEXT_CHARS = 8000


def format_exception_text(
    record: logging.LogRecord, formatter: logging.Formatter | None = None
) -> str | None:
    """レコードに付いた例外を、ログ文書へ保存する文字列にする。

    `exc_text`（整形済み）があればそれを、無ければ `exc_info` を `formatException()` で
    整形して使う。どちらも無ければ None を返す（例外なしのログ）。
    `MAX_EXCEPTION_TEXT_CHARS` を超える場合は、例外の種類とメッセージが載る末尾側を残し、
    先頭を切り詰めて省略した文字数を書き添える。

    :param record: 対象のログレコード
    :param formatter: traceback の整形に使う Formatter。None なら既定の Formatter
    :return: 保存する例外テキスト。例外なしなら None
    """
    text = record.exc_text
    if not text and record.exc_info and record.exc_info[0] is not None:
        text = (formatter or logging.Formatter()).formatException(record.exc_info)
    if not text:
        return None
    if len(text) > MAX_EXCEPTION_TEXT_CHARS:
        omitted = len(text) - MAX_EXCEPTION_TEXT_CHARS
        text = f"...[truncated {omitted} chars]\n" + text[-MAX_EXCEPTION_TEXT_CHARS:]
    return text


class MongoDBHandler(logging.Handler):
    """logging.Handler の実装。ログレコードを MongoDB に書き込む。

    `insert_one` は同期 I/O のため、ロガーへ直接付けずに
    `core/logging_setup.py` が組み立てる `QueueListener` の宛先としてだけ使う
    （イベントループ上の呼び出し元は Queue へ積むだけで戻る）。

    ログ用の `MongoClient` はアプリの非同期クライアント（`AsyncMongoClient`）とは共有しない別インスタンス。

    ドキュメントの構造:
        asctime    : フォーマット済みの日時文字列
        levelname  : ログレベル名 (INFO / WARNING / ERROR ...)
        message    : ログ本文
        created_at : UTC datetime（作成日時）
        expires_at : UTC datetime（TTL インデックス用。レベル別 TTL から計算）
        exception  : 例外の traceback（例外付きのログだけ。`MAX_EXCEPTION_TEXT_CHARS` で切り詰める）
    """

    def __init__(
        self,
        uri: str,
        db_name: str,
        ttl_hours: dict[str, int] | None = None,
        collection_name: str = "logs",
    ) -> None:
        """MongoDBHandler を初期化する。

        TTL インデックスの作成が起動時の接続確認を兼ねる。
        `SERVER_SELECTION_TIMEOUT_MS` 以内に MongoDB へ届かなければ `RuntimeError` を
        送出する（起動 fail-fast。`logging.config.dictConfig` 経由なら `ValueError` に包まれる）。

        :param uri: MongoDB 接続 URI
        :param db_name: データベース名
        :param ttl_hours: ログレベル別の TTL 時間 dict。None の場合はデフォルト値を使用する。
        :param collection_name: ログを保存するコレクション名
        :raises RuntimeError: 起動時に MongoDB へ接続できなかった場合
        """
        super().__init__()
        if ttl_hours is None:
            ttl_hours = {"debug": 72, "info": 240, "warning": 720, "error": 720}
        self._ttl_hours = ttl_hours
        # 初期化に失敗したインスタンスも logging.shutdown() から close() されるため先に用意する
        self._client: MongoClient | None = None
        client = MongoClient(uri, serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS)
        try:
            self._collection = client[db_name][collection_name]
            self._collection.create_index(
                [("expires_at", ASCENDING)],
                expireAfterSeconds=0,
                background=True,
            )
        except PyMongoError as e:
            client.close()
            # URI は認証情報を含みうるため載せない
            raise RuntimeError(
                "MongoDB log handler could not reach MongoDB "
                f"(db={db_name}, collection={collection_name}, "
                f"timeout={SERVER_SELECTION_TIMEOUT_MS}ms): {e}"
            ) from e
        self._client = client

    def emit(self, record: logging.LogRecord) -> None:
        """ログレコードを MongoDB に書き込む。

        pymongo の内部ログによる再帰呼び出しを防ぐため、
        そのロガーからのレコードは無視する。
        TTL はレベル別に設定し、該当レベルが存在しない場合は warning の値にフォールバックする。
        例外付きのレコードは `message` を変えず、traceback を `exception` フィールドに足す。
        書き込みの失敗は `handleError` に任せ、プロセスは落とさない。
        """
        # pymongo の内部ログによる再帰呼び出しを防ぐ
        if record.name.startswith("pymongo"):
            return
        try:
            level_key = record.levelname.lower()
            fallback = self._ttl_hours.get("warning", 720)
            hours = self._ttl_hours.get(level_key, fallback)
            now = utc_now()
            formatter = self.formatter or logging.Formatter()
            doc = {
                "asctime": formatter.formatTime(record),
                "levelname": record.levelname,
                "message": record.getMessage(),
                "created_at": now,
                "expires_at": now + timedelta(hours=hours),
            }
            exception_text = format_exception_text(record, formatter)
            if exception_text is not None:
                doc["exception"] = exception_text
            self._collection.insert_one(doc)
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        """ハンドラを閉じ、ログ用の MongoClient も閉じる。"""
        try:
            if self._client is not None:
                self._client.close()
                self._client = None
        finally:
            super().close()
