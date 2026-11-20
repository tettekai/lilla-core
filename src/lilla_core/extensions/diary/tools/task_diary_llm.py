"""日記の既定タスクのうち、会話で進める聞き取り（`task_hearing_diary`）の task クラス。

清書（`task_write_diary`）は LLM を通さず保存先へ直接書くため、別のクラス
（`task_diary_faircopy.DiaryFaircopyTask`）にした。

実行部分（LLM を 1 往復させ、`NO_NOTIFICATION` なら通知しない、通知先へ送る）は
コアの `task_scheduled_llm` の `ScheduledLlmTask` をそのまま使い、このクラスは
YAML で省略された項目を stem ごとのコード既定値で埋めるだけを受け持つ。そのため
同梱の YAML は `type` だけでよく、利用者が `${CONFIG_ROOT}/tools` に同じ stem の
YAML を置いて置き換えるときも、変えたい項目だけを書けばよい。

YAML の項目（すべて省略可能）:

- `schedule` … crontab（`ui.timezone` で解釈）。既定は stem ごと
- `llm_provider` … 既定は `llm.default`
- `prompt` … source spec（`file:` / `dir:`）。既定は同梱の `task_prompts/` から
  `ui.locale` → `ja` → `en` の順に選ぶ
- `target` … 既定は `dm:{DISCORD_MY_USER_ID}`
- `available_tools` … 既定は stem ごと。展開できなければロード時に落とす
- `max_tool_call_iterations` … `task_scheduled_llm` と同じ
"""
from __future__ import annotations

from pathlib import Path

from lilla_core.builtin_tools import task_scheduled_llm as _base

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "task_prompts"

#: 既定プロンプトの言語の選び方（`ui.locale` に一致する言語の次に試す順）。
_PROMPT_FALLBACK_LOCALES = ("ja", "en")

#: 通知先の既定値（`discord.my_user_id` への DM）。
DEFAULT_TARGET = "dm:{DISCORD_MY_USER_ID}"

#: stem ごとのコード既定値。
TASK_DEFAULTS: dict[str, dict] = {
    "task_hearing_diary": {
        "schedule": "0 22 * * *",
        "available_tools": ["$main"],
    },
}


def select_default_prompt_path(name: str, locale: str | None) -> Path:
    """stem と `ui.locale` から同梱の既定プロンプトのファイルを選ぶ。

    `locale` → `ja` → `en` の順に、存在する最初のファイルを返す。

    Args:
        name: タスクの stem。
        locale: `ui.locale` の値。

    Returns:
        既定プロンプトのファイルパス。

    Raises:
        ValueError: どの言語のファイルも見つからない場合。
    """
    candidates = [locale] if isinstance(locale, str) and locale else []
    candidates += [c for c in _PROMPT_FALLBACK_LOCALES if c not in candidates]
    for lang in candidates:
        path = _PROMPT_DIR / f"{name}.{lang}.md"
        if path.is_file():
            return path
    raise ValueError(f"Task tool {name} has no bundled default prompt")


class DiaryLlmTask(_base.ScheduledLlmTask):
    """YAML で省略された項目を stem ごとの既定値で埋める、日記の定期タスク。"""

    def __init__(self, config: dict, name: str) -> None:
        """YAML 設定に既定値を補ってから `ScheduledLlmTask` として組み立てる。

        Args:
            config: タスクツールの YAML 設定（`_yaml_path` 付き）。
            name: YAML のファイル名 stem（ツール名）。

        Raises:
            ValueError: stem が日記の既定タスクでない場合、または
                `ScheduledLlmTask` の検証（`available_tools` の展開など）に失敗した場合。
        """
        super().__init__(self._fill_defaults(dict(config or {}), name), name)

    @staticmethod
    def _fill_defaults(config: dict, name: str) -> dict:
        """省略された項目を既定値で埋めた設定を返す。

        Args:
            config: YAML 設定のコピー。
            name: タスクの stem。

        Returns:
            既定値を補った設定。

        Raises:
            ValueError: stem が日記の既定タスクでない場合。
        """
        from lilla_core.core.config import get_config

        defaults = TASK_DEFAULTS.get(name)
        if defaults is None:
            raise ValueError(
                f"Task tool {name} is not a diary task "
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
            path = select_default_prompt_path(name, cfg.ui.locale)
            config["prompt"] = f"file:{path}"
        if "available_tools" not in config:
            config["available_tools"] = list(defaults["available_tools"])
        return config
