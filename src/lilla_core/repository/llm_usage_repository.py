"""LLM のトークン使用量の記録（`llm_usage` コレクション）のリポジトリ。

1 回の LLM 呼び出しにつき 1 ドキュメントを保存する。保存するのはプロバイダー名・
モデル名・入口・所要時間・トークン数・概算コスト・生の `usage` だけで、メッセージ
本文・プロンプト・ツール結果は持たない（組み立ては `services/llm_usage.py`）。

保持期間は各ドキュメントの `expire_at` に書き、TTL インデックス
（`expireAfterSeconds: 0`）が消す。保持日数を変えても既存のインデックスを
作り直す必要がなく、変更後に書いた記録から新しい日数が効く。
"""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from typing import Any

from pymongo import ASCENDING

from lilla_core.repository.mongo_client import create_mongo_client

#: 集計の切り口 → `$group` の `_id` に使う式（`day` はタイムゾーンを後から埋める）
_GROUP_KEYS: dict[str, Any] = {
    "provider": {"provider": "$provider", "model": "$model"},
    "entry": {"entry": "$entry"},
}


@lru_cache(maxsize=1)
def get_llm_usage_repo() -> "LlmUsageRepository":
    """LlmUsageRepository のシングルトンインスタンスを返す。"""
    from lilla_core.core.config import get_config

    config = get_config()
    return LlmUsageRepository(config.env.mongodb_uri, config.mongodb.db_name)


def _metrics() -> dict[str, Any]:
    """`$group` に足す集計値（呼び出し数・トークン数・不明件数・概算コスト）を返す。

    `$sum` は数値以外（`None`）を無視するため、トークン数が不明な記録は合計に入らず
    `unknown_calls` にだけ数える。コストは単価が設定されていた記録の分だけを足し、
    その件数を `priced_calls` に数える。
    """
    return {
        "calls": {"$sum": 1},
        "input_tokens": {"$sum": "$input_tokens"},
        "output_tokens": {"$sum": "$output_tokens"},
        "total_tokens": {"$sum": "$total_tokens"},
        "unknown_calls": {"$sum": {"$cond": [{"$isNumber": "$total_tokens"}, 0, 1]}},
        "cost": {"$sum": "$cost"},
        "priced_calls": {"$sum": {"$cond": [{"$isNumber": "$cost"}, 1, 0]}},
        "duration_ms": {"$sum": "$duration_ms"},
    }


class LlmUsageRepository:
    """LLM のトークン使用量の記録を保存・集計するリポジトリ。"""

    def __init__(self, mongo_uri: str, db_name: str) -> None:
        """LlmUsageRepository を初期化する。

        Args:
            mongo_uri: MongoDB の接続 URI。
            db_name: 使用するデータベース名。
        """
        client = create_mongo_client(mongo_uri)
        self._collection = client[db_name]["llm_usage"]

    async def init_collection(self) -> None:
        """コレクションの初期化を行う（起動時の一元初期化用の薄いオーケストレーション層）。"""
        await self.ensure_indexes()

    async def ensure_indexes(self) -> None:
        """インデックスを作成する。

        - `expire_at` に TTL インデックス（`expireAfterSeconds=0`）
        - 集計の期間絞り込み用に `created_at`
        """
        await self._collection.create_index([("expire_at", ASCENDING)], expireAfterSeconds=0)
        await self._collection.create_index([("created_at", ASCENDING)])

    async def insert(self, record: dict[str, Any]) -> None:
        """使用量の記録を 1 件保存する。

        Args:
            record: `services/llm_usage.py` が組み立てた記録。
        """
        await self._collection.insert_one(record)

    async def summarize(self, since: datetime) -> dict[str, Any]:
        """`since` 以降の記録の合計を返す（記録が無ければ各値 0）。

        Args:
            since: 集計の開始時刻（UTC）。

        Returns:
            `calls` / `input_tokens` / `output_tokens` / `total_tokens` /
            `unknown_calls` / `cost` / `priced_calls` / `duration_ms` を持つ dict。
        """
        rows = await self._aggregate(since, None)
        if rows:
            return rows[0]
        return {key: 0 for key in _metrics()}

    async def aggregate(
        self, since: datetime, group_by: str, timezone: str = "UTC"
    ) -> list[dict[str, Any]]:
        """`since` 以降の記録を切り口ごとに集計する。

        Args:
            since: 集計の開始時刻（UTC）。
            group_by: `day`（`timezone` の暦日）/ `provider`（プロバイダー名とモデル名）/
                `entry`（入口のラベル）。
            timezone: `day` のときに暦日を数えるタイムゾーン（IANA 名か `+09:00` 形式）。

        Returns:
            切り口のキー（`date` / `provider` と `model` / `entry`）と集計値を持つ dict の
            リスト。`day` は日付の昇順、それ以外は合計トークン数の降順。

        Raises:
            ValueError: 未知の切り口を渡した場合。
        """
        if group_by == "day":
            key: Any = {
                "date": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$created_at",
                        "timezone": timezone,
                    }
                }
            }
        elif group_by in _GROUP_KEYS:
            key = _GROUP_KEYS[group_by]
        else:
            raise ValueError(f"Unknown llm usage group_by: {group_by}")
        rows = await self._aggregate(since, key)
        if group_by == "day":
            rows.sort(key=lambda row: row.get("date") or "")
        else:
            rows.sort(key=lambda row: (-(row.get("total_tokens") or 0), -row["calls"]))
        return rows

    async def _aggregate(self, since: datetime, key: Any) -> list[dict[str, Any]]:
        """`$match`（期間）→ `$group` を実行し、`_id` の中身を行へ展開して返す。"""
        pipeline = [
            {"$match": {"created_at": {"$gte": since}}},
            {"$group": {"_id": key, **_metrics()}},
        ]
        cursor = await self._collection.aggregate(pipeline)
        docs = await cursor.to_list(length=None)
        rows: list[dict[str, Any]] = []
        for doc in docs:
            group = doc.pop("_id", None)
            if isinstance(group, dict):
                doc.update(group)
            rows.append(doc)
        return rows
