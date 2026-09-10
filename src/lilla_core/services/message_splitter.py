"""メッセージ分割ユーティリティ。"""

import re
import unicodedata

from lilla_core.services.message_util import strip_timestamp_prefix

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


def split_response(text: str) -> list[str]:
    """LLM応答を複数の区切りパターンで分割する。"""
    if not text or not text.strip():
        return []

    # すべての区切りパターンで分割
    parts = _SPLIT_REGEX.split(text)

    result = []
    for part in parts:
        stripped = part.strip()
        if not stripped:
            continue
        if not _is_meaningful_block(stripped):
            continue

        # タイムスタンプ除去
        cleaned = strip_timestamp_prefix(stripped)
        result.append(cleaned)

    return result