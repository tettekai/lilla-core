"""日記（日記ドラフト）の設定・リポジトリ・LLM ツール・会話向けの説明をまとめた拡張。

認可も他拡張も必要としない独立した拡張（`files` と同じ）のため
`requires` を持たない（`LILLA_EXTENSIONS` 内のどこに置いてもよい）。誰からも
`requires` されていないため、日記を使わない構成では `LILLA_EXTENSIONS` から外してよい
（そのときは lilla.yaml に `extensions.diary` を書いていれば一緒に消す）。

この拡張が持つもの:
- `extensions.diary` セクションのモデル（`DiaryConfig`。YAML 担当の項目は無く、節ごと
  省略できる）とその申告
- ダッシュボードで編集する設定（`DiarySettings`。テンプレ本文と日付の切り替わり時刻。
  `settings_model()` で申告し、値はコアの `extension_settings` に保存される）
- `diary_drafts` コレクションのリポジトリ（`repository.py`。`startup_repos()` で申告）
- 日記ドラフトの LLM ツール（`tools/`。`tool_roots()` で探索ルートへ足す）と、その
  既定のツール YAML（`tool_configs/`。`tool_config_roots()` で同梱する。利用者が
  `${CONFIG_ROOT}/tools` に同じ stem の YAML を置くと丸ごと置き換わる）
- 会話向けの日記の説明（`default_prompt()`。ja / en）
- 保存先 `notes` で清書を書くトップレベルディレクトリ `diary` の宣言（`notes_directories()`）
- 清書を notes の `diary/` の下へ書く既定タスク（`tools/task_diary_faircopy.py`。書くかどうかは
  `DiarySettings.faircopy_storage`、パスは `diary/` からの相対パスの `faircopy_path`）と、その通知の
  文言カタログ（`locales/`）

保存先 notes はコアの `FileStorage`（`get_file_storage("notes")`）を名前で引くだけで、
notes の実装を import しない。

モジュールの import 時に `extension` インスタンスを生成するため、ここでは
サブモジュール（`service` / `repository`）を import しない。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from lilla_core.core.extension import Extension

_PACKAGE_DIR = Path(__file__).resolve().parent

#: 清書の保存先のパスに書けるプレースホルダ。
FAIRCOPY_PATH_PLACEHOLDERS = ("date", "year", "month", "day")

#: 清書を書く保存先（`FileStorage`）の名前。清書は notes にだけ書く。
FAIRCOPY_STORAGE = "notes"

#: 保存先 `notes` で清書を書くトップレベルディレクトリ（`faircopy_path` はここからの相対パス）。
FAIRCOPY_DIRECTORY = "diary"

#: 清書のパス（`FAIRCOPY_DIRECTORY` からの相対パス）の既定値。
DEFAULT_FAIRCOPY_PATH = "{year}/{month}/{date}.md"

#: 保存先 `notes` で宣言するトップレベルディレクトリ `diary` の用途（パスの形は書かない）。
NOTES_DIRECTORY_USAGE = {
    "ja": "日記の清書の置き場",
    "en": "Where the fair copies of the diary are kept",
}

#: 清書の保存先の既定値（一度も保存していないときに使う。notes へ書く＝オン）。
DEFAULT_FAIRCOPY_STORAGES = (FAIRCOPY_STORAGE,)

_BRACE_RE = re.compile(r"\{([^{}]*)\}")

def normalize_hhmm(value: Any) -> str:
    """時刻設定を `HH:MM` 形式へ正規化して検証する（`6:5` → `06:05`）。

    YAML から読んでいたころの「60 進整数（分）」の受け入れは、画面の設定（JSON の
    文字列）になったため持たない。

    Raises:
        ValueError: 文字列でない・形式・範囲が不正なとき。
    """
    if not isinstance(value, str):
        raise ValueError("time must be a string in HH:MM format")
    parts = value.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError(f"time must be in HH:MM format: {value!r}")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f"time is out of range: {value!r}")
    return f"{hour:02d}:{minute:02d}"


def check_faircopy_path(value: Any) -> str:
    """清書のパス（`FAIRCOPY_DIRECTORY` からの相対パス）を検証して正規化する。

    前後の空白は除く。値は常に `diary/` からの相対パスとしてそのまま扱う（先頭に
    `diary/` を書けば `diary/diary/...` になる）。

    `/` 区切りの相対パスで、絶対パス（先頭の `/`）・バックスラッシュ・空の要素（`a//b` や
    末尾の `/`）・`.` / `..` の要素は拒否する。`{date}` を必ず含み（日ごとに別のファイルに
    するため）、波括弧は `FAIRCOPY_PATH_PLACEHOLDERS` のプレースホルダにしか使えない。

    Raises:
        ValueError: 文字列でない・空・パスの形が不正・`{date}` を含まない・不明な
            プレースホルダや閉じていない波括弧があるとき。
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("faircopy_path must not be blank")
    value = value.strip()
    if "\\" in value:
        raise ValueError("faircopy_path must use '/' as the separator")
    if value.startswith("/"):
        raise ValueError(
            f"faircopy_path must be relative to {FAIRCOPY_DIRECTORY}/ (no leading '/')"
        )
    segments = value.split("/")
    if any(segment == "" for segment in segments):
        raise ValueError(
            "faircopy_path must not contain empty segments ('//' or a trailing '/')"
        )
    if any(segment in (".", "..") for segment in segments):
        raise ValueError("faircopy_path must not contain '.' or '..' segments")
    for name in _BRACE_RE.findall(value):
        if name not in FAIRCOPY_PATH_PLACEHOLDERS:
            raise ValueError(
                f"unknown placeholder {{{name}}} in faircopy_path "
                f"(available: {', '.join('{' + p + '}' for p in FAIRCOPY_PATH_PLACEHOLDERS)})"
            )
    if "{" in _BRACE_RE.sub("", value) or "}" in _BRACE_RE.sub("", value):
        raise ValueError("faircopy_path has an unmatched brace")
    if "{date}" not in value:
        raise ValueError("faircopy_path must contain {date}")
    return value


