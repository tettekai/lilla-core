"""LLM が会話の流れから必要なスキルを自分で読み込むためのコア組み込み LLM ツール。

`${CONFIG_ROOT}/tools/` に `type: llm_load_skill` の YAML を置き、`tools.sets`（`main`
など）に含めた場合だけ LLM から使える（コアは自動では読み込まない。import パス形式の
`type: lilla_core.builtin_tools.llm_load_skill` も使える）。

成功時は `!skill` と同じ `services/skills.py` の `format_skill_instruction`（追加指示なし）で
包んだ本文をツール結果として返す。結果はキャッシュせず、毎回リポジトリから読む。

このツールが LLM に渡される会話では、`services/conversation_service.py` が
`build_skill_list_section()` で有効なスキルの名前と説明の一覧をシステムプロンプトへ足す。
一覧の上限（`max_skills` / `max_description_chars` / `max_list_chars`）はツール YAML の
固有キーで変えられ、不正な値は `build_schema()`（ツールのロード時）で起動を落とす。
"""
from __future__ import annotations

import logging

from lilla_core.services.skills import format_skill_instruction, normalize_skill_name
from lilla_core.tool_support.tool_result import tool_error, tool_success
from lilla_core.ui.messages import t

logger = logging.getLogger(__name__)

TOOL_NAME = "llm_load_skill"

#: ツール YAML の `type` としてこのツールを指す書き方（ファイル名と import パス）
TOOL_TYPES = frozenset({TOOL_NAME, f"lilla_core.builtin_tools.{TOOL_NAME}"})

#: 一覧の上限の既定値（ツール YAML の固有キー → 既定値）
DEFAULT_LIMITS = {
    "max_skills": 50,
    "max_description_chars": 200,
    "max_list_chars": 4000,
}

SCHEMA = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "名前を指定してスキル（登録済みの動作指示）の本文を読み込みます。"
            "システムプロンプトのスキル一覧を見て、必要と判断したときだけ呼び出してください。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "読み込むスキル名（大文字小文字は区別しない）。",
                },
                "reason": {
                    "type": "string",
                    "description": "スキルを読み込む理由や、読み込んだ後に行う予定の内容（任意）。",
                },
            },
            "required": ["name"],
        },
    },
}


def resolve_limits(config: dict | None) -> dict[str, int]:
    """ツール YAML から一覧の上限を取り出し、既定値で補って返す。

    Args:
        config: ツール YAML の内容（`tool_config`）。

    Returns:
        `DEFAULT_LIMITS` と同じキーを持つ上限の辞書。

    Raises:
        ValueError: 上限が正の整数でない（0 以下・数値でない・bool）場合。
    """
    config = config or {}
    limits: dict[str, int] = {}
    for key, default in DEFAULT_LIMITS.items():
        value = config.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(
                f"Tool '{TOOL_NAME}' option '{key}' must be a positive integer: {value!r}"
            )
        limits[key] = value
    return limits


def build_schema(config: dict) -> dict:
    """ツールのロード時に上限の設定を検証し、SCHEMA を返す。

    ローダーはロード時に `build_schema` を呼ぶため、ここで検証すると不正な上限は
    起動時に落ちる。

    Args:
        config: ツール YAML の内容。

    Returns:
        LLM に渡す SCHEMA。

    Raises:
        ValueError: 上限の値が不正な場合。
    """
    resolve_limits(config)
    return SCHEMA


def is_load_skill_tool(entry: dict) -> bool:
    """ツールレジストリのエントリがこのツールを指しているかを返す。

    LLM へ見せる名前は YAML の stem で変わるため、YAML の `type` で判定する。
    """
    return (entry.get("tool_config") or {}).get("type") in TOOL_TYPES


def _format_list_line(skill: dict, max_description_chars: int) -> str:
    """一覧の 1 行（`- 名前: 説明`。説明が空なら名前だけ）を組み立てる。

    説明の改行は空白に置き換え、`max_description_chars` を超えたら切り詰めて `…` を付ける。
    """
    name = skill.get("name", "")
    description = " ".join((skill.get("description") or "").splitlines()).strip()
    if len(description) > max_description_chars:
        description = description[:max_description_chars] + "…"
    if description:
        return f"- {name}: {description}"
    return f"- {name}"


