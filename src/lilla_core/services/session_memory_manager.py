"""セッションメモリ管理サービス。

会話履歴や short_term_memory（ツール実行の行動履歴）とは別に、エージェント自身が
「今進めている作業の途中状態」「次のターンでも覚えておきたい一時的な意図」などを
判断して保持するための、単一領域のメモリを提供する。

プロセス内メモリのみで保持し、MongoDB 等への永続化は行わない（プロセス再起動で消える）。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from functools import lru_cache

from lilla_core.core.config import AppConfig, get_config
from lilla_core.utils.datetime_utils import utc_now


class SessionMemoryManager:
    """常に最新1件の文字列のみを保持するセッションメモリ管理クラス。"""

    def __init__(self, config: AppConfig) -> None:
        self._ttl_hours = config.memory.session_memory_ttl_hours
        self._content: str | None = None
        self._updated_at: datetime | None = None

    def get(self) -> str | None:
        """有効なセッションメモリがあれば内容を返す。TTL 切れ・未設定なら None を返す。"""
        if self._content is None or self._updated_at is None:
            return None
        if utc_now() - self._updated_at >= timedelta(hours=self._ttl_hours):
            self.clear()
            return None
        return self._content

    def set(self, content: str) -> None:
        """セッションメモリを上書き保存する（TTL はこの時点からリセットされる）。"""
        self._content = content
        self._updated_at = utc_now()

    def clear(self) -> None:
        """セッションメモリをクリアする。"""
        self._content = None
        self._updated_at = None


@lru_cache(maxsize=1)
def get_session_memory_manager() -> SessionMemoryManager:
    """SessionMemoryManager のシングルトンインスタンスを返す。

    初回呼び出し時に設定を読み込んでインスタンスを生成し、以降は同じインスタンスを返す。
    """
    config = get_config()
    return SessionMemoryManager(config)
