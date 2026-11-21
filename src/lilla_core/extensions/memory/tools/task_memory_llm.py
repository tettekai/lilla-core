"""長期記憶の夜間タスク（毎日の整理 `task_memory_review` と週次の core 整理 `task_memory_compaction`）。

実行部分（LLM を回し、`NO_NOTIFICATION` なら通知しない、通知先へ送る）はコアの
`task_scheduled_llm` の `ScheduledLlmTask` をそのまま使い、このクラスは次の 2 つを受け持つ。

- YAML で省略された項目を stem ごとのコード既定値で埋める（同梱の YAML は `type` だけでよく、
  利用者が `${CONFIG_ROOT}/extensions/memory/tools/` に同じ stem の YAML で差分を重ねるときも、
  変えたい項目だけを書けばよい）
- 対象日・パスなど LLM に計算させたくない値をコードで決め、プロンプトの変数として渡す
  （`_template_variables()`）

stem ごとの変数:

- `task_memory_review` … `target_date`（`day_rollover` を基準に直前に締まった日。
  まだ追記できる日は読まない）と `remember_entries`（その日の remember を `<remember_entries>`
  タグで囲み、本文中の同名のタグを `strip_tag` で取り除いたもの）。記録が無い日は LLM を呼ばない。
  初回（`memory/personal/README.md` がまだ無いとき）は同梱のひな形を書いてから回す
- `task_memory_compaction` … `journal_path`（実行した月のアーカイブ `memory/journal/YYYYMM.md`）。
  `memory/core.md` がまだ無ければ LLM を呼ばない

YAML の項目（すべて省略可能）:

- `schedule` … crontab（`ui.timezone` で解釈）。既定は stem ごと
- `llm_provider` … 既定は `llm.default`
- `prompt` … source spec（`file:` / `dir:`）。既定は同梱の `task_prompts/` から
  `ui.locale` → `ja` → `en` の順に選ぶ
- `target` … 既定は `dm:{DISCORD_MY_USER_ID}`
- `available_tools` … 既定は notes の 4 つのツール。展開できなければロード時に落とす
- `max_tool_call_iterations` … 既定は stem ごと
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from lilla_core.builtin_tools import task_scheduled_llm as _base

from lilla_core.extensions.memory import CORE_PATH, PERSONAL_README_PATH

logger = logging.getLogger(__name__)

_PACKAGE_DIR = Path(__file__).resolve().parent.parent
_PROMPT_DIR = _PACKAGE_DIR / "task_prompts"
_TEMPLATE_DIR = _PACKAGE_DIR / "templates"

#: 既定プロンプト・ひな形の言語の選び方（`ui.locale` に一致する言語の次に試す順）。
_FALLBACK_LOCALES = ("ja", "en")

#: 通知先の既定値（`discord.my_user_id` への DM）。
DEFAULT_TARGET = "dm:{DISCORD_MY_USER_ID}"

#: 夜間タスクが読み書きに使うツール（notes の同梱ツール）。
NOTES_TOOLS = ["llm_notes_list", "llm_notes_read", "llm_notes_write", "llm_notes_search"]

REVIEW_TASK = "task_memory_review"
COMPACTION_TASK = "task_memory_compaction"

#: stem ごとのコード既定値。
TASK_DEFAULTS: dict[str, dict] = {
    REVIEW_TASK: {
        "schedule": "30 4 * * *",
        "available_tools": NOTES_TOOLS,
        "max_tool_call_iterations": 20,
    },
    COMPACTION_TASK: {
        "schedule": "30 3 * * mon",
        "available_tools": NOTES_TOOLS,
        "max_tool_call_iterations": 20,
    },
}


#: stem ごとにプロンプトへ渡す変数名（`_template_variables()` と合わせる）。
TASK_VARIABLE_NAMES: dict[str, tuple[str, ...]] = {
    REVIEW_TASK: ("target_date", "remember_entries"),
    COMPACTION_TASK: ("journal_path",),
}


def _select_bundled_file(directory: Path, stem: str, locale: str | None) -> Path:
    """`locale` → `ja` → `en` の順に、`{stem}.{言語}.md` のうち存在する最初のファイルを返す。

    Raises:
        ValueError: どの言語のファイルも見つからない場合。
    """
    candidates = [locale] if isinstance(locale, str) and locale else []
    candidates += [c for c in _FALLBACK_LOCALES if c not in candidates]
    for lang in candidates:
        path = directory / f"{stem}.{lang}.md"
        if path.is_file():
            return path
    raise ValueError(f"No bundled file for {stem} in {directory.name}")


def select_default_prompt_path(name: str, locale: str | None) -> Path:
    """stem と `ui.locale` から同梱の既定プロンプトのファイルを選ぶ。

    Raises:
        ValueError: どの言語のファイルも見つからない場合。
    """
    return _select_bundled_file(_PROMPT_DIR, name, locale)


def select_personal_readme_template(locale: str | None) -> Path:
    """`ui.locale` から同梱の `memory/personal/README.md` のひな形を選ぶ。"""
    return _select_bundled_file(_TEMPLATE_DIR, "personal_README", locale)


def _notes_storage():
    """保存先 `notes` を返す。

    Raises:
        RuntimeError: 保存先 `notes` が登録されていないとき。
    """
    from lilla_core.core.extension import get_file_storage

    storage = get_file_storage("notes")
    if storage is None:
        raise RuntimeError("file storage 'notes' is not registered")
    return storage


class MemoryLlmTask(_base.ScheduledLlmTask):
    """YAML で省略された項目を既定値で埋め、対象日などを変数で渡す長期記憶の夜間タスク。"""

    def __init__(self, config: dict, name: str) -> None:
        """YAML 設定に既定値を補ってから `ScheduledLlmTask` として組み立てる。

        Args:
            config: タスクツールの YAML 設定（`_yaml_path` 付き）。
            name: YAML のファイル名 stem（ツール名）。

        Raises:
            ValueError: stem が長期記憶の夜間タスクでない場合、または
                `ScheduledLlmTask` の検証（`available_tools` の展開など）に失敗した場合。
        """
        super().__init__(self._fill_defaults(dict(config or {}), name), name)

    @staticmethod
    def _fill_defaults(config: dict, name: str) -> dict:
        """省略された項目を既定値で埋めた設定を返す。

        Raises:
            ValueError: stem が長期記憶の夜間タスクでない場合。
        """
        from lilla_core.core.config import get_config

        defaults = TASK_DEFAULTS.get(name)
        if defaults is None:
            raise ValueError(
                f"Task tool {name} is not a memory task "
                f"(supported: {', '.join(TASK_DEFAULTS)})"
            )
        cfg = get_config()
        if not config.get("schedule"):
            config["schedule"] = defaults["schedule"]
        if not config.get("target"):
            config["target"] = DEFAULT_TARGET
        if not config.get("llm_provider"):
            config["llm_provider"] = cfg.llm.default
        if not config.get("prompt"):
            config["prompt"] = f"file:{select_default_prompt_path(name, cfg.ui.locale)}"
        if "available_tools" not in config:
            config["available_tools"] = list(defaults["available_tools"])
        if "max_tool_call_iterations" not in config:
            config["max_tool_call_iterations"] = defaults["max_tool_call_iterations"]
        return config

    def prompt_variable_names(self) -> list[str]:
        """stem ごとに `_template_variables()` が渡す変数名を返す。"""
        return list(TASK_VARIABLE_NAMES.get(self.name, ()))

    async def _template_variables(self, context: dict) -> dict | None:
        """stem ごとの変数を返す（することが無い回は None で LLM を呼ばない）。"""
        now = context.get("now")
        if not isinstance(now, datetime):
            now = None
        if self.name == REVIEW_TASK:
            return await self._review_variables(now)
        return await self._compaction_variables(now)

    async def _review_variables(self, now: datetime | None) -> dict | None:
        """毎日の整理の変数（対象日と、その日の remember）を返す。記録が無ければ None。"""
        from lilla_core.extensions.memory.service import (
            format_entries_for_prompt,
            get_entries,
            last_closed_date,
            load_memory_settings,
            rollover_time,
        )

        settings = await load_memory_settings()
        target = last_closed_date(rollover_time(settings), now)
        entries = await get_entries(target)
        if not entries:
            logger.info("[memory] No remember entries for %s", target.isoformat())
            return None
        await self._ensure_personal_readme()
        return {
            "target_date": target.isoformat(),
            "remember_entries": format_entries_for_prompt(entries),
        }

    async def _compaction_variables(self, now: datetime | None) -> dict | None:
        """週次の core 整理の変数（アーカイブのパス）を返す。`core.md` がまだ無ければ None。"""
        from lilla_core.extensions.memory.service import journal_path

        if not await _notes_storage().exists(CORE_PATH):
            logger.info("[memory] %s does not exist yet", CORE_PATH)
            return None
        return {"journal_path": journal_path(now)}

    async def _ensure_personal_readme(self) -> None:
        """`memory/personal/README.md` がまだ無ければ、同梱のひな形を書く（あれば何もしない）。"""
        from lilla_core.core.config import get_config
        from lilla_core.core.file_storage import FileStorageExistsError

        storage = _notes_storage()
        if await storage.exists(PERSONAL_README_PATH):
            return
        template = select_personal_readme_template(get_config().ui.locale)
        try:
            await storage.write(
                PERSONAL_README_PATH, template.read_text(encoding="utf-8"), mode="create"
            )
        except FileStorageExistsError:
            return
        logger.info("[memory] Created %s from the bundled template", PERSONAL_README_PATH)
