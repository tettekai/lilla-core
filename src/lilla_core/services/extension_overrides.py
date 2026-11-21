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

from lilla_core.core.extension import (
    Extension,
    extension_display_name,
    get_dashboard_pages,
    get_default_prompts,
    get_extensions,
    is_builtin_extension,
    is_official_extension,
)
from lilla_core.services.extension_prompt_override import (
    InvalidPromptOverrideError,
    effective_prompt_template,
    parse_prompt_override,
)
from lilla_core.services.prompt_template import (
    PromptTemplateError,
    check_syntax,
    public_field_names,
)
from lilla_core.services.task_schedules import (
    is_valid_crontab,
    parse_task_overrides,
    resolve_task,
    scheduled_task_entries,
)
from lilla_core.utils.datetime_utils import local_timezone

#: 設定画面（SPA）のハッシュ。標準機能は全般と同じ並びの `#/settings/{name}`、
#: それ以外は「拡張機能」の下の `#/settings/extensions/{name}` に置く。
SETTINGS_HASH = "#/settings"
SETTINGS_EXTENSIONS_HASH = "#/settings/extensions"

#: `list_editable()` の `kind`。標準機能・公式拡張パック・それ以外（外部の拡張）。
KIND_BUILTIN = "builtin"
KIND_OFFICIAL = "official"
KIND_EXTERNAL = "external"

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


def extension_kind(ext: Extension) -> str:
    """拡張の種類（`KIND_BUILTIN` / `KIND_OFFICIAL` / `KIND_EXTERNAL`）を返す。"""
    if is_builtin_extension(ext):
        return KIND_BUILTIN
    if is_official_extension(ext):
        return KIND_OFFICIAL
    return KIND_EXTERNAL


def settings_hash_of(ext: Extension) -> str:
    """設定画面でその拡張を開くハッシュを返す。"""
    if is_builtin_extension(ext):
        return f"{SETTINGS_HASH}/{ext.name}"
    return f"{SETTINGS_EXTENSIONS_HASH}/{ext.name}"


def list_editable(tools: dict) -> list[dict]:
    """ロード済みの拡張を、設定画面に出す情報と編集できるかの目印つきでロード順に返す。

    編集する項目が無い拡張も含めて全件を返す（設定画面の一覧はすべての拡張を並べ、
    項目が無いものは「設定する項目はありません」と出すため）。

    Args:
        tools: ツールのレジストリ。

    Returns:
        `name` / `display_name` / `kind` / `has_prompt` / `task_count` / `has_settings` /
        `editable` / `settings_hash` / `page_hash` を持つ dict のリスト。
        `has_settings` は画面管理の設定（`settings_model()`）を宣言しているか、
        `page_hash` はダッシュボードに画面を出していればそのハッシュ（無ければ `None`）。
    """
    from lilla_core.core.extension import get_settings_models

    settings_models = get_settings_models()
    page_hashes = {entry.name: entry.hash for entry in get_dashboard_pages()}
    items = []
    for ext in get_extensions():
        has_prompt = default_prompts_of(ext.name) is not None
        task_count = len(task_entries_of(ext.name, tools))
        has_settings = ext.name in settings_models
        items.append(
            {
                "name": ext.name,
                "display_name": extension_display_name(ext),
                "kind": extension_kind(ext),
                "has_prompt": has_prompt,
                "task_count": task_count,
                "has_settings": has_settings,
                "editable": has_prompt or task_count > 0 or has_settings,
                "settings_hash": settings_hash_of(ext),
                "page_hash": page_hashes.get(ext.name),
            }
        )
    return items


def _template_variable_names(name: str) -> list[str]:
    """プロンプトのテンプレートから `settings.名前` で使える名前の一覧（設定が無ければ空）。"""
    from lilla_core.services.extension_settings import get_settings_model

    model = get_settings_model(name)
    if model is None:
        return []
    return public_field_names(model)


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

    プロンプトはデフォルト（`ui.locale` → `ja` → `en` → 先頭の言語）・上書きの `mode` /
    `text`・実効値（`effective`。既定と追加分を展開前のまま空行区切りでつないだ表示用の本文で、
    合成の規則は `services/extension_prompt_override.py`）を、
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
        override = None
        invalid = False
        if overridden:
            try:
                override = parse_prompt_override(doc.get("prompt"))
            except InvalidPromptOverrideError:
                invalid = True
        prompt_section = {
            "default": default_text,
            "default_locale": default_locale,
            "overridden": overridden,
            "mode": override.mode if override is not None else None,
            "text": override.text if override is not None else None,
            "invalid": invalid,
            "effective": effective_prompt_template(default_text, override),
            "template_variables": _template_variable_names(name),
        }

    entries = task_entries_of(name, tools)
    overrides = parse_task_overrides(name, doc, set(entries), warn=False)
    tasks = []
    for tool_name, info in entries.items():
        default_schedule = info["instance"].schedule
        file_overrides = info.get("file_overrides", frozenset())
        resolved = resolve_task(
            tool_name, name, default_schedule, overrides.get(tool_name), file_overrides
        )
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
                "file_locked": [key for key in ("schedule", "enabled") if key in file_overrides],
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

    リクエストの形は ``{"prompt": {"mode": "append" | "replace", "text": str} | null,
    "tasks": {ツール名: {"schedule": str | null, "enabled": bool | null}}}`` で、どのキーも
    省略できる。省略したキーは変えず、`null` は上書きを消す（デフォルトへ戻す）。
    プロンプトのモードと本文は 1 つのフィールドとして同時に保存する。`replace` の空文字は
    「注入しない」という有効な上書き。構文チェックは入力本文（`text`）だけが対象。1 か所でも不正ならリクエスト全体を拒否する（部分的に保存しない）。

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
        else:
            try:
                override = parse_prompt_override(value)
            except InvalidPromptOverrideError as e:
                raise OverrideValidationError(str(e)) from e
            try:
                check_syntax(override.text)
            except PromptTemplateError as e:
                raise OverrideValidationError(f"prompt: {e}") from e
            set_fields["prompt"] = override.to_document()

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
            locked = sorted(
                key
                for key in set(value) & set(targets[tool_name].get("file_overrides", ()))
                if value[key] is not None
            )
            if locked:
                raise OverrideValidationError(
                    f"tasks.{tool_name}: {', '.join(locked)} is set by a tool config override "
                    "file and cannot be changed from the dashboard"
                )
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
