"""運用者が書くプロンプト本文を Jinja2 テンプレートとして展開する。

対象は運用者が書くプロンプトだけ（`prompt.system` / `prompt.conversation` /
`prompt.discord`・LLM プロバイダーの `prompt`・`llm_expert` / `task_scheduled_llm` の
`prompt`・拡張の `client_prompt_providers()` の戻り値・スキルの本文・拡張のプロンプト）。
会話履歴・ユーザーメモ・ツール結果などのデータは展開しない（データの中の波括弧を
テンプレートとして解釈させないため）。展開の処理は `render_template()` 1 つに集め、
各呼び出し元はそれぞれの変数だけを渡す。

- 展開には `SandboxedEnvironment` を使う。本文はダッシュボードから書き換えられるものも
  あるため、テンプレートから Python の属性・モジュールへ手が届かないようにする
- どのプロンプトにも共通の変数 `current_hour`（`ui.timezone` の現在日時を
  `YYYY-MM-DD HH:xx` にした文字列。プロンプトのキャッシュが効くよう時までにする）と、
  保存先 `notes` のファイルを読む関数 `notes(path)`（無いファイルは空文字）を渡す。
  notes の読み取りが非同期のため環境は async モードで、展開の関数はどれも async
- 拡張のプロンプトにだけ `settings`（その拡張の画面管理の設定を呼ぶたびに読み直した
  もの。秘匿値（`SecretStr` / `SecretBytes`）は除く）も渡す
- どのプロンプトでも使えるフィルタ `strip_tag(name)`（値の中から `<name …>` / `</name>` の形の
  タグを取り除き、取り除いたら WARNING を出す）を登録する。読み込んだ本文をタグで囲んで渡すときに、
  本文がタグを閉じて外へ出られないようにするため
- 未定義の名前は `StrictUndefined` で失敗にする（黙って空文字にしない）
- 記法を含まない本文が展開後も変わらないよう、末尾の改行を残す
- 会話を止めないため、呼び出し元は失敗したら元の本文を使う（`render_or_raw()`）
"""
from __future__ import annotations

import logging
import re
import types
import typing
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from jinja2 import StrictUndefined
from jinja2.exceptions import TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel, SecretBytes, SecretStr

logger = logging.getLogger(__name__)

#: 展開に使う環境。HTML ではないため自動エスケープはしない。
_ENV = SandboxedEnvironment(
    autoescape=False,
    undefined=StrictUndefined,
    keep_trailing_newline=True,
    enable_async=True,
)

#: `strip_tag` に渡せるタグ名（英字で始まり、英数字・`_`・`-`）。
_TAG_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")


def strip_tag(value: Any, name: Any) -> str:
    """テンプレートのフィルタ `strip_tag(name)` の本体。値の中から `name` のタグを取り除く。

    開きタグ・閉じタグ（`<name>` / `<name 属性>` / `</name>` / `< / NAME >` など。大文字小文字と
    `<` の後ろ・`/` の前後の空白は区別しない）を空文字に置き換える。同じ行に `>` が無い途中までの
    形（`</name` だけ）は、タグ名までを取り除く（後ろの本文は消さない）。取り除いた結果に新しい
    タグができる入れ子の形（`</na</name>me>` など）も残さないよう、無くなるまで繰り返す。
    `name` の後ろに英数字・`_`・`-` が続くもの（`<name_extra>` など）は別のタグとして残す。
    タグではない単語としての `name` は書き換えない。取り除いたときは件数を WARNING ログに出す。

    Args:
        value: 対象の文字列（文字列でなければ `str()` にする）。
        name: 取り除くタグの名前。

    Returns:
        タグを取り除いた文字列。

    Raises:
        ValueError: `name` がタグ名の形でないとき。
    """
    if not isinstance(name, str) or not _TAG_NAME_RE.fullmatch(name):
        raise ValueError("strip_tag() takes a tag name of letters, digits, '_' and '-'")
    pattern = re.compile(
        r"<\s*/?\s*" + re.escape(name) + r"(?![A-Za-z0-9_-])(?:[^<>\n]*>)?", re.IGNORECASE
    )
    text = str(value)
    count = 0
    while True:
        text, removed = pattern.subn("", text)
        if not removed:
            break
        count += removed
    if count:
        logger.warning("Removed %d '%s' tag(s) from text in a prompt template", count, name)
    return text


_ENV.filters["strip_tag"] = strip_tag

_SECRET_TYPES = (SecretStr, SecretBytes)

#: 値から取り除いたことを表す目印（`None` と区別するため）。
_REMOVED = object()

