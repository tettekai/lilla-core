"""日記テンプレの穴（`<<name>>`）の書き方の規則。

テンプレ本文の保存時の検証（`DiarySettings`）と、ドラフトの組み立て・検証
（`diary.service`）が同じ規則を使うためにここへ置く。

`{{...}}` は穴ではなくただの文字列として扱う（穴の書き方を `<<name>>` にしているのは、
Jinja2 の記法とぶつからないようにするため）。
"""
from __future__ import annotations

import re

#: テンプレの穴。`<< name >>` のように前後の空白は許し、名前には空白・`<` / `>` を含めない。
HOLE_RE = re.compile(r"<<\s*([^<>\s]*)\s*>>")


def check_template(text: str) -> None:
    """テンプレ本文が穴の規則で読めることを確かめる（本文は変えない）。

    Raises:
        ValueError: 名前の無い穴（`<<>>`）、または穴として読めない `<<` / `>>`
            （名前に空白を含む・閉じ忘れなど）があるとき。
    """
    for m in HOLE_RE.finditer(text):
        if not m.group(1):
            raise ValueError("template has a hole without a name: <<>>")
    rest = HOLE_RE.sub("", text)
    for token in ("<<", ">>"):
        if token in rest:
            line = next(line for line in rest.splitlines() if token in line)
            raise ValueError(
                f"template has '{token}' that is not part of a <<name>> hole "
                f"(names cannot contain spaces): {line.strip()[:80]!r}"
            )