def check_faircopy_storages(value: Any) -> list[str]:
    """清書を notes へ書くかどうか（`faircopy_storage`）を検証して正規化する。

    結果は `["notes"]`（書く）か `[]`（書かない）のどちらか。`notes` 以外の名前（`files` など）は
    外さずに拒否する（保存値なら「保存値が不正」になる）。

    Raises:
        ValueError: リストでない、要素が文字列でない、または `notes` 以外の名前を含むとき。
    """
    if not isinstance(value, list):
        raise ValueError("faircopy_storage must be a list of storage names")
    if not all(isinstance(item, str) for item in value):
        raise ValueError("faircopy_storage must be a list of strings")
    unknown = [item for item in value if item != FAIRCOPY_STORAGE]
    if unknown:
        raise ValueError(
            f"faircopy_storage only accepts {FAIRCOPY_STORAGE!r}: got {unknown!r}"
        )
    return [FAIRCOPY_STORAGE] if value else []


class DiaryConfig(BaseModel):
    """lilla.yaml の `extensions.diary` セクション（YAML 担当の項目は今は無い）。

    日記の設定はすべてダッシュボードで編集する画面管理の設定（`DiarySettings`）にある。
    その項目を YAML に書くとコアが移行先を示して起動時に落とす。節そのもの
    （`extensions.diary:`）は空なら残っていてもよい。
    """

    model_config = ConfigDict(extra="forbid")