#: どのプロンプトにも渡す共通の変数の名前（呼び出し元の変数と重ねてはならない）。
COMMON_VARIABLE_NAMES = frozenset({"current_hour", "notes"})

#: `notes(path)` が返す本文の最大文字数（超えた分は切り詰めて WARNING を出す）。
MAX_NOTE_CHARS = 100_000

#: `current_hour` の書式。分は伏せる（システムプロンプトの時刻と同じく時まで）。
_CURRENT_HOUR_FORMAT = "%Y-%m-%d %H:xx"


class _SettingsView:
    """テンプレートへ渡す設定の読み取り専用の入れ物。

    Jinja2 の `settings.名前` は dict の属性（`items` / `keys` / `get` など）を先に探すため、
    dict のまま渡すとメソッド名と同じ項目が値ではなくメソッドに展開される。この入れ物は
    公開の属性を持たず、`settings.名前` も `settings['名前']` も項目の値だけを返す。
    入れ子の dict も同じ入れ物に、リストの要素も再帰で包む。無い名前は `AttributeError`
    （添字なら `KeyError`）にし、`StrictUndefined` で失敗にさせる。
    """

    __slots__ = ("_data",)

    def __init__(self, data: dict[str, Any]) -> None:
        """包む dict を受け取る（値は参照のたびに包む）。"""
        object.__setattr__(self, "_data", data)

    def __getattr__(self, name: str) -> Any:
        """`settings.名前` で項目の値を返す。"""
        try:
            return _wrap(self._data[name])
        except KeyError:
            raise AttributeError(name) from None

    def __getitem__(self, name: str) -> Any:
        """`settings['名前']` で項目の値を返す。"""
        return _wrap(self._data[name])

    def __setattr__(self, name: str, value: Any) -> None:
        """書き換えを拒否する。"""
        raise AttributeError("settings is read-only")

    def __contains__(self, name: object) -> bool:
        """`'名前' in settings` で項目の有無を返す。"""
        return name in self._data

    def __iter__(self):
        """項目名を順に返す（`{% for name in settings %}` 用）。"""
        return iter(self._data)

    def __len__(self) -> int:
        """項目の数を返す。"""
        return len(self._data)

    def __bool__(self) -> bool:
        """項目が 1 つでもあれば真。"""
        return bool(self._data)

    def __str__(self) -> str:
        """中身の dict と同じ文字列にする。"""
        return str(self._data)


def _wrap(value: Any) -> Any:
    """dict を `_SettingsView` に、リスト・タプルの要素を再帰で包む。"""
    if isinstance(value, dict):
        return _SettingsView(value)
    if isinstance(value, (list, tuple)):
        return [_wrap(item) for item in value]
    return value


class PromptTemplateError(ValueError):
    """プロンプトのテンプレートを展開できない（構文エラー・未定義の名前・サンドボックスの拒否など）。"""


def current_hour(now: datetime | None = None) -> str:
    """共通の変数 `current_hour` の値（`2026-11-14 20:xx` の形）を返す。

    Args:
        now: 基準の日時。省略時は `local_now()`。タイムゾーン付きなら
            `local_timezone()`（`ui.timezone`）へ合わせてから書式にする。

    Returns:
        `YYYY-MM-DD HH:xx` の文字列（曜日は付けない）。
    """
    from lilla_core.utils.datetime_utils import local_now, local_timezone

    if now is None:
        now = local_now()
    elif now.tzinfo is not None:
        now = now.astimezone(local_timezone())
    return now.strftime(_CURRENT_HOUR_FORMAT)


async def read_note(path: Any) -> str:
    """テンプレートの関数 `notes(path)` の本体。保存先 `notes` のファイルの本文を返す。

    ファイルが無いときは空文字を返す（まだ作られていないノートを書いておけるように）。
    ディレクトリを指したときも空文字を返すが、指定の誤りに気づけるよう WARNING を出す。
    `MAX_NOTE_CHARS` を超える本文は切り詰めて WARNING を出す。

    Raises:
        TypeError: `path` が文字列でないとき。
        RuntimeError: 保存先 `notes` が登録されていないとき。
        FileStorageError: パスの規則違反・読み取りの失敗など（無いファイル・ディレクトリを除く）。
    """
    from lilla_core.core.extension import get_file_storage
    from lilla_core.core.file_storage import FileStorageNotFoundError

    if not isinstance(path, str):
        raise TypeError("notes() takes a note path string")
    storage = get_file_storage("notes")
    if storage is None:
        raise RuntimeError("file storage 'notes' is not registered")
    try:
        text = await storage.read(path)
    except FileStorageNotFoundError:
        if await _is_directory(storage, path):
            logger.warning(
                "Note path %s is a directory, not a file; notes() returns an empty string",
                path,
            )
        return ""
    if len(text) > MAX_NOTE_CHARS:
        logger.warning(
            "Note %s is longer than %d characters; truncating it in the prompt",
            path, MAX_NOTE_CHARS,
        )
        return text[:MAX_NOTE_CHARS]
    return text


