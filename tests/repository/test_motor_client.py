"""src/lilla_core/repository/motor_client.py のテスト。

CLAUDE.md のルールに従い、src/repository 配下のソースのテストはテストクラスを作らず
関数として記述する。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from lilla_core.repository.motor_client import create_motor_client


def test_create_motor_client_shares_single_instance_in_process() -> None:
    """プロセス内で複数回呼び出しても同一インスタンスを返し、実体は 1 回しか生成しない
    (lru_cache(maxsize=1) による共有)。"""
    create_motor_client.cache_clear()
    try:
        with patch(
            "lilla_core.repository.motor_client.AsyncIOMotorClient", return_value=MagicMock()
        ) as mock_client_cls:
            client1 = create_motor_client("mongodb://localhost:27017/test")
            client2 = create_motor_client("mongodb://localhost:27017/test")

            assert client1 is client2
            mock_client_cls.assert_called_once_with(
                "mongodb://localhost:27017/test", tz_aware=True
            )
    finally:
        create_motor_client.cache_clear()