class DiarySettings(BaseModel):
    """ダッシュボードで編集する日記の設定（`extension_settings` に保存する）。

    `template` は日記の Markdown テンプレの本文そのもの（`file:` / `dir:` の参照としては
    解釈しない）。`None` は未設定で、日記ドラフトの作成・保存は失敗する。空・空白だけの
    本文と、`<<name>>` の穴として読めない書き方は保存時に拒否する（本文は書き換えない。
    `{{...}}` は穴ではなくただの文字列）。
    `day_rollover` は日付の切り替わり時刻（`HH:MM`、`ui.timezone` で判定）で、日記日 d の
    分は d の `day_rollover` から翌日の `day_rollover` まで書き込める。
    `faircopy_storage` は清書タスクが notes へ清書を書くかどうかのオンオフで、`["notes"]` なら
    書き、`[]` なら書かない（一度も保存していなければ `["notes"]`）。`notes` 以外の名前は
    拒否する（`check_faircopy_storages`）。
    `faircopy_path` は notes の `diary/` からの相対パスで、`{date}` などを日付で埋めて
    `diary/` の下に書く（`diary.service.render_faircopy_path`）。

    値は `diary.service.load_diary_settings()` で処理ごとに 1 回読み、その処理の間は
    同じインスタンスを使い続ける。
    """

    model_config = ConfigDict(frozen=True)

    template: str | None = Field(
        default=None,
        title="テンプレート",
        description=(
            "日記の Markdown 本文。<<date>> は作成時に日付で埋まり、それ以外の <<名前>> は"
            "会話で埋める穴。変えても既存の下書きは書き換わらない"
        ),
        json_schema_extra={"multiline": True},
    )
    day_rollover: str = Field(
        default="04:00",
        title="日付の切り替わり時刻",
        description="HH:MM（ui.timezone）。この時刻より前は前日の日記として扱う",
    )
    faircopy_storage: list[str] = Field(
        default=list(DEFAULT_FAIRCOPY_STORAGES),
        title="清書の保存先",
        description=(
            "チェックすると、清書タスク（task_write_diary）が notes の diary/ の下へ清書を書く。"
            "チェックを外すと清書はどこにも保存されません（独自の書き出しタスクを使うとき用）"
        ),
        json_schema_extra={
            "widget": "checkbox-group",
            "choices": [{"value": FAIRCOPY_STORAGE, "title": "notes（diary/ の下）"}],
        },
    )
    faircopy_path: str = Field(
        default=DEFAULT_FAIRCOPY_PATH,
        title="清書のパス（diary/ からの相対パス）",
        description=(
            "notes の diary/ からの相対パス（例: {year}/{month}/{date}.md → "
            "diary/2026/11/2026-11-12.md）。{date}（YYYY-MM-DD）を必ず含め、"
            "{year} / {month} / {day} も使える。先頭の /、..、空の要素（// や末尾の /）は使えない。"
            "同じ日は上書きする"
        ),
    )

    @field_validator("template")
    @classmethod
    def _validate_template(cls, value: str | None) -> str | None:
        """空・空白だけの本文と、穴として読めない `<<` / `>>` を拒否する。"""
        if value is None:
            return None
        if not value.strip():
            raise ValueError("template must not be blank (unset it instead)")
        from lilla_core.extensions.diary.template_rules import check_template

        check_template(value)
        return value

    @field_validator("day_rollover", mode="before")
    @classmethod
    def _normalize_time(cls, value: Any) -> str:
        """`day_rollover` を `HH:MM` 形式へ正規化する。"""
        return normalize_hhmm(value)

    @field_validator("faircopy_storage", mode="before")
    @classmethod
    def _validate_storages(cls, value: Any) -> list[str]:
        """notes へ書くかどうかに正規化する（`notes` 以外の名前は拒否する）。"""
        return check_faircopy_storages(value)

    @field_validator("faircopy_path", mode="before")
    @classmethod
    def _validate_path(cls, value: Any) -> str:
        """清書のパス（`diary/` からの相対パス）を検証して正規化する。"""
        return check_faircopy_path(value)


_PROMPT_JA = """\
## 日記
日記には、その日の出来事や気持ちを書く。日記の本文は日付ごとの下書き（Markdown）。日記の聞き取り（ヒアリング）のときだけでなく、普段の会話でも使う。

- 会話の中で日記に残る話が出たら、llm_diary_draft_get でその日の下書きの最新の全文を取ってから、llm_diary_draft_set で書く。
- llm_diary_draft_set には、取得した全文のうち話に出た <<名前>> の穴だけを書き換えた全文を渡す。見出し・frontmatter のキー・日付・末尾の固定文は変えない。
- 話に出なかった空の穴は <<名前>> のまま残す。moodscore は本人に確認した値だけを入れ、推測しない。
- date はツールの引数で必ず指定する。日付の区切り（既定 04:00）より前の時刻なら前日、それ以外はその日の日付にする。
{%- if settings.faircopy_storage %}
- 書き終えた日記は、清書タスクが清書して notes の diary/{{ settings.faircopy_path }} に保存している（{date} は YYYY-MM-DD、{year} / {month} / {day} は 4 桁 / 2 桁 / 2 桁の年・月・日）。過去の日記を読むときは、日付からこのパスを組み立てて llm_notes_read で読む。
- 清書は清書タスクが動いた後にできるため、当日や、まだ清書されていない日は llm_diary_draft_get で下書きを見る。
{%- endif %}"""

