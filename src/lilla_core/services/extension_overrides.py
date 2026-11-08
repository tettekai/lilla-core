"""拡張ごとの上書き（`extension_overrides`）を編集するための業務ロジック。

観測用ダッシュボードの編集ページ（`handlers/dashboard_extensions.py`）が使う。
受け持つのは次の 3 つで、HTTP の組み立てはしない。

- 編集できる対象の列挙（デフォルトのプロンプトを持つか、拡張が同梱した定期タスク）
- 画面に出す「デフォルト・上書き・実効値」の組み立て（`build_detail`）
- 保存リクエストの検証と、リポジトリへ渡す `$set` / `$unset` への変換（`parse_update`）

上書きの解釈（プロンプトは `services/memory_manager.py`）とずれないよう、実効値の
決め方はそれと同じ規則で書く。タスクの実効値は `services/task_schedules.py` の共通処理
（内部スケジューラ・`GET /api/tasks` と同じ）で決める。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from lilla_core.core.extension import Extension, get_default_prompts, get_extensions
from lilla_core.services.task_schedules import (
    is_valid_crontab,
    parse_task_overrides,
    resolve_task,
    scheduled_task_entries,
)
from lilla_core.utils.datetime_utils import local_timezone

#: 保存リクエストのトップレベルで受け付けるキー。
_UPDATE_KEYS = frozenset({"prompt", "tasks"})
#: タスク 1 つの上書きで受け付けるキー。
_TASK_KEYS = frozenset({"schedule", "enabled"})


class OverrideValidationError(ValueError):
    """保存リクエストが不正なとき（画面へそのまま見せてよい英語の理由を持つ）。"""


@dataclass(frozen=True)
class OverrideUpdate:
    """検証済みの保存内容。

    Attributes:
        set_fields: リポジトリへ渡す「パス -> 値」。
        unset_fields: リポジトリへ渡す消すパス。
        task_names: 変更したタスク（ツール名）。空ならスケジューラへの反映は要らない。
    """

    set_fields: dict[str, object] = field(default_factory=dict)
    unset_fields: list[str] = field(default_factory=list)
    task_names: list[str] = field(default_factory=list)


def find_extension(name: str) -> Extension | None:
    """ロード済みの拡張を名前で探す。無ければ `None`。"""
    for ext in get_extensions():
        if ext.name == name:
            return ext
    return None


def default_prompts_of(name: str) -> dict[str, str] | None:
    """拡張のデフォルトプロンプト（言語 -> 本文）を返す。持たなければ `None`。"""
    for ext_name, prompts in get_default_prompts():
        if ext_name == name:
            return prompts
    return None


def task_entries_of(name: str, tools: dict) -> dict[str, dict]:
    """拡張が同梱した定期タスクを「ツール名 -> ツールのエントリ」でロード順に返す。

    対象はロード済みのツールのうち、その拡張のもの（利用者の YAML で置き換えた分も
    含む）で、`schedule` を持つものだけ。`enabled: false` の YAML でロードしていない
    ツールは `tools` に無いため対象にならない（上書きで復活させない）。

    Args:
        name: 拡張名。
        tools: `load_all_tools()` が返したツールのレジストリ。
    """
    return scheduled_task_entries(tools, name)


def list_editable(tools: dict) -> list[dict]:
    """ロード済みの拡張を、編集できるものの目印つきでロード順に返す。

    Args:
        tools: ツールのレジストリ。

    Returns:
        `name` / `has_prompt` / `task_count` / `has_settings` / `editable` を持つ dict のリスト。
        `has_settings` は画面管理の設定（`settings_model()`）を宣言しているか。
    """
    from lilla_core.core.extension import get_settings_models

    settings_models = get_settings_models()
    items = []
    for ext in get_extensions():
        has_prompt = default_prompts_of(ext.name) is not None
        task_count = len(task_entries_of(ext.name, tools))
        has_settings = ext.name in settings_models
        items.append(
            {
                "name": ext.name,
                "has_prompt": has_prompt,
                "task_count": task_count,
                "has_settings": has_settings,
                "editable": has_prompt or task_count > 0 or has_settings,
            }
        )
    return items


def build_detail(
    name: str,
    tools: dict,
    doc: dict | None,
    locale: str,
    applied_schedules: dict[str, str],
    scheduler_running: bool,
    scheduler_enabled: bool = True,
) -> dict:
    """1 つの拡張の編集画面に出す内容を組み立てる。

    プロンプトはデフォルト（`ui.locale` → `ja` → `en` → 先頭の言語）・上書き・実効値を、
    タスクはタスクごとに `schedule` / `enabled` のデフォルト・上書き・実効値と、
    スケジューラに今載っている crontab（`applied_schedule`。載っていなければ `None`）を返す。
    実効値は保存済みの上書きを起動時・反映時と同じ規則で解釈したもので、型の不正・
    不正な crontab の上書きは `invalid` に挙げてデフォルトを使う。

    Args:
        name: 拡張名（ロード済みであること）。
        tools: ツールのレジストリ。
        doc: その拡張の上書きドキュメント。無ければ `None`。
        locale: `ui.locale`。
        applied_schedules: `task_handler.get_applied_schedules()` の結果。
        scheduler_running: スケジューラが起動済みか。
        scheduler_enabled: 設定（`scheduler.enabled`）上、内部スケジューラを使うか。
            偽なら画面は「起動時に効く」ではなく外部のスケジューラ向けの説明を出す。
    """
    from lilla_core.services.memory_manager import select_default_prompt

    doc = doc or {}
    prompt_section = None
    prompts = default_prompts_of(name)
    if prompts is not None:
        default_text = select_default_prompt(prompts, locale)
        default_locale = next(
            (lang for lang, text in prompts.items() if text == default_text), None
        )
        overridden = "prompt" in doc
        value = doc.get("prompt")
        invalid = overridden and not isinstance(value, str)
        effective = value if overridden and not invalid else default_text
        prompt_section = {
            "default": default_text,
            "default_locale": default_locale,
            "overridden": overridden,
            "override": value if overridden and not invalid else None,
            "invalid": invalid,
            "effective": effective,
        }

    entries = task_entries_of(name, tools)
    overrides = parse_task_overrides(name, doc, set(entries), warn=False)
    tasks = []
    for tool_name, info in entries.items():
        default_schedule = info["instance"].schedule
        resolved = resolve_task(tool_name, name, default_schedule, overrides.get(tool_name))
        override: dict = {}
        invalid_keys = set(resolved.override.invalid)
        if resolved.override.schedule is not None:
            if resolved.schedule_override_rejected:
                invalid_keys.add("schedule")
            else:
                override["schedule"] = resolved.override.schedule
        if resolved.override.enabled is not None:
            override["enabled"] = resolved.override.enabled
        invalid = [key for key in ("schedule", "enabled") if key in invalid_keys]
        schedule = override.get("schedule", default_schedule)
        enabled = resolved.enabled
        applied = applied_schedules.get(tool_name)
        expected = schedule if enabled else None
        tasks.append(
            {
                "name": tool_name,
                "default": {"schedule": default_schedule, "enabled": True},
                "override": override,
                "invalid": invalid,
                "effective": {"schedule": schedule, "enabled": enabled},
                "applied_schedule": applied,
                "in_sync": (not scheduler_running) or applied == expected,
            }
        )

    updated_at = doc.get("updated_at")
    return {
        "name": name,
        "locale": locale,
        "timezone": str(local_timezone()),
        "scheduler_running": scheduler_running,
        "scheduler_enabled": scheduler_enabled,
        "prompt": prompt_section,
        "tasks": tasks,
        "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else None,
    }


def parse_update(name: str, body: object, tools: dict) -> OverrideUpdate:
    """保存リクエストを検証し、リポジトリへ渡す更新内容に変える。

    リクエストの形は ``{"prompt": str | null, "tasks": {ツール名: {"schedule": str | null,
    "enabled": bool | null}}}`` で、どのキーも省略できる。省略したキーは変えず、`null` は
    上書きを消す（デフォルトへ戻す）。プロンプトの空文字は「注入しない」という有効な
    上書き。1 か所でも不正ならリクエスト全体を拒否する（部分的に保存しない）。

    Args:
        name: 拡張名（ロード済みであること）。
        body: 解釈済みの JSON ボディ。
        tools: ツールのレジストリ。

    Returns:
        検証済みの `OverrideUpdate`。

    Raises:
        OverrideValidationError: 形・型・未知のキー・対象外のタスク・不正な crontab・
            何も変えないリクエストのいずれか。
    """
    if not isinstance(body, dict):
        raise OverrideValidationError("Request body must be a JSON object")
    unknown = sorted(set(body) - _UPDATE_KEYS)
    if unknown:
        raise OverrideValidationError(f"Unknown keys: {', '.join(unknown)}")

    set_fields: dict[str, object] = {}
    unset_fields: list[str] = []
    task_names: list[str] = []

    if "prompt" in body:
        if default_prompts_of(name) is None:
            raise OverrideValidationError(f"Extension '{name}' has no default prompt to override")
        value = body["prompt"]
        if value is None:
            unset_fields.append("prompt")
        elif isinstance(value, str):
            set_fields["prompt"] = value
        else:
            raise OverrideValidationError("prompt must be a string or null")

    if "tasks" in body:
        tasks = body["tasks"]
        if not isinstance(tasks, dict):
            raise OverrideValidationError("tasks must be an object")
        targets = task_entries_of(name, tools)
        for tool_name, value in tasks.items():
            if tool_name not in targets:
                raise OverrideValidationError(
                    f"'{tool_name}' is not a scheduled task bundled by extension '{name}'"
                )
            if not isinstance(value, dict):
                raise OverrideValidationError(f"tasks.{tool_name} must be an object")
            unknown = sorted(set(value) - _TASK_KEYS)
            if unknown:
                raise OverrideValidationError(
                    f"Unknown keys in tasks.{tool_name}: {', '.join(unknown)}"
                )
            if not value:
                continue
            base = f"tasks.{tool_name}"
            if "schedule" in value:
                schedule = value["schedule"]
                if schedule is None:
                    unset_fields.append(f"{base}.schedule")
                elif isinstance(schedule, str) and schedule.strip():
                    schedule = " ".join(schedule.split())
                    if not is_valid_crontab(schedule):
                        raise OverrideValidationError(
                            f"tasks.{tool_name}.schedule is not a valid crontab expression"
                        )
                    set_fields[f"{base}.schedule"] = schedule
                else:
                    raise OverrideValidationError(
                        f"tasks.{tool_name}.schedule must be a non-empty string or null"
                    )
            if "enabled" in value:
                enabled = value["enabled"]
                if enabled is None:
                    unset_fields.append(f"{base}.enabled")
                elif isinstance(enabled, bool):
                    set_fields[f"{base}.enabled"] = enabled
                else:
                    raise OverrideValidationError(
                        f"tasks.{tool_name}.enabled must be a boolean or null"
                    )
            task_names.append(tool_name)

    if not set_fields and not unset_fields:
        raise OverrideValidationError("Nothing to update")
    return OverrideUpdate(set_fields=set_fields, unset_fields=unset_fields, task_names=task_names)