def format_skill_list_section(
    skills: list[dict], tool_name: str, limits: dict[str, int]
) -> str:
    """有効なスキルの一覧からシステムプロンプトへ足す節を組み立てる。

    名前順に並んだ前提で先頭から行を足し、件数（`max_skills`）か各行の合計文字数
    （`max_list_chars`。見出しと説明文・省略案内は数えない）のどちらかの上限に
    達した時点で以降を載せない。省略した件数があれば末尾に案内を 1 行付ける。

    Args:
        skills: 有効なスキル（名前順）。
        tool_name: LLM へ見せているこのツールの名前（YAML の stem）。
        limits: `resolve_limits()` の戻り値。

    Returns:
        節の文字列。スキルが 0 件なら空文字列。
    """
    if not skills:
        return ""
    lines: list[str] = []
    total_chars = 0
    for skill in skills:
        if len(lines) >= limits["max_skills"]:
            break
        line = _format_list_line(skill, limits["max_description_chars"])
        if total_chars + len(line) > limits["max_list_chars"]:
            break
        lines.append(line)
        total_chars += len(line)

    section = [
        t("tool.load_skill.list_header"),
        t("tool.load_skill.list_intro", tool=tool_name),
        *lines,
    ]
    omitted = len(skills) - len(lines)
    if omitted > 0:
        section.append(t("tool.load_skill.list_truncated", count=omitted, tool=tool_name))
    return "\n".join(section)


async def build_skill_list_section(tool_name: str, tool_config: dict | None) -> str:
    """有効なスキルをリポジトリから取得して、システムプロンプトへ足す節を返す。

    キャッシュせず、呼ぶたびに取得する。取得に失敗した場合は WARNING ログを出して
    空文字列を返す（一覧が出せないだけで会話は止めない）。

    Args:
        tool_name: LLM へ見せているこのツールの名前（YAML の stem）。
        tool_config: このツールの YAML の内容（上限の設定を読む）。

    Returns:
        節の文字列。有効なスキルが無い・取得に失敗した場合は空文字列。
    """
    limits = resolve_limits(tool_config)
    # 設定を読み込むリポジトリの生成をツールのロード時に走らせないため関数内 import にする。
    from lilla_core.repository.skill_repository import get_skill_repo

    try:
        skills = await get_skill_repo().get_enabled()
    except Exception as e:
        logger.warning("Failed to load enabled skills for the system prompt: %s", e)
        return ""
    return format_skill_list_section(skills, tool_name, limits)


async def execute(input: dict, context: dict) -> dict:
    """指定したスキルの本文を `format_skill_instruction`（追加指示なし）で包んで返す。

    `reason` は結果の本文に使わない（ツール呼び出しの引数としてループ内に残るだけ）。

    Args:
        input: ツール入力（`name` は必須、`reason` は任意）。
        context: ツール実行コンテキスト（未使用）。

    Returns:
        標準結果辞書。成功時は `data` に展開後の文字列、名前が規則に合わない・存在しない・
        無効な場合は `tool_error`。
    """
    raw_name = input.get("name")
    name = normalize_skill_name(raw_name)
    if name is None:
        message = t("tool.load_skill.not_found", name=raw_name)
        return tool_error(TOOL_NAME, message, message)

    from lilla_core.repository.skill_repository import get_skill_repo

    skill = await get_skill_repo().find_by_name(name)
    if skill is None:
        message = t("tool.load_skill.not_found", name=name)
        return tool_error(TOOL_NAME, message, message)
    if not skill.get("enabled", True):
        message = t("tool.load_skill.disabled", name=name)
        return tool_error(TOOL_NAME, message, message)

    logger.info("[SKILL] Loading skill '%s' via tool", name)
    content = format_skill_instruction(name, skill.get("prompt", ""))
    return tool_success(TOOL_NAME, t("tool.load_skill.loaded", name=name), content)
