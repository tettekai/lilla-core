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
  渡す source spec（`file:` / `dir:`。リストでもよい）。読み込んだ本文は Jinja2
  テンプレートとして展開する（`services/prompt_template.py`。`{{ current_hour }}` は
  実行時刻を `ui.timezone` で `YYYY-MM-DD HH:xx` にした文字列）
- `available_tools` … 任意。この実行で LLM に見せるツールの許可リスト（YAML stem と
  `$` + セット名（`$main` など）の並び）。`llm_tool_loader.resolve_available_tools` で
  展開する。空リスト、またはキーが無い場合はどちらもツールなし（`main` セットは継承しない）。
  値はあるが不正（リストでない・未知のトークンや stem など）なときだけ初期化で落とす
- `max_tool_call_iterations` … 任意。このタスクだけのツール呼び出しの往復上限（正の
  int）。省略時は `llm.max_tool_call_iterations` を使う。int 以外・bool・1 未満は
  初期化で落とす

拡張が同梱したタスク（ローダーが `_extension` を入れたもの）は、ダッシュボードの設定画面で
保存したプロンプトの上書き（`extension_overrides` の `tasks.<ツール名>.prompt`。会話向けの
拡張のプロンプトと同じ `{"mode": "append" | "replace", "text"}`）を実行のたびに読み、
`prompt` の本文と合成する（`services/extension_prompt_override.py` の規則）。
`${CONFIG_ROOT}/extensions/<拡張名>/tools/` の YAML に `prompt` を書いたときは、そちらが勝ち、
画面の上書きは使わない。利用者が `${CONFIG_ROOT}/tools` に置いたタスクは上書きの対象外。
"""
from __future__ import annotations

import logging
from datetime import datetime

from lilla_core.services.prompt_template import render_or_raw

logger = logging.getLogger(__name__)

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

    #: ダッシュボードからプロンプトを上書きできるタスクの目印（ローダーはファイルから
    #: クラスを読むため、`isinstance` ではなくこの属性で見分ける）。
    prompt_override_supported = True

    def __init__(self, config: dict, name: str) -> None:
        """YAML 設定からタスクを組み立てる。

        `llm_provider` / `prompt` はこのタスクの前提なので、欠けていれば起動時
        （ツールのロード時）に落とす。`available_tools` は任意で、キーが無い
        場合は空リスト（ツールなし。`main` セットは継承しない）として
        扱い、キーがあるのに展開できない（不正な）場合だけ落とす。
        `max_tool_call_iterations` も任意で、キーが無ければ None（グローバル設定を
        使う）とし、正の int でなければ落とす。

        Args:
            config: タスクツールの YAML 設定（`_yaml_path` 付き）。
            name: YAML のファイル名 stem（ツール名）。

        Raises:
            ValueError: `llm_provider` / `prompt` が無い場合、
                `available_tools` が指定されているが展開できない場合、または
                `max_tool_call_iterations` が正の int でない場合。
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
        self.extension = self._config.get("_extension")
        self._file_override_keys = frozenset(self._config.get("_override_keys") or ())
        if not self._llm_provider:
            raise ValueError(f"Task tool {name} requires llm_provider")
        if not self._prompt_sources:
            raise ValueError(f"Task tool {name} requires prompt")
        self._allowed_tool_names = self._resolve_available_tools(
            self._config.get("available_tools", [])
        )
        self._max_tool_call_iterations = self._resolve_max_tool_call_iterations(
            self._config.get("max_tool_call_iterations")
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

        variables = await self._template_variables(context)
        if variables is None:
            logger.info(
                "[scheduled_llm] %s has nothing to do this time. Skipping", self.name
            )
            return

        prompt = await self._build_prompt(context, variables)
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
            max_tool_call_iterations=self._max_tool_call_iterations,
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

    def _resolve_max_tool_call_iterations(self, value) -> int | None:
        """`max_tool_call_iterations` を検証する。

        Args:
            value: YAML の `max_tool_call_iterations` の値（キーが無ければ None）。

        Returns:
            このタスクの往復上限。未指定なら None（グローバル設定を使う）。

        Raises:
            ValueError: 正の int でない場合（int 以外・bool・1 未満）。
        """
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(
                f"Task tool {self.name} has invalid max_tool_call_iterations: "
                f"{value!r} (must be a positive integer)"
            )
        return value

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

    async def _template_variables(self, context: dict) -> dict | None:
        """プロンプトの展開に足す変数を返す（派生クラスが上書きする。既定は何も足さない）。

        LLM に計算させたくない値（対象日など）をコードで決めて渡すための差し込み口。
        `None` を返すと、その回は LLM を呼ばずに何もしない（INFO ログだけ）。
        共通の変数（`current_hour`）と同じ名前は使えない（展開に失敗し、読み込んだままの
        本文になる）。

        Args:
            context: タスク実行コンテキスト。

        Returns:
            変数名 → 値。その回を飛ばすなら None。
        """
        return {}

    def prompt_variable_names(self) -> list[str]:
        """`_template_variables()` がプロンプトへ渡す変数名を返す（派生クラスが上書きする）。

        ダッシュボードのプレビューと保存時の検査が、この名前のダミー値で上書きの本文を
        試しに展開する。共通の変数（`current_hour` / `notes`）は含めない。
        """
        return []

    @property
    def prompt_file_overridden(self) -> bool:
        """`${CONFIG_ROOT}/extensions/<拡張名>/tools/` の YAML が `prompt` を書いているか。"""
        return "prompt" in self._file_override_keys

    async def load_default_prompt(self) -> str:
        """`prompt` の source spec を読み込んだ本文（展開前）を返す。

        画面上書きを合成するときの「既定」で、ダッシュボードの表示にも使う。
        """
        from lilla_core.utils.resource_loader import load_text_resources

        return await load_text_resources(self._prompt_sources)

    async def _load_prompt_override(self):
        """ダッシュボードで保存したこのタスクのプロンプトの上書きを読む。

        拡張が同梱していないタスク・上書きファイルが `prompt` を書いているタスクは読まない。
        読み込みの失敗と不正な保存値は WARNING を出して `None`（既定を使う）にする。

        Returns:
            `PromptOverride`。上書きが無い・使わないなら `None`。
        """
        from lilla_core.repository.extension_override_repository import (
            get_extension_override_repo,
        )
        from lilla_core.services.extension_prompt_override import (
            InvalidPromptOverrideError,
            parse_prompt_override,
        )

        if not self.extension or self.prompt_file_overridden:
            return None
        try:
            doc = await get_extension_override_repo().find_one(self.extension)
        except Exception as e:
            logger.warning(
                "[scheduled_llm] Failed to load the prompt override of task '%s' of "
                "extension '%s'; using the default prompt: %s", self.name, self.extension, e,
            )
            return None
        tasks = (doc or {}).get("tasks")
        entry = tasks.get(self.name) if isinstance(tasks, dict) else None
        if not isinstance(entry, dict) or "prompt" not in entry:
            return None
        try:
            return parse_prompt_override(entry["prompt"])
        except InvalidPromptOverrideError as e:
            logger.warning(
                "[scheduled_llm] Ignoring invalid prompt override of task '%s' of "
                "extension '%s'; using the default prompt: %s", self.name, self.extension, e,
            )
            return None

    async def _build_prompt(self, context: dict, variables: dict | None = None) -> str:
        """`prompt` の本文に画面の上書きを合成し、Jinja2 テンプレートとして展開する。

        既定（`prompt` の本文）と上書きの追加分は、それぞれ独立したテンプレートとして
        展開してから空行区切りでつなぐ（`services/extension_prompt_override.py` の規則）。
        `current_hour` の基準時刻は context の `now`（無ければ今）で、表示は
        `local_timezone()` が解決したタイムゾーン（`ui.timezone`、未指定なら
        OS のローカル）へ合わせる。展開に失敗した部分は WARNING を出して読み込んだ
        ままの本文を使う。

        Args:
            context: タスク実行コンテキスト。
            variables: 展開に足す変数（`_template_variables()` の戻り値）。

        Returns:
            LLM へ渡すプロンプト本文（前後の空白は除去済み）。
        """
        from lilla_core.services.extension_prompt_override import compose_extension_prompt

        now = context.get("now")
        if not isinstance(now, datetime):
            now = None
        default_text = await self.load_default_prompt()
        override = await self._load_prompt_override()
        label = f"task '{self.name}' prompt"

        async def _render(parts: list[str]) -> list[str]:
            return [
                await render_or_raw(part, label, variables or None, now=now) for part in parts
            ]

        return (await compose_extension_prompt(default_text, override, _render)).strip()
