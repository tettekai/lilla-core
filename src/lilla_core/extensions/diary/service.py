"""日記ドラフトのサービス。

日記の正本はノート側にあり、会話中はその日のドラフト（日記の Markdown そのもの）を
MongoDB に持って読み書きする。書き出し先はここでは知らない。ドラフトの雛形は
ダッシュボードで編集する設定（`DiarySettings.template`）のテンプレ本文で、穴は
`<<name>>` 形式:

- `<<date>>` … 初回作成時にプログラムが日付（曜日付き。例: `2026-09-28 (月)`）で埋める
- それ以外 … 会話で埋める穴。清書時に残っていれば `-` にする（`<<moodscore>>` は除く）

テンプレの穴以外の文字（見出し・frontmatter のキー・末尾など）は固定文で、更新時に
残っていることを検証する。見出しの一覧はコードに持たず、テンプレから取る。

日付は `ui.timezone` 基準で `DiarySettings.day_rollover`（既定 04:00）を区切りとし、
日記日 d の分には d の `day_rollover` から翌日の `day_rollover` まで書き込める。各日の
区間は切れ目なく続くため、どの時刻にも書き込める日記日がちょうど 1 つある。

設定は 1 回の処理（get / set）の中で `load_diary_settings()` を 1 回だけ呼んで読み、
その処理の間は同じスナップショットを使う（下位の関数は設定を引数で受け取り、
自分では読み直さない）。保存した設定は次の処理から効き、実行中の処理は変わらない。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from lilla_core.services.extension_settings import (
    InvalidStoredSettingsError,
    SettingsUnavailableError,
    load_settings,
)
from lilla_core.utils.datetime_utils import local_timezone, utc_now

from lilla_core.extensions.diary import DiarySettings
from lilla_core.extensions.diary.template_rules import HOLE_RE as _HOLE_RE
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_WEEKDAYS = "月火水木金土日"

#: 清書時に推測して埋めない穴の名前。
UNFILLED_HOLES = frozenset({"moodscore"})


class DiaryDraftError(Exception):
    """日記ドラフト操作の失敗。メッセージはそのまま LLM に返す。"""


@dataclass(frozen=True)
class FaircopyResult:
    """清書の結果。`unresolved` は埋めずに残した穴の名前。"""

    content: str
    unresolved: list[str]


def parse_date(value: object) -> date:
    """`YYYY-MM-DD` 形式の日付文字列を検証して `date` にする。

    Raises:
        DiaryDraftError: 未指定または形式・暦として不正なとき。
    """
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value.strip()):
        raise DiaryDraftError("date is required in YYYY-MM-DD format")
    try:
        return date.fromisoformat(value.strip())
    except ValueError as e:
        raise DiaryDraftError(f"invalid date: {value}") from e


async def load_diary_settings() -> DiarySettings:
    """日記の設定（ダッシュボードで保存したもの）を読む。処理の始めに 1 回だけ呼ぶ。

    保存していなければ初期値（テンプレ未設定・04:00）を返す。DB から読めないときと
    保存値が不正なときは、未設定扱いにせず失敗させる。

    Raises:
        DiaryDraftError: 設定を DB から読めない、または保存値が不正なとき。
    """
    try:
        return await load_settings("diary", DiarySettings)
    except SettingsUnavailableError as e:
        raise DiaryDraftError(
            "could not load the diary settings (the database is unavailable); "
            "try again later"
        ) from e
    except InvalidStoredSettingsError as e:
        fields = ", ".join(err["field"] or "(whole)" for err in e.errors) or "(unknown)"
        raise DiaryDraftError(
            f"the saved diary settings are invalid ({fields}); "
            "save them again on the dashboard Extensions page (diary)"
        ) from e


def format_date_label(d: date) -> str:
    """`<<date>>` に埋める曜日付きの日付（例: `2026-09-28 (月)`）を返す。"""
    return f"{d.isoformat()} ({_WEEKDAYS[d.weekday()]})"


def _normalize(text: str) -> str:
    """改行コードを LF にそろえ、末尾の空白・改行を落とす。"""
    return text.replace("\r\n", "\n").rstrip()


def template_text(settings: DiarySettings) -> str:
    """設定のテンプレ本文を、比較用に改行をそろえて返す。

    Raises:
        DiaryDraftError: テンプレが未設定のとき。
    """
    if settings.template is None:
        raise DiaryDraftError(
            "the diary template is not set; save a template in the diary settings "
            "on the dashboard Extensions page"
        )
    return _normalize(settings.template)


def build_initial_draft(template: str, d: date) -> str:
    """テンプレをコピーし、`<<date>>` だけを日付で埋めたドラフトを返す。"""
    label = format_date_label(d)
    return _HOLE_RE.sub(
        lambda m: label if m.group(1) == "date" else m.group(0), template
    )


def validate_content(template: str, content: str, d: date) -> None:
    """ドラフト本文がテンプレの固定文を保っていることを検証する。

    穴（`<<name>>`）の中だけ自由に変えてよい。`<<date>>` の位置は引数の日付と一致
    しなければならない。

    Raises:
        DiaryDraftError: 固定文が欠けている、または日付が引数と一致しないとき。
    """
    label = format_date_label(d)
    fixed: list[str] = []
    pattern = ""
    pos = 0
    for m in _HOLE_RE.finditer(template):
        fixed.append(template[pos:m.start()])
        pattern += re.escape(template[pos:m.start()])
        if m.group(1) == "date":
            pattern += re.escape(label)
            fixed.append(label)
        else:
            pattern += ".*?"
        pos = m.end()
    fixed.append(template[pos:])
    pattern += re.escape(template[pos:])

    body = _normalize(content)
    if re.fullmatch(pattern, body, re.DOTALL):
        return

    cursor = 0
    for i, segment in enumerate(fixed):
        if not segment.strip():
            continue
        if i == 0 and not body.startswith(segment):
            found = -1
        else:
            found = body.find(segment, cursor)
        if found < 0:
            shown = segment.strip().splitlines()[0][:80]
            raise DiaryDraftError(
                "the fixed text of the template is broken (headings, frontmatter keys, "
                f"the date line and the ending cannot be changed); missing: {shown!r}. "
                "Start from the content returned by get and change only the text "
                "inside the <<...>> holes"
            )
        cursor = found + len(segment)
    raise DiaryDraftError(
        "the fixed text of the template is broken (extra text was added around it). "
        "Start from the content returned by get and change only the text "
        "inside the <<...>> holes"
    )


def make_faircopy(content: str) -> FaircopyResult:
    """残っている穴を `-` にした清書を返す。`<<moodscore>>` は推測せずそのまま残す。"""
    unresolved: list[str] = []

    def _fill(m: re.Match) -> str:
        if m.group(1) in UNFILLED_HOLES:
            if m.group(1) not in unresolved:
                unresolved.append(m.group(1))
            return m.group(0)
        return "-"

    return FaircopyResult(_HOLE_RE.sub(_fill, content), unresolved)


def day_rollover(settings: DiarySettings) -> time:
    """日付の区切り時刻（`DiarySettings.day_rollover`）を `time` で返す。"""
    hour, minute = settings.day_rollover.split(":")
    return time(int(hour), int(minute))


def writable_window(d: date, rollover: time) -> tuple[datetime, datetime]:
    """日記日 `d` の書き込み可能な区間（`ui.timezone` の aware datetime）を返す。

    区間は d の `rollover` から翌日の `rollover` まで（終端は含まない）。
    """
    tz = local_timezone()
    start = datetime.combine(d, rollover, tzinfo=tz)
    end = datetime.combine(d + timedelta(days=1), rollover, tzinfo=tz)
    return start, end


def current_writable_date(rollover: time, now: datetime | None = None) -> date:
    """いま書き込み可能な日記日を返す（`rollover` より前なら前日）。"""
    now = (now or utc_now()).astimezone(local_timezone())
    if now.time() < rollover:
        return now.date() - timedelta(days=1)
    return now.date()


def ensure_writable(d: date, rollover: time) -> None:
    """日記日 `d` がいま書き込み可能でなければ、いま書き込める date を含めて失敗させる。

    Raises:
        DiaryDraftError: 書き込み可能時間帯の外のとき。
    """
    now = utc_now().astimezone(local_timezone())
    start, end = writable_window(d, rollover)
    if start <= now < end:
        return
    raise DiaryDraftError(
        f"{d.isoformat()} is outside its writable window "
        f"({start.strftime('%Y-%m-%d %H:%M')} to {end.strftime('%Y-%m-%d %H:%M')}); "
        f"the date you can write now is {current_writable_date(rollover, now).isoformat()}"
    )


async def get_draft(d: date) -> str:
    """その日のドラフトを返す。無ければテンプレから作って保存する。

    設定はドラフトが無いときだけ読む（既存のドラフトはテンプレを変えても書き換えない）。

    Raises:
        DiaryDraftError: ドラフトが無く、設定を読めない・テンプレが未設定のとき。
    """
    from lilla_core.extensions.diary.repository import get_diary_draft_repo

    repo = get_diary_draft_repo()
    doc = await repo.get(d.isoformat())
    if doc is None:
        settings = await load_diary_settings()
        initial = build_initial_draft(template_text(settings), d)
        doc = await repo.create_if_absent(d.isoformat(), initial)
    return doc["content"]


async def set_draft(d: date, content: object) -> None:
    """その日のドラフトを丸ごと置き換える。

    Raises:
        DiaryDraftError: 設定を読めない・テンプレが未設定、書き込み可能時間帯の外、
            または固定文が壊れているとき。
    """
    from lilla_core.extensions.diary.repository import get_diary_draft_repo

    if not isinstance(content, str) or not content.strip():
        raise DiaryDraftError("content is required (the Markdown body)")
    settings = await load_diary_settings()
    ensure_writable(d, day_rollover(settings))
    validate_content(template_text(settings), content, d)
    await get_diary_draft_repo().set_content(d.isoformat(), _normalize(content) + "\n")


async def get_faircopy(d: date) -> FaircopyResult:
    """ドラフトを読み、残った穴を `-` にした清書を返す（保存も書き出しもしない）。

    Raises:
        DiaryDraftError: その日のドラフトが無いとき。
    """
    from lilla_core.extensions.diary.repository import get_diary_draft_repo

    doc = await get_diary_draft_repo().get(d.isoformat())
    if doc is None:
        raise DiaryDraftError(f"no draft for {d.isoformat()}")
    return make_faircopy(doc["content"])


def render_faircopy_path(pattern: str, d: date) -> str:
    """清書を書く notes 上のパスを組み立てる（`diary/` + プレースホルダを埋めた相対パス）。

    `pattern` は `DiarySettings.faircopy_path`（`diary/` からの相対パス。検証済み）。
    `{date}` は `YYYY-MM-DD`、`{year}` / `{month}` / `{day}` は 4 桁 / 2 桁 / 2 桁。
    """
    from lilla_core.extensions.diary import FAIRCOPY_DIRECTORY

    values = {
        "date": d.isoformat(),
        "year": f"{d.year:04d}",
        "month": f"{d.month:02d}",
        "day": f"{d.day:02d}",
    }
    relative = re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), pattern)
    return f"{FAIRCOPY_DIRECTORY}/{relative}"
