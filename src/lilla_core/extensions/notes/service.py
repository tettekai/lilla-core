"""ノートの業務ロジック（パスの検証・大きさの上限・ファイルとディレクトリの整合・大量削除の通知）。

`NotesStorage`（コアの `FileStorage`）・LLM ツール・ダッシュボードの API が同じ関数を通る
ようにし、検証を二重に持たない。設定とリポジトリは呼び出しのたびに引く。書き込みは
リポジトリの `write_lock` の中で「検査 → コミット」を行い、1 つずつ順番に処理する。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from typing import TYPE_CHECKING, Any

from lilla_core.core.file_storage import check_write_mode

from .errors import (
    NotesError,
    NotesExistsError,
    NotesNotFoundError,
    NotesPathError,
    NotesTooLargeError,
)
from .paths import check_writable_path, child_path, normalize_path

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from lilla_core.extensions.notes import NotesConfig
    from lilla_core.extensions.notes.repository import Note, NotesRepository, NoteVersion

#: 存在しないパスの文言。
NOT_FOUND_MESSAGE = "指定したパスは存在しません。"
#: `create` で既にあるときの文言。
ALREADY_EXISTS_MESSAGE = "ファイルが既にあります（上書きするなら mode に overwrite を指定してください）。"
#: ディレクトリへ書こうとしたときの文言。
WRITE_TO_DIR_MESSAGE = "そのパスはディレクトリです。ファイルのパスを指定してください。"
#: 親の要素がファイルのときの文言。
PARENT_IS_FILE_MESSAGE = "親のパス「{parent}」がファイルのため、その下には書けません。"
#: ファイルを一覧しようとしたときの文言。
NOT_A_DIR_MESSAGE = "ディレクトリではありません。"
#: 書いた後の本文が上限を超えるときの文言。
TOO_LARGE_MESSAGE = "ファイルが大きすぎるため書けません（上限 {limit} 文字）。"
#: 直前の版が無いときの文言。
NO_PREVIOUS_VERSION_MESSAGE = "直前の版はありません。"
#: `content` が文字列でないときの文言。
INVALID_CONTENT_MESSAGE = "content を文字列で指定してください。"

#: 検索で本文を読むノートの上限（超えたら `truncated`）。
MAX_SEARCH_NOTES = 2000
#: 検索で返す該当行の上限（超えたら `truncated`）。
MAX_SEARCH_RESULTS = 50
#: 該当行として返す文字数の上限（超えた分は切り詰める）。
MAX_LINE_CHARS = 300
#: 大量削除の通知に添付するパッチの文字数の上限（超えた分は切り詰める）。
MAX_ALERT_PATCH_CHARS = 200_000


@dataclass(frozen=True)
class Entry:
    """ディレクトリの直下のエントリ 1 つ。"""

    path: str
    name: str
    #: `"file"` か `"dir"`。
    type: str
    #: ファイルの文字数（ディレクトリは None）。
    size: int | None
    #: 更新日時（ディレクトリは配下の最新）。
    modified_time: datetime | None


@dataclass(frozen=True)
class WriteResult:
    """書き込みの結果。"""

    path: str
    created: bool
    #: 書いた後の本文の文字数。
    size: int


@dataclass(frozen=True)
class SearchMatch:
    """検索で見つかった行 1 つ。"""

    path: str
    line: int
    text: str


def load_config() -> NotesConfig:
    """`extensions.notes` セクションを型付きで返す（節が無ければ既定値）。"""
    from lilla_core.core.config import get_section
    from lilla_core.extensions.notes import NotesConfig

    return get_section("notes", NotesConfig)


def _repo() -> NotesRepository:
    """リポジトリを返す（テストで差し替えやすいよう関数にしておく）。"""
    from lilla_core.extensions.notes.repository import get_notes_repo

    return get_notes_repo()


def _ancestors(path: str) -> list[str]:
    """パスの祖先（自分は含まない）を浅い順に返す。"""
    parts = path.split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts))]


async def _check_file_path(repo: NotesRepository, path: str) -> None:
    """ファイルとして書けるパスか（祖先がファイルでない・配下にノートが無い）を確かめる。

    Raises:
        NotesPathError: 書けない場合。
    """
    for parent in _ancestors(path):
        if await repo.get_meta(parent) is not None:
            raise NotesPathError(PARENT_IS_FILE_MESSAGE.format(parent=parent))
    if await repo.has_children(path):
        raise NotesPathError(WRITE_TO_DIR_MESSAGE)


async def read_note(path: Any) -> Note:
    """ノートを返す。

    Raises:
        NotesPathError: パスの形式が正しくない場合。
        NotesNotFoundError: 無い場合。
    """
    note = await _repo().get(normalize_path(path))
    if note is None:
        raise NotesNotFoundError(NOT_FOUND_MESSAGE)
    return note


async def write_note(path: Any, content: Any, mode: str = "overwrite") -> WriteResult:
    """ノートを書く（1 回の書き込みが 1 コミット。内容が変わらなければコミットしない）。

    Args:
        path: 書くパス。
        content: 書く内容。
        mode: `create` / `overwrite` / `append`。

    Raises:
        ValueError: `mode` が正しくない場合。
        NotesPathError: パスの形式が正しくない・書ける場所の外（ルート直下・許可されていない
            トップレベル）・ディレクトリを指す・親がファイルの場合。
        NotesExistsError: `create` で既にある場合。
        NotesTooLargeError: 書いた後の本文が上限を超える場合。
        NotesError: `content` が文字列でない場合。
    """
    check_write_mode(mode)
    normalized = normalize_path(path)
    check_writable_path(normalized)
    if not isinstance(content, str):
        raise NotesError(INVALID_CONTENT_MESSAGE)
    config = load_config()
    repo = _repo()

    async with repo.write_lock:
        await _check_file_path(repo, normalized)
        current = await repo.get(normalized)
        if mode == "create" and current is not None:
            raise NotesExistsError(ALREADY_EXISTS_MESSAGE)
        before = current.content if current is not None else None
        after = (before or "") + content if mode == "append" else content
        if len(after) > config.max_content_chars:
            raise NotesTooLargeError(TOO_LARGE_MESSAGE.format(limit=config.max_content_chars))
        reason = "create" if current is None else mode
        result = await repo.commit({normalized: after}, f"{reason}: {normalized}")

    if result.commit_id is not None and before is not None and mode != "append":
        await _alert_if_large_deletion(normalized, before, after, result.commit_id, config)
    return WriteResult(path=normalized, created=current is None, size=len(after))


async def list_entries(path: Any = "") -> list[Entry]:
    """ディレクトリの直下のエントリを名前順に返す（再帰しない。空文字は最上位）。

    Raises:
        NotesPathError: パスの形式が正しくない・ファイルを指す場合。
        NotesNotFoundError: ディレクトリが無い場合（最上位は空でも成功）。
    """
    directory = normalize_path(path, allow_empty=True)
    repo = _repo()
    metas = await repo.list_under(directory)
    if directory and not metas:
        if await repo.get_meta(directory) is not None:
            raise NotesPathError(NOT_A_DIR_MESSAGE)
        raise NotesNotFoundError(NOT_FOUND_MESSAGE)

    offset = len(directory) + 1 if directory else 0
    files: dict[str, Entry] = {}
    dirs: dict[str, datetime | None] = {}
    for meta in metas:
        rest = meta.path[offset:]
        name, sep, _ = rest.partition("/")
        if sep:
            latest = dirs.get(name)
            if latest is None or (meta.updated_at and meta.updated_at > latest):
                dirs[name] = meta.updated_at
        else:
            files[name] = Entry(
                path=meta.path,
                name=name,
                type="file",
                size=meta.size,
                modified_time=meta.updated_at,
            )
    entries = list(files.values()) + [
        Entry(child_path(directory, name), name, "dir", None, modified)
        for name, modified in dirs.items()
        if name not in files
    ]
    return sorted(entries, key=lambda e: e.name)


async def list_all() -> list[Entry]:
    """すべてのノート（ファイルだけ）をパス順に返す（ダッシュボードのツリー用）。"""
    return [
        Entry(m.path, m.path.rsplit("/", 1)[-1], "file", m.size, m.updated_at)
        for m in await _repo().list_under("")
    ]


async def exists(path: Any) -> bool:
    """ファイルかディレクトリがあれば True（空文字の最上位は常に True）。

    Raises:
        NotesPathError: パスの形式が正しくない場合。
    """
    normalized = normalize_path(path, allow_empty=True)
    if not normalized:
        return True
    repo = _repo()
    return await repo.get_meta(normalized) is not None or await repo.has_children(normalized)


async def search(
    path: Any,
    query: str,
    max_results: int = MAX_SEARCH_RESULTS,
) -> tuple[list[SearchMatch], bool]:
    """最新の `main` の配下のノートから `query` を含む行を探す（部分一致・大文字小文字を区別しない）。

    Args:
        path: 起点のディレクトリかファイル（空文字は全体）。
        query: 探す文字列（前後の空白を除く。空なら呼び出し側で弾く）。
        max_results: 返す行の上限。

    Returns:
        該当行（パス順・行番号順）と、上限で切り詰めたか。

    Raises:
        NotesPathError: パスの形式が正しくない場合。
    """
    directory = normalize_path(path, allow_empty=True)
    needle = query.strip()
    folded = needle.casefold()
    notes, truncated = await _repo().search(directory, needle, MAX_SEARCH_NOTES)
    matches: list[SearchMatch] = []
    for note in notes:
        for number, line in enumerate(note.content.splitlines(), start=1):
            if folded in line.casefold():
                if len(matches) >= max_results:
                    return matches, True
                matches.append(SearchMatch(note.path, number, line[:MAX_LINE_CHARS]))
    return matches, truncated


async def delete_note(path: Any) -> None:
    """ノートを削除する（消す前の内容は Git の履歴に残り、直前の版として読める）。

    Raises:
        NotesPathError: パスの形式が正しくない場合。
        NotesNotFoundError: 無い場合。
    """
    normalized = normalize_path(path)
    config = load_config()
    repo = _repo()
    async with repo.write_lock:
        current = await repo.get(normalized)
        if current is None:
            raise NotesNotFoundError(NOT_FOUND_MESSAGE)
        result = await repo.commit({normalized: None}, f"delete: {normalized}")
    if result.commit_id is not None:
        await _alert_if_large_deletion(normalized, current.content, "", result.commit_id, config)


async def get_previous_version(path: Any) -> NoteVersion:
    """直前の版（最後の変更の前の内容）を Git の履歴から返す。

    Raises:
        NotesPathError: パスの形式が正しくない場合。
        NotesNotFoundError: 直前の版が無い場合。
    """
    version = await _repo().get_version(normalize_path(path))
    if version is None:
        raise NotesNotFoundError(NO_PREVIOUS_VERSION_MESSAGE)
    return version


async def restore_previous_version(path: Any) -> None:
    """直前の版へ戻す（新しいコミットとして書く。戻す前の内容が新しい「直前の版」になる）。

    Raises:
        NotesPathError: パスの形式が正しくない・書ける場所の外（ルート直下・許可されていない
            トップレベル）・ディレクトリになっている・親がファイルの場合。
        NotesNotFoundError: 直前の版が無い場合。
        NotesTooLargeError: 直前の版が今の上限を超える場合。
    """
    normalized = normalize_path(path)
    check_writable_path(normalized)
    config = load_config()
    repo = _repo()
    async with repo.write_lock:
        await _check_file_path(repo, normalized)
        version = await repo.get_version(normalized)
        if version is None:
            raise NotesNotFoundError(NO_PREVIOUS_VERSION_MESSAGE)
        if len(version.content) > config.max_content_chars:
            raise NotesTooLargeError(TOO_LARGE_MESSAGE.format(limit=config.max_content_chars))
        current = await repo.get(normalized)
        result = await repo.commit({normalized: version.content}, f"restore: {normalized}")
    if result.commit_id is not None and current is not None:
        await _alert_if_large_deletion(
            normalized, current.content, version.content, result.commit_id, config
        )


@dataclass(frozen=True)
class DiffStats:
    """書き込みの差分の行数とパッチ。"""

    added: int
    removed: int
    patch: str


def diff_stats(path: str, before: str, after: str) -> DiffStats:
    """書き込み前後の差分（追加・削除の行数と unified diff）を返す。"""
    import difflib

    lines = list(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}" if after else "/dev/null",
        )
    )
    added = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))
    patch = "".join(line if line.endswith("\n") else line + "\n" for line in lines)
    return DiffStats(added, removed, patch)


def _resolve_bot() -> Any:
    """通知に使う Discord クライアント（共有インスタンス）を返す（無ければ None）。"""
    try:
        from lilla_core.bot_client import bot
    except Exception:  # 通知先が無くても書き込みは成功させる
        return None
    return bot


async def _alert_if_large_deletion(
    path: str, before: str, after: str, commit_id: str, config: NotesConfig
) -> None:
    """消えた行が閾値以上なら、統計・コミットハッシュ・パッチをエラー通知チャンネルへ知らせる。

    書き込みはもう終わっているので、通知の失敗は WARNING ログだけで呼び出し元へ伝えない。
    """
    threshold = config.deletion_alert_lines
    # 消えうる行数が閾値に届かなければ差分を取らない（大きなファイルの diff は重いため）
    if len(before.splitlines()) < threshold:
        return
    try:
        import asyncio

        stats = await asyncio.to_thread(diff_stats, path, before, after)
        if stats.removed < threshold:
            return
        from lilla_core.core.error_notify import notify_warning
        from lilla_core.ui.messages import t

        patch = stats.patch
        if len(patch) > MAX_ALERT_PATCH_CHARS:
            patch = patch[:MAX_ALERT_PATCH_CHARS] + "\n" + t("notes.alert.patch_truncated") + "\n"
        await notify_warning(
            _resolve_bot(),
            t(
                "notes.alert.large_deletion",
                path=path,
                removed=stats.removed,
                added=stats.added,
                commit=commit_id,
            ),
            attachment_name=f"notes-{commit_id[:12]}.patch",
            attachment_text=patch,
        )
    except Exception as e:  # 通知の失敗で書き込みの結果を変えない
        logger.warning("Failed to send large deletion alert for notes commit %s: %s", commit_id, e)