async def _is_directory(storage: Any, path: str) -> bool:
    """`path` が保存先にあるディレクトリかを返す（一覧が取れれば真。失敗は偽として扱う）。"""
    try:
        await storage.list(path)
    except Exception:
        return False
    return True


def check_syntax(text: str) -> None:
    """テンプレートの構文だけを確かめる。

    Raises:
        PromptTemplateError: 構文エラーのとき。メッセージに行番号を含める。
    """
    try:
        _ENV.parse(text)
    except TemplateSyntaxError as e:
        raise PromptTemplateError(f"Template syntax error (line {e.lineno}): {e.message}") from e


async def render_template(
    text: str,
    variables: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
    notes_reader: Callable[[Any], Awaitable[str]] | None = None,
) -> str:
    """テンプレートを、共通の変数と呼び出し元の変数で展開する（全プロンプト共通の処理）。

    共通の変数（`current_hour` と関数 `notes`）はここで作る。呼び出し元ごとに違う変数（拡張の
    `settings` など）は `variables` で渡す。値は展開済みの値としてそのまま出力し、
    値の中の波括弧はもう一度展開しない。

    Args:
        text: テンプレートの本文。
        variables: 呼び出し元の変数（共通の変数と同じ名前は使えない）。
        now: `current_hour` の基準の日時。省略時は今。
        notes_reader: 関数 `notes(path)` の本体の差し替え。省略時は保存先 `notes` を読む
            `read_note()`（プレビューで実データを読まないときに使う）。

    Returns:
        展開した本文。

    Raises:
        ValueError: `variables` が共通の変数と同じ名前を含むとき（呼び出し元の誤り）。
        PromptTemplateError: 構文エラー・未定義の名前・サンドボックスの拒否など、
            展開のどこかで失敗したとき。
    """
    variables = dict(variables or {})
    reserved = COMMON_VARIABLE_NAMES & variables.keys()
    if reserved:
        raise ValueError(f"Template variables overlap common variables: {sorted(reserved)}")
    check_syntax(text)
    common = {"current_hour": current_hour(now), "notes": notes_reader or read_note}
    try:
        return await _ENV.from_string(text).render_async(**common, **variables)
    except Exception as e:
        raise PromptTemplateError(f"Failed to render template: {type(e).__name__}: {e}") from e


