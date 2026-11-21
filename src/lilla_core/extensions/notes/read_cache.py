"""`llm_notes_read` のツール結果のキャッシュ（`tool_cache`）。

FrontMatter に `lilla-cache-expiration-minutes`（正の整数・分）を持つノートだけを、パスを
キーにキャッシュする（lilla-agent の Obsidian 読み取りツールと同じ挙動）。保存はローダーの
`cache.mode: auto` が行い、ここでは保存する内容（`cache_writes` の 1 件）を組み立てるのと、
書き込みのあとに変わったノートのキャッシュを消すことだけを持つ。
"""
from __future__ import annotations

import logging
from typing import Any

import yaml

logger = logging.getLogger(__name__)

READ_TOOL_NAME = "llm_notes_read"
"""キャッシュを保存するときの `tool_name`（同梱のツール YAML の stem）。"""

CACHE_EXPIRATION_KEY = "lilla-cache-expiration-minutes"
"""キャッシュの有効期限（分）を書く FrontMatter のキー。"""

_DELIMITER = "---"


def split_front_matter(text: str) -> tuple[Any, str] | None:
    """先頭の FrontMatter（`---` の行で挟んだ YAML）と、それを除いた本文を返す。

    1 行目が `---` でない、または閉じの `---` の行が無ければ FrontMatter は無いとして None を返す。
    本文は前後の空白を除く。YAML として読めなければ `yaml.YAMLError` をそのまま投げる。
    """
    lines = text.split("\n")
    if lines[0].rstrip() != _DELIMITER:
        return None
    for index in range(1, len(lines)):
        if lines[index].rstrip() == _DELIMITER:
            metadata = yaml.safe_load("\n".join(lines[1:index]))
            body = "\n".join(lines[index + 1:]).strip()
            return metadata, body
    return None


def build_cache_write(path: str, text: str) -> dict | None:
    """ノートの本文から `cache_writes` の 1 件（`{key, data, expiration_minutes}`）を作る。

    FrontMatter・キーが無いノートは黙って None を返す（キャッシュしないのが普通のノートのため）。
    FrontMatter を読めない・値が正の整数（bool は除く）でない場合は WARNING を出して None を返す。
    `data` は FrontMatter を除いた本文の全文（`offset` / `limit` で切り詰める前）。
    """
    try:
        parsed = split_front_matter(text)
    except yaml.YAMLError as e:
        logger.warning("Failed to parse note front matter (path=%s): %s", path, e)
        return None
    if parsed is None:
        return None
    metadata, body = parsed
    if metadata is None:
        return None
    if not isinstance(metadata, dict):
        logger.warning("Note front matter is not a mapping (path=%s)", path)
        return None
    if CACHE_EXPIRATION_KEY not in metadata:
        return None
    value = metadata[CACHE_EXPIRATION_KEY]
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        logger.warning(
            "%s must be a positive integer (path=%s): %r", CACHE_EXPIRATION_KEY, path, value
        )
        return None
    return {"key": path, "data": body, "expiration_minutes": value}


async def invalidate(paths: list[str]) -> None:
    """渡したパスの `llm_notes_read` のキャッシュを消す。

    失敗は WARNING ログだけで例外は投げない（書き込みの結果は変えない）。
    """
    if not paths:
        return
    try:
        from lilla_core.repository.tool_cache_repository import get_tool_cache_repo

        repo = get_tool_cache_repo()
    except Exception as e:  # キャッシュを消せなくても書き込みは成功させる
        logger.warning("Failed to open the tool cache to invalidate notes: %s", e)
        return
    for path in paths:
        try:
            await repo.delete(READ_TOOL_NAME, path)
        except Exception as e:  # 1 件の失敗で残りを止めない
            logger.warning("Failed to invalidate the notes read cache (path=%s): %s", path, e)