_PROMPT_EN = """\
## Diary
The diary records what happened that day and how the user felt. Its body is a per-day draft (Markdown). Use it in everyday conversation, not only when interviewing the user for the diary.

- When something worth keeping in the diary comes up, first call llm_diary_draft_get to fetch the latest full draft for that day, then write it with llm_diary_draft_set.
- Pass llm_diary_draft_set the full text you fetched, with only the <<name>> holes that came up in the conversation rewritten. Do not change headings, frontmatter keys, the date, or the fixed text at the end.
- Leave empty holes that did not come up as <<name>>. Fill moodscore only with a value the user confirmed; never guess it.
- Always pass date as a tool argument. Before the day rollover time (default 04:00) use the previous day; otherwise use that day's date.
{%- if settings.faircopy_storage %}
- Finished diaries are written out by the faircopy task to notes at diary/{{ settings.faircopy_path }} ({date} is YYYY-MM-DD; {year} / {month} / {day} are the 4-digit year, 2-digit month, and 2-digit day). To read a past diary, build this path from the date and read it with llm_notes_read.
- The faircopy only exists after the faircopy task has run, so for today or any day not yet written out, read the draft with llm_diary_draft_get.
{%- endif %}"""


class DiaryExtension(Extension):
    """日記の設定・リポジトリ・ツール・会話向けの説明を申告する拡張。"""

    name = "diary"

    def config_model(self) -> type[BaseModel]:
        """日記用の YAML セクションのモデルを返す（節名は `extensions.diary`）。"""
        return DiaryConfig

    def default_prompt(self) -> dict[str, str]:
        """会話向けの日記の説明を言語ごとに返す（en は ja の翻訳）。

        本文はコアが会話のたびに Jinja2 で展開する。`settings` は
        `DiarySettings` の値（秘匿値なし）で、清書がオン（`faircopy_storage` が空でない）
        ときだけ、清書の保存先（notes の `diary/` + `faircopy_path`）と読み方を足す。
        設定は会話ごとに読み直されるため、ダッシュボードで変えると次の会話から説明も変わる。
        `extension_overrides` の `prompt` で上書きしているときはこの本文は使われない
        （上書きの本文にも同じ `settings` が渡るので、必要なら同じ書き方で載せる）。
        """
        return {"ja": _PROMPT_JA, "en": _PROMPT_EN}

    def startup_repos(self) -> list:
        """`on_ready` で `init_collection()` を呼ぶリポジトリを返す。"""
        from lilla_core.extensions.diary.repository import get_diary_draft_repo

        return [get_diary_draft_repo]

    def tool_roots(self) -> list[Path]:
        """日記のツール（`lilla_core/extensions/diary/tools/`）の探索先を返す。"""
        return [_PACKAGE_DIR / "tools"]

    def tool_config_roots(self) -> list[Path]:
        """同梱する既定のツール YAML（`lilla_core/extensions/diary/tool_configs/`）のディレクトリを返す。

        利用者が `${CONFIG_ROOT}/tools` に同じ stem の YAML を置くと、同梱分は
        丸ごと置き換わる（無効化するなら `enabled: false`）。
        """
        return [_PACKAGE_DIR / "tool_configs"]

    def notes_directories(self) -> dict[str, dict[str, str]]:
        """保存先 `notes` で清書を書くトップレベルディレクトリ（`diary`）と、その用途を返す。

        notes が書き込みを許すのはロード済みの拡張が宣言したディレクトリの配下だけなので、
        清書を書く `FAIRCOPY_DIRECTORY`（`diary`）をここで宣言する。`faircopy_path` は
        このディレクトリからの相対パスで、外を指せない（`check_faircopy_path`）。
        用途は notes のデフォルトのプロンプトにそのまま載るため、パスの形は書かない。
        """
        return {FAIRCOPY_DIRECTORY: dict(NOTES_DIRECTORY_USAGE)}

    def settings_model(self) -> type[BaseModel]:
        """ダッシュボードで編集する設定（テンプレ本文・日付の切り替わり時刻・清書の保存先）のモデルを返す。"""
        return DiarySettings

    def locale_dirs(self) -> list[Path]:
        """清書タスクの通知に使う文言カタログ（`lilla_core/extensions/diary/locales/`）を返す。"""
        return [_PACKAGE_DIR / "locales"]


extension = DiaryExtension()
