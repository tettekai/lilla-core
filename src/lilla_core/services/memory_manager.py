from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

from lilla_core.core.config import AppConfig, get_config
from lilla_core.core.extension_points import (
    get_client_prompt_provider,
    register_client_prompt_provider,
)
from lilla_core.services.message_util import format_session_memory_block, prepend_timestamp_prefix
from lilla_core.services.session_memory_manager import get_session_memory_manager
from lilla_core.utils.datetime_utils import local_now, local_timezone


class MemoryManager:
    """会話履歴・ユーザーメモなどのメモリを統括管理するクラス。"""

    def __init__(self, config: AppConfig) -> None:
        from lilla_core.repository.conversation_repository import get_conversation_repo
        from lilla_core.repository.user_memo_repository import get_user_memo_repo
        from lilla_core.repository.tool_cache_repository import get_tool_cache_repo
        self._conv_repo = get_conversation_repo()
        self._memo_repo = get_user_memo_repo()
        self._tool_cache_repo = get_tool_cache_repo()
        self._session_memory = get_session_memory_manager()
        self._config = config
        self._max_history_turns = config.memory.max_history_turns
        # Discord はコア自身が知ってよいクライアント種別なので、拡張に頼らず
        # ここで自己登録する。`get_memory_manager()` を通さず `MemoryManager()` を
        # 直接生成する経路（テストなど）でも確実に登録されるよう __init__ に置く。
        # 他の拡張ポイントと同じ「後勝ち」規約を壊さないよう、未登録の場合のみ
        # デフォルトを登録する（拡張側が独自の discord プロバイダを登録済みなら
        # それを上書きしない）。
        if get_client_prompt_provider("discord") is None:
            register_client_prompt_provider(
                "discord", lambda: get_config().discord_client_prompt
            )

    async def add_conversation(
        self,
        message: dict,
        tags: list[str] | None = None,
        discord_channel_id: int | None = None,
        discord_message_ids: list[int] | None = None,
    ) -> str:
        """会話メッセージを保存し、保存したドキュメントの _id を返す。

        Args:
            message: 保存する会話メッセージ（role / content）。
            tags: エントリの分類タグ（例: ["toolresult", "dirty"]）。
            discord_channel_id: 対応する Discord メッセージのチャンネル ID。
            discord_message_ids: 対応する Discord メッセージの ID リスト。

        Returns:
            保存したドキュメントの _id（文字列）。
        """
        return await self._conv_repo.save(
            message,
            tags=tags,
            discord_channel_id=discord_channel_id,
            discord_message_ids=discord_message_ids,
        )

    async def load_conversation_history_with_timestamps(self) -> list[dict]:
        """タイムスタンプ付きの会話履歴を LLM 送信用に返す。

        MongoDB の time フィールドをシステムのローカルタイムゾーンに変換し、
        各メッセージの content 先頭に "[Apr 28 11:22] " 形式のタイムスタンプを付与する。
        元の message dict は変更せず、新しい dict として返す。
        MongoDB 保存済みメッセージには一切手を加えない。

        content がリスト（画像添付など）の場合は、先頭の type="text" ブロックの
        text フィールド先頭に付与する。text ブロックが存在しない場合は先頭に挿入する。
        """
        local_tz = local_timezone()
        today = date.today()
        since_local = datetime(
            today.year, today.month, today.day,
            tzinfo=local_tz,
        ) - timedelta(days=self._config.memory.history_days - 1)
        since_utc = since_local.astimezone(timezone.utc)
        docs = await self._conv_repo.load_with_time_since(since_utc, self._max_history_turns)
        tz = local_tz
        result = []
        for doc in docs:
            msg = doc["message"]
            t = doc["time"].astimezone(tz).strftime("%b %d %H:%M")
            prefix = f"[{t}] "
            new_content = prepend_timestamp_prefix(msg["content"], prefix)
            result.append({"role": msg["role"], "content": new_content})
        return result

    async def build_system_prompt(self, extra_prompt: str = "", client_type: str = "") -> str:
        """systemプロンプトを組み立てて返す。

        base_prompt に以下を順に追記する:
        1. クライアント固有プロンプト（client_type に対応するプロバイダが
           `register_client_prompt_provider` で登録されている場合のみ付与）
        2. 動的メモリ（有効なuser_memosが存在する場合のみ）
        3. セッションメモリ（有効なセッションメモリが存在する場合のみ）
        4. 現在日時と時刻付き会話履歴
        """
        memos = await self._memo_repo.get_active()
        memo_section = ""
        if memos:
            lines = ["## User's Instructions"]
            for memo in memos:
                lines.append(f"- {memo['content']}")
            memo_section = "\n".join(lines)

        session_memory = self._session_memory.get()
        session_memory_section = format_session_memory_block(session_memory) if session_memory else ""

        now = local_now()
        weekday = now.strftime("%A")
        now_str = now.strftime("%b") + f" {now.day}" + " " + now.strftime("%H:xx") + f" ({weekday})"
        time_section = f"Current time: {now_str}"

        # クライアント種別ごとのプロンプトは拡張ポイントのレジストリから引く。
        # プロバイダは呼ぶたびに評価され（＝ファイルを読み直し）、未登録の
        # client_type では何も付与しない。
        provider = get_client_prompt_provider(client_type)
        client_prompt = provider() if provider is not None else ""

        base = self._config.system_prompt
        if client_prompt:
            base = base + "\n\n" + client_prompt
        if extra_prompt:
            base = base + "\n\n" + extra_prompt
        parts = [base]
        if memo_section:
            parts.append(memo_section)
        if session_memory_section:
            parts.append(session_memory_section)
        parts.append(time_section)

        cache_records = await self._tool_cache_repo.get_all_valid()
        if cache_records:
            lines = ["## Cached Tool Results"]
            for rec in cache_records:
                lines.append(f"\n### {rec['tool_name']}({rec['args_key']})")
                lines.append(rec["data"])
            parts.append("\n".join(lines))

        return "\n\n".join(parts)


@lru_cache(maxsize=1)
def get_memory_manager() -> MemoryManager:
    """MemoryManager のシングルトンインスタンスを返す。

    初回呼び出し時に設定を読み込んでインスタンスを生成し、以降は同じインスタンスを返す。
    """
    config = get_config()
    return MemoryManager(config)
