"""services/skills.py のテスト。"""
from __future__ import annotations

import pytest

from lilla_core.services.skills import (
    SKILL_NAME_MAX_LENGTH,
    format_skill_instruction,
    normalize_skill_name,
)
from lilla_core.ui import messages

_JA_WRAPPER = (
    "[システム指示: スキル「proofread」]\n"
    "以下はユーザーの発言ではなく、システムからの動作指示です。この指示に従って応答してください。\n"
    "\n"
    "{body}\n"
    "[/システム指示]"
)
_EN_WRAPPER = (
    '[System instruction: skill "proofread"]\n'
    "The following is not something the user said; it is a behavior instruction from the system. "
    "Follow this instruction when responding.\n"
    "\n"
    "{body}\n"
    "[/System instruction]"
)


@pytest.fixture
def locale(monkeypatch: pytest.MonkeyPatch):
    """`t()` が使うロケールを差し替える関数を返す。"""

    def _set(value: str) -> None:
        monkeypatch.setattr(messages, "current_locale", lambda: value)

    return _set


class TestNormalizeSkillName:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("review", "review"),
            ("Review", "review"),
            ("CODE-review_2", "code-review_2"),
            ("a" * SKILL_NAME_MAX_LENGTH, "a" * SKILL_NAME_MAX_LENGTH),
        ],
    )
    def test_accepts_and_lowercases(self, name: str, expected: str) -> None:
        """規則に合う名前は小文字にして返す。"""
        assert normalize_skill_name(name) == expected

    @pytest.mark.parametrize(
        "name",
        ["", " review", "review ", "two words", "日本語", "dot.name", "a" * 65, "a/b", None, 1],
    )
    def test_rejects(self, name) -> None:
        """空・空白入り・記号・全角・長すぎ・文字列以外は None。"""
        assert normalize_skill_name(name) is None


class TestFormatSkillInstruction:
    def test_wraps_prompt_without_extra(self, locale) -> None:
        """追加指示を省略するとラッパーだけを返す。"""
        locale("ja")
        result = format_skill_instruction("proofread", "次の文章を校正して")
        assert result == _JA_WRAPPER.format(body="次の文章を校正して")

    def test_appends_labeled_extra(self, locale) -> None:
        """追加指示は前後の空白を除き、空行を挟んでラベル付きで続ける。"""
        locale("ja")
        result = format_skill_instruction("proofread", "次の文章を校正して", "  今日は良い天気でした。  ")
        assert result == (
            _JA_WRAPPER.format(body="次の文章を校正して") + "\n\n追加指示: 今日は良い天気でした。"
        )

    @pytest.mark.parametrize("extra", ["", "   ", " \n\t ", None])
    def test_blank_extra_is_omitted(self, locale, extra) -> None:
        """空・空白だけの追加指示は行ごと付けない。"""
        locale("ja")
        result = format_skill_instruction("proofread", "本文", extra)
        assert result == _JA_WRAPPER.format(body="本文")

    def test_multiline_prompt_and_extra(self, locale) -> None:
        """複数行の本文はそのまま入れ、複数行の追加指示は 1 行目にだけラベルを付ける。"""
        locale("ja")
        result = format_skill_instruction("proofread", "\n1行目\n\n2行目\n", "A\nB\nC")
        assert result == _JA_WRAPPER.format(body="1行目\n\n2行目") + "\n\n追加指示: A\nB\nC"

    def test_braces_in_extra_are_kept(self, locale) -> None:
        """追加指示の `{}` は書式として解釈せずそのまま残す。"""
        locale("ja")
        result = format_skill_instruction("proofread", "本文", "{name} {0}")
        assert result.endswith("追加指示: {name} {0}")

    def test_english_locale(self, locale) -> None:
        """`en` では英語のラッパーとラベルを使う。"""
        locale("en")
        result = format_skill_instruction("proofread", "次の文章を校正して", "今日は良い天気でした。")
        assert result == (
            _EN_WRAPPER.format(body="次の文章を校正して")
            + "\n\nAdditional instructions: 今日は良い天気でした。"
        )
