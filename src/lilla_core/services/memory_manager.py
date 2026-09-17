from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache

from lilla_core.core.config import AppConfig, get_config
from lilla_core.core.extension import get_client_prompt_providers
from lilla_core.services.message_util import format_session_memory_block, prepend_timestamp_prefix
from lilla_core.services.session_memory_manager import get_session_memory_manager
from lilla_core.utils.datetime_utils import local_now


def _resolve_client_prompt(client_type: str) -> str:
    """`client_type` に付けるクライアント固有プロンプトを解決する。

    解決順は次のとおり。プロバイダは呼ぶたびに評価される（＝ファイルを
    読み直す）ため、戻り値はキャッシュしない。

    1. 拡張の `client_prompt_providers()` に `client_type` があれば、全プロバイダを
       ロード順に評価し、空でない戻り値を空行区切りで連結して使う
    2. 誰も出していなければ、`"discord"` のときだけコア内蔵の
       `discord_client_prompt` を使う
    3. どちらにも該当しなければ何も付けない

    Args:
        client_type: プロンプトを解決するクライアント種別。

    Returns:
        追記するプロンプト本文。付けるものが無ければ空文字列。
    """
    providers = get_client_prompt_providers(client_type)
    if providers:
        parts = [text for text in (provider() for provider in providers) if text]
        return "\n\n".join(parts)
    if client_type == "discord":
        return get_config().discord_client_prompt
    return ""


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

        MongoDB の time フィールドを `local_timezone()` が解決したタイムゾーン
        （`ui.timezone`、未指定なら OS のローカル）に変換し、各メッセージの
        content 先頭に "[Apr 28 11:22] " 形式のタイムスタンプを付与する。
        履歴を遡る起点となる「今日」も同じタイムゾーンのカレンダー日付で数える。
        元の message dict は変更せず、新しい dict として返す。
        MongoDB 保存済みメッセージには一切手を加えない。

        content がリスト（画像添付など）の場合は、先頭の type="text" ブロックの
        text フィールド先頭に付与する。text ブロックが存在しない場合は先頭に挿入する。
        """
        now_local = local_now()
        local_tz = now_local.tzinfo
        today = now_local.date()
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

    def _resolve_registered_channel_section(self, discord_channel_id: int | None) -> str:
        """「今この登録チャンネルにいる」旨の短い一節を返す。

        `discord.channels` に登録されたチャンネルでの会話のときだけ本文を返し、
        未登録チャンネル・DM・Discord 以外の呼び出し（`discord_channel_id` が
        `None`）では空文字列を返す。会話履歴そのものは登録の有無によらず全
        チャンネル横断のままなので、「ここだけの履歴ではない」ことも併せて伝える。

        Args:
            discord_channel_id: 会話が行われている Discord チャンネル ID。

        Returns:
            システムプロンプトへ追記する一節。付けるものが無ければ空文字列。
        """
        if discord_channel_id is None:
            return ""
        entry = self._config.discord.find_channel_by_id(discord_channel_id)
        if entry is None:
            return ""
        return (
            "## Current Channel\n"
            f'You are talking in the registered Discord channel "{entry.name}". '
            "The conversation history below spans every channel and DM, not just this one."
        )

    async def build_system_prompt(
        self,
        extra_prompt: str = "",
        client_type: str = "",
        discord_channel_id: int | None = None,
    ) -> str:
        """systemプロンプトを組み立てて返す。

        base_prompt に以下を順に追記する:
        1. クライアント固有プロンプト（`_resolve_client_prompt` で解決できた場合のみ付与）
        2. 登録チャンネルの一節（`discord.channels` に登録されたチャンネルでの会話のみ）
        3. 動的メモリ（有効なuser_memosが存在する場合のみ）
        4. セッションメモリ（有効なセッションメモリが存在する場合のみ）
        5. 現在日時と時刻付き会話履歴

        Args:
            extra_prompt: base_prompt の直後に追記する文字列（会話プロンプトなど）。
            client_type: クライアント固有プロンプトの解決に使うクライアント種別。
            discord_channel_id: 会話が行われている Discord チャンネル ID。登録
                チャンネルの一節の解決に使う。
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

        client_prompt = _resolve_client_prompt(client_type)
        channel_section = self._resolve_registered_channel_section(discord_channel_id)

        base = self._config.system_prompt
        if client_prompt:
            base = base + "\n\n" + client_prompt
        if extra_prompt:
            base = base + "\n\n" + extra_prompt
        parts = [base]
        if channel_section:
            parts.append(channel_section)
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
