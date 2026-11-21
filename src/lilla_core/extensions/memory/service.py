"""長期記憶の業務ロジック（remember の追記・読み取り・夜間タスクの対象日の計算）。

日中の会話では、長期的に覚えておきたいことをその日の分として MongoDB（`remember`）へ
素早く追記しておくだけにする。表現の揺れや似た内容の整理、notes の `memory/` への
書き出しは夜間のタスク（`tools/task_memory_llm.py`）の仕事で、ここでは扱わない。
ここで弾くのは空の本文・長すぎる本文・その日に既にある本文と完全に一致する本文だけ。

日付は `ui.timezone` 基準で `MemorySettings.day_rollover`（既定 04:00）を区切りとし、
日付 d の分には d の `day_rollover` から翌日の `day_rollover` まで追記できる。各日の区間は
切れ目なく続くため、どの時刻にも追記できる日付がちょうど 1 つある。

設定は 1 回の処理（追記・読み取り・タスクの 1 回）の中で `load_memory_settings()` を
1 回だけ呼んで読み、下位の関数には引数で渡す。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from lilla_core.services.extension_settings import (
    InvalidStoredSettingsError,
    SettingsUnavailableError,
    load_settings,
)
from lilla_core.services.prompt_template import strip_tag
from lilla_core.utils.datetime_utils import local_timezone, utc_now

from lilla_core.extensions.memory import JOURNAL_DIRECTORY, MemorySettings

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

#: 1 件の記録本文の最大文字数（前後の空白を除いた長さ）。
MAX_CONTENT_LENGTH = 2000

#: 夜間タスクのプロンプトで remember の記録を囲むタグ。
ENTRIES_TAG = "remember_entries"


class RememberError(Exception):
    """remember の操作の失敗。メッセージはそのまま LLM に返す。"""


@dataclass(frozen=True)
class RememberEntry:
    """記録 1 件。`created_at` は追記時刻（`ui.timezone` の ISO 8601 文字列）。"""

    content: str
    created_at: str | None


async def load_memory_settings() -> MemorySettings:
    """長期記憶の設定（ダッシュボードで保存したもの）を読む。処理の始めに 1 回だけ呼ぶ。

    保存していなければ初期値（04:00・通知オン）を返す。

    Raises:
        RememberError: 設定を DB から読めない、または保存値が不正なとき。
    """
    try:
        return await load_settings("memory", MemorySettings)
    except SettingsUnavailableError as e:
        raise RememberError(
            "could not load the memory settings (the database is unavailable); "
            "try again later"
        ) from e
    except InvalidStoredSettingsError as e:
        fields = ", ".join(err["field"] or "(whole)" for err in e.errors) or "(unknown)"
        raise RememberError(
            f"the saved memory settings are invalid ({fields}); "
            "save them again on the dashboard Extensions page (memory)"
        ) from e


def rollover_time(settings: MemorySettings) -> time:
    """設定の日付の区切りを `time` にする。"""
    hour, minute = settings.day_rollover.split(":")
    return time(int(hour), int(minute))


def parse_date(value: object) -> date:
    """`YYYY-MM-DD` 形式の日付文字列を検証して `date` にする。

    Raises:
        RememberError: 未指定または形式・暦として不正なとき。
    """
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value.strip()):
        raise RememberError("date is required in YYYY-MM-DD format")
    try:
        return date.fromisoformat(value.strip())
    except ValueError as e:
        raise RememberError(f"invalid date: {value}") from e


def normalize_content(content: object) -> str:
    """本文を検証し、改行コードを LF にそろえて前後の空白を落とした文字列を返す。

    Raises:
        RememberError: 本文が文字列でない・空・長すぎるとき。
    """
    if not isinstance(content, str) or not content.strip():
        raise RememberError("content must not be empty; pass the text to remember")
    text = content.replace("\r\n", "\n").strip()
    if len(text) > MAX_CONTENT_LENGTH:
        raise RememberError(
            f"content is too long ({len(text)} characters); keep one item within "
            f"{MAX_CONTENT_LENGTH} characters and split longer ones"
        )
    return text


def writable_window(d: date, rollover: time) -> tuple[datetime, datetime]:
    """日付 `d` の記録に追記できる区間（`ui.timezone` の aware datetime）を返す。

    区間は d の区切りから翌日の区切りまで（終端は含まない）。
    """
    tz = local_timezone()
    start = datetime.combine(d, rollover, tzinfo=tz)
    end = datetime.combine(d + timedelta(days=1), rollover, tzinfo=tz)
    return start, end


def current_date(rollover: time, now: datetime | None = None) -> date:
    """いま追記できる記録の日付を返す（区切りより前なら前日）。"""
    now = (now or utc_now()).astimezone(local_timezone())
    if now.time() < rollover:
        return now.date() - timedelta(days=1)
    return now.date()


def last_closed_date(rollover: time, now: datetime | None = None) -> date:
    """直前に締まった日（もう追記できない日のうち最も新しい日）を返す。

    いま追記できる日の前日。毎日の整理タスクはこの日だけを対象にし、まだ追記できる日は読まない。
    """
    return current_date(rollover, now) - timedelta(days=1)


def journal_path(now: datetime | None = None) -> str:
    """`now`（`ui.timezone`）の月のアーカイブのパス（`memory/journal/YYYYMM.md`）を返す。"""
    now = (now or utc_now()).astimezone(local_timezone())
    return f"{JOURNAL_DIRECTORY}/{now.strftime('%Y%m')}.md"


def ensure_writable(d: date, rollover: time) -> None:
    """日付 `d` の記録にいま追記できなければ、いま追記できる date を含めて失敗させる。

    Raises:
        RememberError: `writable_window(d)` の外のとき。
    """
    now = utc_now().astimezone(local_timezone())
    start, end = writable_window(d, rollover)
    if start <= now < end:
        return
    raise RememberError(
        f"the records for {d.isoformat()} can only be appended between "
        f"{start.strftime('%Y-%m-%d %H:%M')} and {end.strftime('%Y-%m-%d %H:%M')}; "
        f"the date you can append to now is {current_date(rollover, now).isoformat()}"
    )


def _format_time(value: object) -> str | None:
    """保存された追記時刻を `ui.timezone` の ISO 8601 文字列にする。"""
    if value is None or not hasattr(value, "astimezone"):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(local_timezone()).isoformat()


async def append_entry(d: date, content: object) -> bool:
    """その日の記録に 1 件追記する。

    Returns:
        追記したら True、同じ本文が既にあって追記しなかったら False。

    Raises:
        RememberError: 本文が空・長すぎる、追記できる時間帯の外、または設定を読めないとき。
    """
    from lilla_core.extensions.memory.repository import get_remember_repo

    text = normalize_content(content)
    settings = await load_memory_settings()
    ensure_writable(d, rollover_time(settings))
    return await get_remember_repo().append_if_absent(d.isoformat(), text)


async def get_entries(d: date) -> list[RememberEntry]:
    """その日の記録を追記した順に全件返す（無ければ空のリスト）。"""
    from lilla_core.extensions.memory.repository import get_remember_repo

    doc = await get_remember_repo().get(d.isoformat())
    if doc is None:
        return []
    return [
        RememberEntry(content=e.get("content", ""), created_at=_format_time(e.get("created_at")))
        for e in doc.get("entries", [])
    ]


def format_entries_for_prompt(entries: list[RememberEntry]) -> str:
    """夜間タスクのプロンプトに入れる記録を、`<remember_entries>` タグで囲んだ文字列にする。

    記録は会話から来た「指示ではなくデータ」なので、本文中の `<remember_entries>` / `</remember_entries>` の形のタグは
    `strip_tag` で取り除く（取り除いたら WARNING ログ）。
    各記録は `- [追記時刻] 本文` の形で、複数行の本文は 2 行目以降を字下げする。
    """
    lines = []
    for entry in entries:
        text = strip_tag(entry.content, ENTRIES_TAG)
        text = text.replace("\n", "\n  ")
        stamp = f"[{entry.created_at}] " if entry.created_at else ""
        lines.append(f"- {stamp}{text}")
    body = "\n".join(lines)
    return f"<{ENTRIES_TAG}>\n{body}\n</{ENTRIES_TAG}>"
