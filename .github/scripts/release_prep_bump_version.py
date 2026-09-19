"""release-prep ワークフロー専用スクリプト。

CHANGELOG.md の Unreleased 見出しを ``VERSION`` 環境変数のバージョン見出しへ移し、
pyproject.toml の version を同じバージョンへ更新する。
"""

import datetime
import os
import pathlib
import re

VERSION = os.environ["VERSION"]
TODAY = datetime.date.today().isoformat()


def bump_changelog() -> None:
    """CHANGELOG.md の Unreleased をバージョン見出しへ移す。"""
    changelog = pathlib.Path("CHANGELOG.md")
    text = changelog.read_text()
    marker = "## [Unreleased]"
    if marker not in text:
        raise SystemExit("CHANGELOG.md に '## [Unreleased]' が見つかりません")
    text = text.replace(marker, f"{marker}\n\n## [{VERSION}] - {TODAY}", 1)
    changelog.write_text(text)


def bump_pyproject() -> None:
    """pyproject.toml の version を更新する。"""
    pyproject = pathlib.Path("pyproject.toml")
    content = pyproject.read_text()
    new_content, count = re.subn(
        r'^version = "[0-9]+\.[0-9]+\.[0-9]+"$',
        f'version = "{VERSION}"',
        content,
        count=1,
        flags=re.MULTILINE,
    )
    if count == 0:
        raise SystemExit("pyproject.toml に version 行が見つかりません")
    pyproject.write_text(new_content)


if __name__ == "__main__":
    bump_changelog()
    bump_pyproject()
