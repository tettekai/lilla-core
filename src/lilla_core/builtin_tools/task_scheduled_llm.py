"""スケジュール実行で LLM に処理を委譲し、必要なときだけ Discord へ通知する定期タスク。

cron で起きて、YAML で指定したプロンプトを `client_type="task"` の会話として
`run_conversation` へ渡し、返ってきた本文を `target` へ送るだけのタスク。
「送るべきことが無い」と LLM が判断した回（`NO_NOTIFICATION` を返した回）は
会話履歴にも残さず Discord にも送らないので、定期的に様子を見に行かせても
通知が増えない。

コアは自動では読み込まない。`${CONFIG_ROOT}/tools/task_*.yaml` に
`type: task_scheduled_llm` を置くと opt-in で有効化できる（import パス形式の
`type: lilla_core.builtin_tools.task_scheduled_llm` も使える）。

YAML の項目:

- `schedule` … cron 式（`ui.timezone` で解釈される）
- `target` … 通知先（`dm:USER_ID` / `channel:CHANNEL_ID`）。`{DISCORD_MY_USER_ID}`
  と書くと `discord.my_user_id` に置き換える
- `llm_provider` … 必須。`run_conversation` に渡す LLM プロバイダー名
- `prompt` … 必須。`utils/resource_loader.py` の `load_text_resources` にそのまま
  渡す source spec（`file:` / `dir:`。リストでもよい）
- `available_tools` … 必須。この実行で LLM に見せるツールの許可リスト（YAML stem と
  `$main` の並び）。`llm_tool_loader.resolve_available_tools` で展開する。空リストは
  ツールなしで、キーが無い場合は継承せず初期化で落とす
"""
from __future__ import annotations

import logging
from datetime import datetime

from lilla_core.utils.datetime_utils import local_now, local_timezone

logger = logging.getLogger(__name__)

#: プロンプト中で現在日時に置き換えるプレースホルダ。
_NOW_PLACEHOLDER = "{{now}}"
#: `_NOW_PLACEHOLDER` を置き換える日時の書式（解決済みタイムゾーンの日時）。
_NOW_FORMAT = "%Y-%m-%d %H:%M"
#: `target` 中で `discord.my_user_id` に置き換えるプレースホルダ。
_MY_USER_ID_PLACEHOLDER = "{DISCORD_MY_USER_ID}"
#: LLM が「今回は通知しない」と伝えるためのマーカー。
_NO_NOTIFICATION_MARKER = "NO_NOTIFICATION"


def _is_no_notification(reply: str) -> bool:
    """LLM の返答が「通知しない」を意味するかどうかを返す。

    前後の空白と、強調のために付きやすい `*` を落としてから判定するため、
    `**NO_NOTIFICATION**` のような形も「通知しない」と扱う。空の返答も同じ扱い。

    Args:
        reply: LLM の返答本文。

    Returns:
        通知を送らないなら True。
    """
    cleaned = (reply or "").strip().strip("*").strip()
    return not cleaned or cleaned.upper() == _NO_NOTIFICATION_MARKER


