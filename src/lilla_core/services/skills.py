"""スキル（名前付きの再利用プロンプト）の名前規則。

スキル名は `!skill <スキル名>` のコマンド引数として空白区切りで渡されるため、
半角英数字・アンダースコア・ハイフンだけを許し、大文字小文字は区別しない
（保存時も呼び出し時も小文字に正規化して照合する）。
"""
from __future__ import annotations

import re

#: スキル名の最大文字数
SKILL_NAME_MAX_LENGTH = 64

_SKILL_NAME_RE = re.compile(rf"^[A-Za-z0-9_-]{{1,{SKILL_NAME_MAX_LENGTH}}}$")


def normalize_skill_name(name) -> str | None:
    """スキル名を規則に照らして検証し、小文字に正規化して返す。

    前後の空白は取り除かない（空白を含む入力は規則違反として扱う）。

    Args:
        name: 検証する名前。文字列以外は規則違反として扱う。

    Returns:
        規則に合えば小文字にした名前、合わなければ None。
    """
    if not isinstance(name, str) or not _SKILL_NAME_RE.fullmatch(name):
        return None
    return name.lower()
