"""LLM へ送る直前の最後の網（拒否リストによる送信の停止）。

`llm.send_blocklist_path` に JSON の文字列配列ファイルの絶対パスを書いたときだけ有効になる。
有効なあいだは、`api/llm_client.py` が `send_http_request` へ渡す `data`（JSON 化される
ボディ）に含まれる文字列を役割によらずすべて集め、NFKC 正規化 + 大文字小文字の無視で
拒否リストの語に部分一致したらその回の送信を止める（メールアドレスなども、止めたいものは
リストに書く。形での推測はしない）。

ヘッダー（`Authorization` など）は対象外で、data URL の base64 本体（画像など、文字列に
ならない部分）も対象外。言い換えや画像内の文字は扱わない。

リストは起動時（`bot.py`）に一度だけ読んでプロセスのメモリに持つ。読めない・JSON でない・
文字列配列でない場合は、設定の不備と同じく起動を止める。`bot.py` を通らずに LLM を呼ぶ
プロセス（運用スクリプトなど）では最初の送信時に読み、失敗したら送らない。リストの中身・一致した語・リクエスト本文は、ログにも例外メッセージにも出さない。
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from lilla_core.core.config import get_config
from lilla_core.core.exceptions import LlmSendBlockedError, LlmSendGuardUnavailableError

logger = logging.getLogger(__name__)

# 送信を止めたときの例外メッセージ（固定文言。一致した語や本文を載せない）
BLOCKED_MESSAGE = "LLM request was not sent because it may contain personal information"
UNAVAILABLE_MESSAGE = "LLM send blocklist could not be loaded"

# 画像などを埋め込んだ data URL（base64）。文字列にならない部分として検査から外す
_DATA_URL_PATTERN = re.compile(r"^data:[^,]*;base64,", re.IGNORECASE)


@dataclass(frozen=True)
class _LoadedBlocklist:
    """読み込みに成功した拒否リスト（正規化済みの語）。"""

    terms: tuple[str, ...]


# パス → 読み込み済みの拒否リスト。起動時に一度だけ埋め、以後は読み直さない
_cache: dict[str, _LoadedBlocklist] = {}


def normalize_text(text: str) -> str:
    """比較用に文字列を正規化する（NFKC + 大文字小文字の無視）。"""
    return unicodedata.normalize("NFKC", text).casefold()


def _read_blocklist(path: str) -> _LoadedBlocklist:
    """拒否リストファイルを読み、正規化済みの語の集合を返す。

    JSON の文字列配列だけを受け付け、空文字（正規化後）の語は全文一致になるため不正とする。
    失敗時は ValueError / OSError を送出する。メッセージにファイルの中身は含めない。
    """
    raw = Path(path).read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
        raise ValueError("send blocklist must be a JSON array of strings")
    terms = tuple(normalize_text(item) for item in data)
    if any(not term for term in terms):
        raise ValueError("send blocklist must not contain empty strings")
    return _LoadedBlocklist(terms=terms)


def _load(path: str) -> _LoadedBlocklist:
    """パスごとの拒否リストを返す。未読み込みならここで一度だけ読む。

    失敗時は `LlmSendGuardUnavailableError` を送出する（失敗は覚えないため、次の呼び出しで
    読み直す）。元の例外メッセージは JSON の該当箇所などを含みうるため、種類だけを出す。
    """
    cached = _cache.get(path)
    if cached is not None:
        return cached
    try:
        result = _read_blocklist(path)
    except Exception as e:
        logger.error("Failed to load LLM send blocklist at %s (%s)", path, type(e).__name__)
        raise LlmSendGuardUnavailableError(
            f"{UNAVAILABLE_MESSAGE} (path: {path}, error: {type(e).__name__})"
        ) from None
    logger.info("Loaded LLM send blocklist (%d terms)", len(result.terms))
    _cache[path] = result
    return result


def preload_llm_send_blocklist(config=None) -> None:
    """起動時に拒否リストを一度だけ読み込む（未指定なら何もしない）。

    読めない・形式が不正な場合は `LlmSendGuardUnavailableError` を送出し、
    呼び出し元（`bot.py`）の起動を止める。

    Args:
        config: 参照する設定。省略時は `get_config()`。
    """
    path = (config or get_config()).llm.send_blocklist_path
    if path is not None:
        _load(path)


def clear_cache() -> None:
    """読み込み済みの拒否リストを捨てる（テスト用）。"""
    _cache.clear()


def _iter_strings(value: Any) -> Iterator[str]:
    """JSON 化される値に含まれる文字列（辞書のキーを含む）をすべて列挙する。"""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str):
                yield key
            yield from _iter_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_strings(item)


def _matches(data: Any, terms: tuple[str, ...]) -> bool:
    """ボディ内の文字列のどれかが拒否リストの語を含むかを返す（どの語かは返さない）。"""
    if not terms:
        return False
    for text in _iter_strings(data):
        if _DATA_URL_PATTERN.match(text):
            continue
        normalized = normalize_text(text)
        if any(term in normalized for term in terms):
            return True
    return False


def ensure_llm_request_allowed(data: Any) -> None:
    """LLM へ送るリクエストボディを検査し、送ってはいけなければ例外で止める。

    `llm.send_blocklist_path` が未指定なら何もしない。

    Args:
        data: `send_http_request` の `data` に渡すボディ（JSON 化前）。

    Raises:
        LlmSendGuardUnavailableError: 拒否リストが指定されているのに使えない場合。
        LlmSendBlockedError: 拒否リストの語が含まれる場合。
    """
    path = get_config().llm.send_blocklist_path
    if path is None:
        return
    loaded = _load(path)
    if _matches(data, loaded.terms):
        logger.warning("LLM request was not sent: request body matched the send blocklist")
        raise LlmSendBlockedError(BLOCKED_MESSAGE)