class ScheduledLlmTask:
    """cron で LLM に処理を委譲し、必要なときだけ `target` へ通知する定期タスク。"""

    def __init__(self, config: dict, name: str) -> None:
        """YAML 設定からタスクを組み立てる。

        `llm_provider` / `prompt` / `available_tools` はこのタスクの前提なので、
        欠けていれば起動時（ツールのロード時）に落とす。`available_tools` は
        ロード済みの LLM ツールに対してここで展開し、展開に失敗しても落とす
        （書き忘れと「ツールなし」を区別するため、キーが無いときに
        `main_available_tools` を継承することはしない）。

        Args:
            config: タスクツールの YAML 設定（`_yaml_path` 付き）。
            name: YAML のファイル名 stem（ツール名）。

        Raises:
            ValueError: `llm_provider` / `prompt` / `available_tools` が無い場合、
                または `available_tools` を展開できない場合。
        """
        self._config = config or {}
        self.name = name
        self.description = self._config.get(
            "description",
            "Run a scheduled LLM turn and notify only when there is something to say.",
        )
        self.category = self._config.get("category", "notification")
        self.schedule = self._config.get("schedule")
        self._target = self._config.get("target")
        self._llm_provider = self._config.get("llm_provider")
        self._prompt_sources = self._config.get("prompt")
        if not self._llm_provider:
            raise ValueError(f"Task tool {name} requires llm_provider")
        if not self._prompt_sources:
            raise ValueError(f"Task tool {name} requires prompt")
        if "available_tools" not in self._config:
            raise ValueError(f"Task tool {name} requires available_tools")
        self._allowed_tool_names = self._resolve_available_tools(
            self._config["available_tools"]
        )

    async def execute(self, context: dict) -> None:
        """LLM に 1 往復させ、通知が必要なら `target` へ送る。

        例外は ERROR ログに出して外へ漏らさない（1 回の失敗でスケジューラ側の
        ジョブを騒がせないため）。

        Args:
            context: タスク実行コンテキスト。`discord_client` / `llm_tools` /
                `now` を参照する。
        """
        try:
            await self._run(context or {})
        except Exception as e:
            logger.error(
                "[scheduled_llm] %s failed: %s", self.name, e, exc_info=True
            )

    async def _run(self, context: dict) -> None:
        """プロンプトを組み立てて LLM を回し、通知判定のうえで配送する。

        Args:
            context: タスク実行コンテキスト。
        """
        from lilla_core.repository.conversation_repository import get_conversation_repo
        from lilla_core.services.conversation_service import run_conversation
        from lilla_core.services.message_util import send_to_discord

        target = self._resolve_target()
        if not target:
            logger.warning(
                "[scheduled_llm] %s has no target configured. Skipping", self.name
            )
            return

        prompt = self._build_prompt(context)
        if not prompt:
            logger.warning(
                "[scheduled_llm] %s resolved an empty prompt from %r. Skipping",
                self.name, self._prompt_sources,
            )
            return

        reply = await run_conversation(
            context.get("llm_tools") or {},
            client_type="task",
            llm_name=self._llm_provider,
            inject_user_content=prompt,
            allowed_tool_names=self._allowed_tool_names,
        )
        if _is_no_notification(reply):
            logger.info(
                "[scheduled_llm] %s decided not to notify. Nothing saved or sent",
                self.name,
            )
            return

        await send_to_discord(
            context.get("discord_client"), target, reply, get_conversation_repo()
        )
        logger.info("[scheduled_llm] %s sent a notification to %s", self.name, target)

    def _resolve_available_tools(self, entries) -> list[str]:
        """`available_tools` をロード済みの LLM ツールに対して展開する。

        Args:
            entries: YAML の `available_tools` の値。

        Returns:
            展開済みの YAML stem のリスト（空ならツールなし）。

        Raises:
            ValueError: 展開できない場合（リストでない・未知のトークンや stem など）。
        """
        from lilla_core.loaders.llm_tool_loader import (
            get_llm_tools,
            resolve_available_tools,
        )

        try:
            return resolve_available_tools(entries, get_llm_tools())
        except ValueError as e:
            raise ValueError(
                f"Task tool {self.name} has invalid available_tools: {e}"
            ) from e

    def _resolve_target(self) -> str | None:
        """通知先を解決する（`{DISCORD_MY_USER_ID}` を `discord.my_user_id` に置換）。

        Returns:
            通知先の文字列。未設定なら None。
        """
        from lilla_core.core.config import get_config

        if not self._target:
            return None
        return str(self._target).replace(
            _MY_USER_ID_PLACEHOLDER, str(get_config().discord.my_user_id)
        )

    def _build_prompt(self, context: dict) -> str:
        """`prompt` の source spec を読み込み、`{{now}}` を現在日時に置き換える。

        基準時刻は context の `now`（無ければ `local_now()`）で、表示は
        `local_timezone()` が解決したタイムゾーン（`ui.timezone`、未指定なら
        OS のローカル）へ合わせる。

        Args:
            context: タスク実行コンテキスト。

        Returns:
            LLM へ渡すプロンプト本文（前後の空白は除去済み）。
        """
        from lilla_core.utils.resource_loader import load_text_resources

        now = context.get("now")
        if not isinstance(now, datetime):
            now = local_now()
        if now.tzinfo is not None:
            now = now.astimezone(local_timezone())
        prompt = load_text_resources(self._prompt_sources)
        return prompt.replace(_NOW_PLACEHOLDER, now.strftime(_NOW_FORMAT)).strip()
