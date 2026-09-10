"""Motor クライアントの共通ファクトリ。"""
from __future__ import annotations

from functools import lru_cache

from motor.motor_asyncio import AsyncIOMotorClient


@lru_cache(maxsize=1)
def create_motor_client(mongo_uri: str) -> AsyncIOMotorClient:
    """tz_aware=True を指定した AsyncIOMotorClient をプロセス内で共有する。

    motor はデフォルトで MongoDB の datetime を naive（tzinfo なし）の UTC として
    返すため、`.astimezone()` などでローカル時刻と誤解釈されるバグの原因になる。
    tz_aware=True により読み出した datetime はすべて timezone-aware な UTC となる。
    全リポジトリ・全 Mongo アクセスはこのファクトリ経由でクライアントを生成すること。
    `lru_cache(maxsize=1)` によりプロセス内で常に 1 インスタンスのみを共有し、
    呼び出しのたびに新規クライアント（コネクションプール＋監視タスク）を生成
    しないようにする（`mongo_uri` は `get_config()` 由来の固定値のため、複数
    URI での呼び分けは想定しない）。

    Args:
        mongo_uri: MongoDB への接続 URI。

    Returns:
        tz_aware=True が設定された AsyncIOMotorClient（プロセス内で共有）。
    """
    return AsyncIOMotorClient(mongo_uri, tz_aware=True)
