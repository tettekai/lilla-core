"""services/skills.py のテスト。"""
from __future__ import annotations

import pytest

from lilla_core.services.skills import SKILL_NAME_MAX_LENGTH, normalize_skill_name


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
