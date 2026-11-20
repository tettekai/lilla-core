"""拡張のプロンプトの上書き（`extension_overrides` の `prompt`）の解釈と、既定との合成。

上書きは ``{"mode": "append" | "replace", "text": 文字列}`` の形で保存する（既定と
合成した全文は保存しない）。合成の規則はここに 1 つだけ持ち、会話のシステムプロンプト
（`services/memory_manager.py`）とダッシュボードの表示（`services/extension_overrides.py`）が
同じ規則を使う。

- 上書きなし: 既定だけ
- `append`: 既定と追加分を、それぞれ独立したテンプレートとして展開してから空行区切りでつなぐ
  （つないだ本文を 1 つのテンプレートとして展開しない。そのため追加分から既定側の
  `{% set %}` やマクロは参照できない）
- `replace`: 入力本文だけ（空文字は「この拡張の本文を注入しない」）

空または空白だけの部分は展開せず、展開した結果が空白だけになった部分もつながない。
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

#: 追加（既定の後ろへ書き足す）。
MODE_APPEND = "append"
#: 置換（既定を使わず入力本文だけ）。
MODE_REPLACE = "replace"
#: 受け付けるモード。
PROMPT_OVERRIDE_MODES = (MODE_APPEND, MODE_REPLACE)
#: 上書き 1 つが持つキー（どちらも必須で、これ以外は持たない）。
PROMPT_OVERRIDE_KEYS = frozenset({"mode", "text"})
#: 部分どうしの区切り。
PART_SEPARATOR = "\n\n"


class InvalidPromptOverrideError(ValueError):
    """上書きの値が新形式（`{"mode", "text"}`）に合わないとき（英語の理由を持つ）。"""


@dataclass(frozen=True)
class PromptOverride:
    """検証済みのプロンプトの上書き。

    Attributes:
        mode: `append` か `replace`。
        text: 利用者が書いた本文（展開前）。
    """

    mode: str
    text: str

    def to_document(self) -> dict[str, str]:
        """MongoDB へ保存する形（`{"mode", "text"}`）にする。"""
        return {"mode": self.mode, "text": self.text}


def parse_prompt_override(value: object) -> PromptOverride:
    """保存値・リクエストの値を検証して `PromptOverride` にする。

    Args:
        value: `{"mode": "append" | "replace", "text": str}` の形であるべき値。

    Returns:
        検証済みの上書き。

    Raises:
        InvalidPromptOverrideError: オブジェクトでない（旧形式の文字列を含む）・キーの過不足・
            未知のモード・`text` が文字列でないとき。
    """
    if not isinstance(value, dict):
        raise InvalidPromptOverrideError(
            f"prompt must be an object with 'mode' and 'text', got {type(value).__name__}"
        )
    unknown = sorted(str(key) for key in set(value) - PROMPT_OVERRIDE_KEYS)
    if unknown:
        raise InvalidPromptOverrideError(f"Unknown keys in prompt: {', '.join(unknown)}")
    missing = sorted(PROMPT_OVERRIDE_KEYS - set(value))
    if missing:
        raise InvalidPromptOverrideError(f"Missing keys in prompt: {', '.join(missing)}")
    mode = value["mode"]
    if not isinstance(mode, str) or mode not in PROMPT_OVERRIDE_MODES:
        raise InvalidPromptOverrideError(
            f"prompt.mode must be one of {', '.join(PROMPT_OVERRIDE_MODES)}"
        )
    text = value["text"]
    if not isinstance(text, str):
        raise InvalidPromptOverrideError("prompt.text must be a string")
    return PromptOverride(mode=mode, text=text)


def prompt_parts(default_text: str, override: PromptOverride | None) -> list[str]:
    """展開する部分（展開前の本文）を、つなぐ順に返す。空白だけの部分は含めない。

    Args:
        default_text: その拡張の `default_prompt()` から選んだ既定の本文。
        override: 上書き。無ければ `None`。

    Returns:
        既定・追加分のうち、使う部分のリスト（0〜2 個）。
    """
    if override is None:
        parts = [default_text]
    elif override.mode == MODE_APPEND:
        parts = [default_text, override.text]
    else:
        parts = [override.text]
    return [part for part in parts if part.strip()]


def join_prompt_parts(parts: list[str]) -> str:
    """部分を空行区切りでつなぐ。空白だけの部分は外す（余分な区切りを足さない）。"""
    return PART_SEPARATOR.join(part for part in parts if part.strip())


def effective_prompt_template(default_text: str, override: PromptOverride | None) -> str:
    """展開前の部分をそのままつないだ本文（ダッシュボードの `effective` 表示用）。"""
    return join_prompt_parts(prompt_parts(default_text, override))


async def compose_extension_prompt(
    default_text: str,
    override: PromptOverride | None,
    render_parts: Callable[[list[str]], Awaitable[list[str]]],
) -> str:
    """既定と上書きから、注入する本文を組み立てる（部分ごとに展開してからつなぐ）。

    Args:
        default_text: その拡張の既定の本文（locale で選んだもの）。
        override: 上書き。無ければ `None`。
        render_parts: 部分のリストを受け取り、同じ順・同じ数の展開結果を返す関数。
            失敗した部分は展開前の本文を返す想定（例外は投げない）。

    Returns:
        注入する本文。注入するものが無ければ空文字列。
    """
    parts = prompt_parts(default_text, override)
    if not parts:
        return ""
    return join_prompt_parts(await render_parts(parts))
