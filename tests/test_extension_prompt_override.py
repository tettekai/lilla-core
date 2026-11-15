"""`services/extension_prompt_override.py`（拡張のプロンプトの上書きの解釈と合成）のテスト。"""
from __future__ import annotations

import pytest

from lilla_core.services.extension_prompt_override import (
    InvalidPromptOverrideError,
    PromptOverride,
    compose_extension_prompt,
    effective_prompt_template,
    parse_prompt_override,
    prompt_parts,
)


class TestParsePromptOverride:
    """保存値・リクエストの値の検証。"""

    @pytest.mark.parametrize("mode", ["append", "replace"])
    def test_accepts_new_format(self, mode) -> None:
        """`{"mode", "text"}` を受け付け、保存用の形へ戻せる。"""
        override = parse_prompt_override({"mode": mode, "text": ""})
        assert override == PromptOverride(mode=mode, text="")
        assert override.to_document() == {"mode": mode, "text": ""}

    @pytest.mark.parametrize(
        "value",
        [
            "旧形式",
            None,
            1,
            ["append", "x"],
            {},
            {"mode": "append"},
            {"text": "x"},
            {"mode": "prepend", "text": "x"},
            {"mode": None, "text": "x"},
            {"mode": "append", "text": 1},
            {"mode": "append", "text": "x", "extra": True},
        ],
    )
    def test_rejects_other_shapes(self, value) -> None:
        """旧形式の文字列・キーの過不足・未知のモード・型の違いは拒否する。"""
        with pytest.raises(InvalidPromptOverrideError):
            parse_prompt_override(value)


class TestPromptParts:
    """展開する部分の選び方と、表示用の実効値。"""

    def test_no_override_uses_default(self) -> None:
        """上書きが無ければ既定だけ。"""
        assert prompt_parts("既定", None) == ["既定"]
        assert effective_prompt_template("既定", None) == "既定"

    def test_append_keeps_default_then_text(self) -> None:
        """append は既定、追加分の順。表示用は展開前のまま空行でつなぐ。"""
        override = PromptOverride("append", "{{ x }}")
        assert prompt_parts("{% set x = 1 %}既定", override) == ["{% set x = 1 %}既定", "{{ x }}"]
        assert effective_prompt_template("既定", override) == "既定\n\n{{ x }}"

    @pytest.mark.parametrize(
        ("default", "text", "expected"),
        [("既定", "", "既定"), ("既定", " \n", "既定"), (" ", "追加", "追加"), ("", "", "")],
    )
    def test_append_drops_blank_parts(self, default, text, expected) -> None:
        """空白だけの部分はつながない（余分な区切りを足さない）。"""
        assert effective_prompt_template(default, PromptOverride("append", text)) == expected

    def test_replace_uses_text_only(self) -> None:
        """replace は入力本文だけ。空文字は注入しない。"""
        assert effective_prompt_template("既定", PromptOverride("replace", "置換")) == "置換"
        assert prompt_parts("既定", PromptOverride("replace", "")) == []


class TestComposeExtensionPrompt:
    """部分ごとに展開してからつなぐ合成。"""

    async def test_renders_each_part_independently(self) -> None:
        """展開関数には部分のリストを渡し、結果のうち空白だけのものは外してつなぐ。"""
        calls = []

        async def render(parts: list[str]) -> list[str]:
            calls.append(list(parts))
            return [part.upper() if part != "blank" else "  " for part in parts]

        result = await compose_extension_prompt("a", PromptOverride("append", "b"), render)
        assert result == "A\n\nB"
        assert calls == [["a", "b"]]

        result = await compose_extension_prompt("a", PromptOverride("append", "blank"), render)
        assert result == "A"

    async def test_nothing_to_render(self) -> None:
        """部分が無ければ展開関数を呼ばずに空文字を返す。"""

        async def render(parts: list[str]) -> list[str]:
            raise AssertionError("must not be called")

        assert await compose_extension_prompt("既定", PromptOverride("replace", ""), render) == ""