async def render_or_raw(
    text: str,
    label: str,
    variables: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> str:
    """`render_template()` で展開し、失敗したら WARNING を出して元の本文を返す。

    プロンプトの書き損じで会話やタスクを止めないための入口。空の本文は展開せずそのまま返す。

    Args:
        text: テンプレートの本文。
        label: ログに出すプロンプトの名前（`prompt.system`・`skill 'name'` など）。
        variables: 呼び出し元の変数。
        now: `current_hour` の基準の日時。省略時は今。

    Returns:
        展開した本文。失敗したときは `text`。
    """
    if not text:
        return text
    try:
        return await render_template(text, variables, now=now)
    except PromptTemplateError as e:
        logger.warning(
            "Failed to render prompt template %s; using the template as is: %s", label, e,
        )
        return text


async def render_prompt(text: str, settings: dict[str, Any]) -> str:
    """拡張のプロンプトを、共通の変数と `settings` で展開する。

    `settings` は `_SettingsView` に包んで渡すため、項目名が dict のメソッド名
    （`items` など）と同じでも `settings.名前` は値を返す。

    Args:
        text: テンプレートの本文。
        settings: テンプレートに `settings` として渡す値。

    Returns:
        展開した本文。

    Raises:
        PromptTemplateError: 構文エラー・未定義の名前・サンドボックスの拒否など、
            展開のどこかで失敗したとき。
    """
    return await render_template(text, {"settings": _SettingsView(settings)})


def _mentions_secret(annotation: Any) -> bool:
    """型の注釈が秘匿値の型を含むか（`SecretStr | None` や `list[SecretStr]` も含む）。"""
    if isinstance(annotation, type) and issubclass(annotation, _SECRET_TYPES):
        return True
    origin = typing.get_origin(annotation)
    if origin is None and not isinstance(annotation, types.UnionType):
        return False
    return any(_mentions_secret(arg) for arg in typing.get_args(annotation))


def _strip_secret_values(value: Any) -> Any:
    """dict / list / tuple を再帰でたどり、秘匿値を取り除く。"""
    if isinstance(value, _SECRET_TYPES):
        return _REMOVED
    if isinstance(value, dict):
        stripped = {key: _strip_secret_values(item) for key, item in value.items()}
        return {key: item for key, item in stripped.items() if item is not _REMOVED}
    if isinstance(value, (list, tuple)):
        stripped = [_strip_secret_values(item) for item in value]
        return [item for item in stripped if item is not _REMOVED]
    return value


def public_field_names(model: type[BaseModel]) -> list[str]:
    """テンプレートから `settings.名前` で使える項目名（秘匿値の型の項目を除く）。"""
    return [
        name for name, field in model.model_fields.items()
        if not _mentions_secret(field.annotation)
    ]


def settings_for_template(settings: BaseModel) -> dict[str, Any]:
    """設定のインスタンスを、テンプレートへ渡す dict にする（秘匿値を除く）。

    秘匿値の型の項目は値が `None` でも名前ごと除き、それ以外は値を再帰でたどって
    `SecretStr` / `SecretBytes` を取り除く（入れ子のモデル・リストの中も同じ）。
    """
    allowed = set(public_field_names(type(settings)))
    dumped = settings.model_dump()
    return _strip_secret_values({key: value for key, value in dumped.items() if key in allowed})


async def load_template_settings(name: str) -> dict[str, Any]:
    """拡張の画面管理の設定を読み、テンプレートへ渡す dict にする（呼ぶたびに DB を読む）。

    `settings_model()` を持たない拡張は DB を読まずに空の dict を返す。

    Raises:
        ExtensionSettingsError: 設定を DB から読めない・保存値が不正なとき。
    """
    from lilla_core.services.extension_settings import get_settings_model, load_settings

    if get_settings_model(name) is None:
        return {}
    return settings_for_template(await load_settings(name))


@dataclass(frozen=True)
class TemplateComment:
    """テンプレートの中の `{# … #}`（展開すると消える部分）1 つ。"""

    #: コメントが始まる行（1 始まり）。
    line: int
    #: `{#` と `#}` の間の文字列（前後の空白も含めてそのまま）。
    text: str


@dataclass(frozen=True)
class TemplatePreview:
    """試しに展開した結果（保存時の検査とダッシュボードのプレビューが共有する）。"""

    #: 展開した本文。設定を読めず試しの展開を飛ばしたときは `None`。
    rendered: str | None
    #: 展開で消えるコメント（出てくる順）。
    comments: list[TemplateComment] = field(default_factory=list)
    #: 設定を読めなかったため構文だけを確かめたか。
    settings_unavailable: bool = False


def find_comments(text: str) -> list[TemplateComment]:
    """テンプレートの中のコメント（`{# … #}`）を Jinja2 の字句解析で探す。

    `{% raw %}` の中の `{#` などは Jinja2 と同じくコメントにしない。

    Raises:
        PromptTemplateError: 字句として読めないとき（構文エラー）。
    """
    comments: list[TemplateComment] = []
    try:
        for lineno, token, value in _ENV.lex(text):
            if token == "comment":
                comments.append(TemplateComment(line=lineno, text=value))
    except TemplateSyntaxError as e:
        raise PromptTemplateError(f"Template syntax error (line {e.lineno}): {e.message}") from e
    return comments


async def preview_skill_prompt(text: str) -> TemplatePreview:
    """スキルの本文を、実行時と同じ変数（`current_hour`）で試しに展開する。

    保存時の検査（ダッシュボードの Skills API）もこの関数を使い、プレビューと結果をそろえる。

    Raises:
        PromptTemplateError: 構文エラー・未定義の名前・サンドボックスの拒否など。
    """
    rendered = await render_template(text)
    return TemplatePreview(rendered=rendered, comments=find_comments(text))


async def preview_extension_prompt(name: str, text: str) -> TemplatePreview:
    """拡張のプロンプトを、今の設定（秘匿値を除く）で試しに展開する。

    設定を読めない（DB から読めない・保存値が不正）ときは試しの展開を飛ばし、構文だけを
    確かめて `settings_unavailable=True` を返す（設定の不調で上書きの保存まで止めないため）。
    通らなかった分岐の中は確かめられない（Jinja2 は通った分岐だけを評価する）。

    Raises:
        PromptTemplateError: 構文エラーか、今の設定で展開できないとき。
    """
    from lilla_core.services.extension_settings import ExtensionSettingsError

    check_syntax(text)
    comments = find_comments(text)
    try:
        settings = await load_template_settings(name)
    except ExtensionSettingsError as e:
        logger.info(
            "Skipping trial render of prompt template of extension '%s'; "
            "settings could not be loaded: %s", name, e,
        )
        return TemplatePreview(rendered=None, comments=comments, settings_unavailable=True)
    return TemplatePreview(rendered=await render_prompt(text, settings), comments=comments)


async def validate_extension_prompt(name: str, text: str) -> None:
    """保存しようとしているプロンプトを、今の設定で試しに展開して確かめる。

    中身は `preview_extension_prompt()` と同じ（プレビューと保存時の検査をそろえるため）。

    Raises:
        PromptTemplateError: 構文エラーか、今の設定で展開できないとき。
    """
    await preview_extension_prompt(name, text)


async def render_extension_prompt_parts(name: str, texts: list[str]) -> list[str]:
    """拡張のプロンプトの部分（既定・追加分）を、それぞれ独立したテンプレートとして展開する。

    設定はまとめて 1 回だけ読む。読めなければ拡張名を含む WARNING を出し、どの部分も
    展開前の本文をそのまま返す。部分ごとの展開の失敗は、その部分だけ WARNING を出して
    展開前の本文を返し、ほかの部分の結果には影響させない（会話は止めない）。

    Args:
        name: 拡張名。
        texts: 展開する本文のリスト。

    Returns:
        `texts` と同じ順・同じ数の展開結果。
    """
    from lilla_core.services.extension_settings import ExtensionSettingsError

    if not texts:
        return []
    try:
        settings = await load_template_settings(name)
    except ExtensionSettingsError as e:
        logger.warning(
            "Failed to load settings for prompt template of extension '%s'; "
            "injecting the template as is: %s", name, e,
        )
        return list(texts)
    results: list[str] = []
    for text in texts:
        try:
            results.append(await render_prompt(text, settings))
        except PromptTemplateError as e:
            logger.warning(
                "Failed to render prompt template of extension '%s'; "
                "injecting the template as is: %s", name, e,
            )
            results.append(text)
    return results


async def render_extension_prompt(name: str, text: str) -> str:
    """拡張のプロンプト本文を、その拡張の設定で展開する（失敗しても例外を投げない）。

    設定の読み込みか展開に失敗したら、拡張名を含む WARNING ログを出して `text` を
    そのまま返す（会話は止めない）。中身は `render_extension_prompt_parts()` の 1 部分分。

    Args:
        name: 拡張名。
        text: テンプレートの本文。

    Returns:
        展開した本文。失敗したときは `text`。
    """
    return (await render_extension_prompt_parts(name, [text]))[0]


def dummy_task_variables(names: list[str]) -> dict[str, str]:
    """タスクのプロンプトの試し展開に使う、変数名ごとのダミー値（`[名前]`）を返す。"""
    return {name: f"[{name}]" for name in names}


async def _dummy_note(path: Any) -> str:
    """試し展開用の `notes(path)`。保存先を読まず、パスを示す目印だけを返す。

    Raises:
        TypeError: `path` が文字列でないとき（実行時の `notes()` と同じ）。
    """
    if not isinstance(path, str):
        raise TypeError("notes() takes a note path string")
    return f"[notes: {path}]"


async def preview_task_prompt(text: str, variable_names: list[str]) -> TemplatePreview:
    """定期タスクのプロンプトを、宣言した変数名のダミー値で試しに展開する。

    実データ（remember や notes）は読まない。`notes(path)` もパスを示す目印を返すだけ。
    保存時の検査（`validate_task_prompt()`）も同じ処理を使う。

    Args:
        text: 入力本文。
        variable_names: そのタスクがプロンプトへ渡す変数名（`prompt_variable_names()`）。

    Raises:
        PromptTemplateError: 構文エラー・未定義の名前・サンドボックスの拒否など。
    """
    check_syntax(text)
    comments = find_comments(text)
    rendered = await render_template(
        text, dummy_task_variables(variable_names), notes_reader=_dummy_note
    )
    return TemplatePreview(rendered=rendered, comments=comments)


async def validate_task_prompt(text: str, variable_names: list[str]) -> None:
    """保存しようとしている定期タスクのプロンプトを試しに展開して確かめる。

    中身は `preview_task_prompt()` と同じ（プレビューと保存時の検査をそろえるため）。

    Raises:
        PromptTemplateError: 構文エラーか、展開できないとき。
    """
    await preview_task_prompt(text, variable_names)
