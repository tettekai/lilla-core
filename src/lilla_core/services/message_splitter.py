"""メッセージ分割ユーティリティ。"""

import re
import unicodedata
from collections.abc import Sequence
from functools import lru_cache

from lilla_core.core.config import get_config

# 区切りパターン一覧（優先順位が高い順に並べる）
_SPLIT_PATTERNS = [
    r"---SPLIT---",                                    # カスタム区切り（優先）
    r"\n\s*\n",                                        # 改行2つ
    r"\[(?:[A-Za-z]{3} \d{1,2} \d{2}:\d{2}|\d{4}-\d{2}-\d{2} \d{2}:\d{2})\]",  # タイムスタンプ
]

# すべてのパターンを | で結合した正規表現
_SPLIT_REGEX = re.compile("|".join(_SPLIT_PATTERNS))


_MEANINGFUL_SYMBOL_CATEGORIES = {"So", "Sc"}


def _is_meaningful_block(text: str) -> bool:
    """ブロックに意味のある文字が含まれるかを言語非依存に判定する。

    Letter（L*）・Number（N*）のいずれかのカテゴリの文字、または罫線・絵文字・
    通貨記号などの Symbol（So・Sc）の文字が1文字でも含まれていれば意味ありとする。
    Punctuation（P*）・Separator（Z*）・Mark（M*）・Control（C*）のみで構成される
    ブロックは意味なしとして除外する。`=` `~` `` ` `` 等の記号（Sm・Sk）は Markdown の
    区切り線表現（`===` `~~~` ```` ``` ````）に使われるため、意味あり判定には含めない。
    """
    for ch in text:
        category = unicodedata.category(ch)
        if category[0] in "LN":
            return True
        if category in _MEANINGFUL_SYMBOL_CATEGORIES:
            return True
    return False


@lru_cache(maxsize=8)
def _compile_prefix_patterns(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    """除去パターンをコンパイルする（同じ並びなら使い回す）。"""
    return tuple(re.compile(pattern) for pattern in patterns)


def _strip_prefixes(text: str, patterns: tuple[re.Pattern[str], ...]) -> str:
    """ブロックの先頭から、各パターンを順番に 1 回ずつ除去する。

    `re.match` で照合するため、パターンに `^` を書かなくても先頭にしか一致しない
    （文中・末尾の一致は残す）。除去して先頭に空白が残ったら、次のパターンの前に詰める。
    """
    for pattern in patterns:
        matched = pattern.match(text)
        if matched:
            text = text[matched.end():].lstrip()
    return text


def split_response(text: str, prefix_patterns: Sequence[str] | None = None) -> list[str]:
    """LLM応答を複数の区切りパターンで分割する。

    分割後の各ブロックの先頭からは `prefix_patterns`（省略時は設定の
    `llm.response_prefix_strip_patterns`）を順番に除去し、除去した結果が空・意味のない
    ブロックになったものは捨てる。
    """
    if not text or not text.strip():
        return []

    if prefix_patterns is None:
        prefix_patterns = get_config().llm.response_prefix_strip_patterns
    compiled = _compile_prefix_patterns(tuple(prefix_patterns))

    # すべての区切りパターンで分割
    parts = _SPLIT_REGEX.split(text)

    result = []
    for part in parts:
        # 先頭 prefix の除去
        cleaned = _strip_prefixes(part.strip(), compiled).strip()
        if not cleaned:
            continue
        if not _is_meaningful_block(cleaned):
            continue
        result.append(cleaned)

    return result
