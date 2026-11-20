"""ノートをディスク上の bare Git リポジトリに保存するリポジトリ。

原本は設定 `extensions.notes.repo_path` の bare リポジトリの `main` ブランチだけ。作業ツリーは
置かず、dulwich でブロブ・ツリー・コミットを直接組み立てる。1 回の書き込みが 1 コミットで、
一覧などの日時はコミット時刻（committer time）から求める。ディレクトリはツリーそのもので、
空のディレクトリは無い。

読み取りは HEAD のコミットから組み立てた索引（パス → ブロブ・作成と更新の日時・最後に
変えたコミット）をメモリに持ち、HEAD が変わったら差分だけ反映する（外から履歴が書き換え
られたなど、辿れなければ作り直す）。dulwich の処理は同期なので、呼び出しはスレッドへ逃がす。

書き込みを 1 つずつ順番に処理するための `write_lock`（`asyncio.Lock`）を持つ。検査から
コミットまでを同じロックの中で行うのは呼び出し側（`service.py`）の責任。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from dulwich.diff_tree import CHANGE_ADD, CHANGE_DELETE, tree_changes
from dulwich.errors import NotGitRepository
from dulwich.index import commit_tree
from dulwich.objects import Blob, Commit
from dulwich.repo import Repo

logger = logging.getLogger(__name__)

#: ノートを置くブランチ。
BRANCH_REF = b"refs/heads/main"
#: コミットの作者・コミッター。
COMMITTER = b"Lilla notes <notes@lilla.invalid>"
#: ファイルのモード（通常ファイル）。
FILE_MODE = 0o100644
#: コミットメッセージの先頭に書く操作の種類。
REASONS = ("create", "overwrite", "append", "delete", "restore")


@dataclass(frozen=True)
class NoteMeta:
    """本文を除いたノートの情報。"""

    path: str
    size: int
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class Note:
    """ノート 1 件。"""

    path: str
    content: str
    created_at: datetime | None
    updated_at: datetime | None

    @property
    def size(self) -> int:
        """本文の文字数。"""
        return len(self.content)


@dataclass(frozen=True)
class NoteVersion:
    """直前の版 1 件。"""

    path: str
    content: str
    #: その版を置き換えた操作（`overwrite` / `append` / `delete` / `restore`）。
    reason: str
    #: 置き換えたコミットの時刻。
    saved_at: datetime | None


@dataclass(frozen=True)
class CommitResult:
    """書き込みの結果。"""

    #: 作ったコミットの ID（16 進）。変更が無ければ None。
    commit_id: str | None


@dataclass
class _Index:
    """ある HEAD の時点の索引。"""

    head: bytes | None
    #: 今あるファイルのパス → ブロブ ID。
    files: dict[str, bytes] = field(default_factory=dict)
    #: 今あるファイルのパス → (作成, 更新) の時刻。
    times: dict[str, tuple[datetime, datetime]] = field(default_factory=dict)
    #: 一度でも触れたパス → 最後に変えたコミットの ID（消したファイルも残す）。
    last_commit: dict[str, bytes] = field(default_factory=dict)

    def copy(self) -> "_Index":
        """浅いコピーを返す。"""
        return _Index(self.head, dict(self.files), dict(self.times), dict(self.last_commit))


def _commit_time(commit: Commit) -> datetime:
    """コミットのコミッター時刻を UTC の datetime で返す。"""
    return datetime.fromtimestamp(commit.commit_time, tz=timezone.utc)


def _reason_of(commit: Commit) -> str:
    """コミットメッセージの先頭から操作の種類を読む（読めなければ空文字）。"""
    head = commit.message.decode("utf-8", "replace").split(":", 1)[0].strip()
    return head if head in REASONS else ""


@lru_cache(maxsize=1)
def get_notes_repo() -> "NotesRepository":
    """NotesRepository のシングルトンを返す（初回に設定を読む）。"""
    from lilla_core.core.config import get_section
    from lilla_core.extensions.notes import NotesConfig

    return NotesRepository(get_section("notes", NotesConfig).repo_path)


class NotesRepository:
    """bare Git リポジトリのノートの読み書き。"""

    def __init__(self, repo_path: Path) -> None:
        self._path = Path(repo_path)
        self._repo: Repo | None = None
        self._open_lock = threading.Lock()
        self._index_lock = threading.Lock()
        self._index: _Index | None = None
        self._blob_sizes: dict[bytes, int] = {}
        #: 書き込みを直列化するロック（検査からコミットまでを囲む）。
        self.write_lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        """リポジトリのディレクトリ。"""
        return self._path

    # --- 初期化 -----------------------------------------------------------

    async def init_repository(self) -> None:
        """リポジトリを開く（無ければ `main` を指す bare リポジトリを作る）。"""
        await asyncio.to_thread(self._open)

    def _open(self) -> Repo:
        """リポジトリを開いて返す（初回だけ作成・検査する）。

        Raises:
            RuntimeError: パスがファイル、または空でないのに bare リポジトリでない場合。
        """
        with self._open_lock:
            if self._repo is not None:
                return self._repo
            path = self._path
            if path.exists() and not path.is_dir():
                raise RuntimeError(f"notes repository path is not a directory: {path}")
            if not path.exists() or not any(path.iterdir()):
                path.mkdir(parents=True, exist_ok=True)
                repo = Repo.init_bare(str(path), default_branch=b"main")
                logger.info("Created notes repository at %s", path)
            else:
                try:
                    repo = Repo(str(path))
                except NotGitRepository as e:
                    raise RuntimeError(
                        f"notes repository path is not empty and is not a Git repository: {path}"
                    ) from e
                if not repo.bare:
                    raise RuntimeError(f"notes repository must be a bare repository: {path}")
            self._repo = repo
            return repo

    # --- 索引 -------------------------------------------------------------

    def _head(self, repo: Repo) -> bytes | None:
        """`main` の指すコミット ID を返す（コミットが無ければ None）。"""
        try:
            return repo.refs[BRANCH_REF]
        except KeyError:
            return None

    def _snapshot(self) -> _Index:
        """今の HEAD の索引を返す（必要なら作り直す・差分を足す）。"""
        repo = self._open()
        head = self._head(repo)
        with self._index_lock:
            current = self._index
            if current is not None and current.head == head:
                return current
            index = self._advance(repo, current, head)
            self._index = index
            return index

    def _advance(self, repo: Repo, base: _Index | None, head: bytes | None) -> _Index:
        """`base` から `head` まで第 1 親を辿って索引を進める（辿れなければ作り直す）。"""
        store = repo.object_store
        stop = base.head if base is not None else None
        chain = self._first_parent_chain(store, head, stop)
        if base is not None and stop is not None and (not chain or chain[-1].parents[:1] != [stop]):
            if head != stop:
                # base の HEAD に辿り着かなかった（履歴が書き換えられた）ので最初から作る
                base = None
                chain = self._first_parent_chain(store, head, None)
        if base is None:
            index = _Index(head=None)
            prev_tree = None
        else:
            index = base.copy()
            prev_tree = store[base.head].tree if base.head is not None else None
        for commit in reversed(chain):
            when = _commit_time(commit)
            for change in tree_changes(store, prev_tree, commit.tree):
                if change.type == CHANGE_DELETE:
                    path = change.old.path.decode("utf-8")
                    index.files.pop(path, None)
                    index.times.pop(path, None)
                else:
                    path = change.new.path.decode("utf-8")
                    index.files[path] = change.new.sha
                    if change.type == CHANGE_ADD or path not in index.times:
                        index.times[path] = (when, when)
                    else:
                        index.times[path] = (index.times[path][0], when)
                index.last_commit[path] = commit.id
            prev_tree = commit.tree
        index.head = head
        return index

    @staticmethod
    def _first_parent_chain(store, head: bytes | None, stop: bytes | None) -> list[Commit]:
        """`head` から第 1 親を `stop` の手前（無ければ最初のコミット）まで新しい順に返す。"""
        chain: list[Commit] = []
        sha = head
        while sha is not None and sha != stop:
            commit = store[sha]
            chain.append(commit)
            sha = commit.parents[0] if commit.parents else None
        return chain

    def _blob_text(self, repo: Repo, sha: bytes) -> str:
        """ブロブを UTF-8 の文字列として返す（壊れたバイト列は置き換える）。"""
        return repo.object_store[sha].data.decode("utf-8", "replace")

    def _size(self, repo: Repo, sha: bytes) -> int:
        """ブロブの文字数を返す（ブロブは不変なので ID ごとに覚えておく）。"""
        size = self._blob_sizes.get(sha)
        if size is None:
            size = len(self._blob_text(repo, sha))
            self._blob_sizes[sha] = size
        return size

    # --- 読み取り ---------------------------------------------------------

    def _get_sync(self, path: str) -> Note | None:
        """`get` の同期版。"""
        index = self._snapshot()
        sha = index.files.get(path)
        if sha is None:
            return None
        created, updated = index.times[path]
        return Note(path, self._blob_text(self._open(), sha), created, updated)

    async def get(self, path: str) -> Note | None:
        """パスのノートを返す（無ければ None）。"""
        return await asyncio.to_thread(self._get_sync, path)

    def _meta_sync(self, index: _Index, path: str) -> NoteMeta:
        """索引からノートの情報を組み立てる。"""
        created, updated = index.times[path]
        return NoteMeta(path, self._size(self._open(), index.files[path]), created, updated)

    async def get_meta(self, path: str) -> NoteMeta | None:
        """パスのノートの情報（本文なし）を返す（無ければ None）。"""

        def run() -> NoteMeta | None:
            index = self._snapshot()
            return self._meta_sync(index, path) if path in index.files else None

        return await asyncio.to_thread(run)

    @staticmethod
    def _under(index: _Index, directory: str) -> list[str]:
        """ディレクトリの配下（再帰）のファイルのパスをパス順に返す（空文字は全件）。"""
        prefix = directory + "/" if directory else ""
        return sorted(p for p in index.files if p.startswith(prefix))

    async def has_children(self, directory: str) -> bool:
        """ディレクトリの配下にノートが 1 つでもあれば True（空文字は全体）。"""

        def run() -> bool:
            return bool(self._under(self._snapshot(), directory))

        return await asyncio.to_thread(run)

    async def list_under(self, directory: str) -> list[NoteMeta]:
        """ディレクトリの配下（再帰）のノートの情報をパス順に返す（空文字は全件）。"""

        def run() -> list[NoteMeta]:
            index = self._snapshot()
            return [self._meta_sync(index, p) for p in self._under(index, directory)]

        return await asyncio.to_thread(run)

    async def search(self, directory: str, query: str, limit: int) -> tuple[list[Note], bool]:
        """最新の `main` のツリーで、本文に `query` を含むノートを返す（大文字小文字を区別しない）。

        Args:
            directory: 起点のディレクトリ（空文字は全体）。ファイルのパスでもよい。
            query: 探す文字列（正規表現としては解釈しない）。
            limit: 返すノートの上限。

        Returns:
            パス順のノートと、上限で切り詰めたか。
        """

        def run() -> tuple[list[Note], bool]:
            index = self._snapshot()
            repo = self._open()
            needle = query.casefold()
            paths = self._under(index, directory)
            if directory and directory in index.files:
                paths = [directory]
            found: list[Note] = []
            for path in paths:
                content = self._blob_text(repo, index.files[path])
                if needle in content.casefold():
                    if len(found) >= limit:
                        return found, True
                    created, updated = index.times[path]
                    found.append(Note(path, content, created, updated))
            return found, False

        return await asyncio.to_thread(run)

    async def get_version(self, path: str) -> NoteVersion | None:
        """パスの直前の版（最後に変えたコミットの 1 つ前の内容）を返す（無ければ None）。

        最後の変更が新規作成なら、その前の版は無い。削除されたファイルは削除前の内容を返す。
        """

        def run() -> NoteVersion | None:
            index = self._snapshot()
            commit_id = index.last_commit.get(path)
            if commit_id is None:
                return None
            repo = self._open()
            commit = repo.object_store[commit_id]
            if not commit.parents:
                return None
            parent = repo.object_store[commit.parents[0]]
            sha = self._lookup(repo, parent.tree, path)
            if sha is None:
                return None
            return NoteVersion(
                path, self._blob_text(repo, sha), _reason_of(commit), _commit_time(commit)
            )

        return await asyncio.to_thread(run)

    @staticmethod
    def _lookup(repo: Repo, tree_id: bytes, path: str) -> bytes | None:
        """ツリーからパスのブロブ ID を引く（無い・ブロブでなければ None）。"""
        from dulwich.object_store import tree_lookup_path

        try:
            mode, sha = tree_lookup_path(repo.object_store.__getitem__, tree_id, path.encode("utf-8"))
        except KeyError:
            return None
        return sha if mode == FILE_MODE or (mode & 0o170000) == 0o100000 else None

    # --- 書き込み ---------------------------------------------------------

    def _commit_sync(self, changes: dict[str, str | None], message: str) -> CommitResult:
        """`commit` の同期版。"""
        repo = self._open()
        store = repo.object_store
        index = self._snapshot()
        files = dict(index.files)
        for path, content in changes.items():
            if content is None:
                files.pop(path, None)
                continue
            blob = Blob.from_string(content.encode("utf-8"))
            store.add_object(blob)
            files[path] = blob.id
        if files == index.files:
            return CommitResult(None)

        tree_id = commit_tree(
            store, [(p.encode("utf-8"), sha, FILE_MODE) for p, sha in sorted(files.items())]
        )
        now = int(self._now())
        commit = Commit()
        commit.tree = tree_id
        commit.parents = [index.head] if index.head is not None else []
        commit.author = commit.committer = COMMITTER
        commit.author_time = commit.commit_time = now
        commit.author_timezone = commit.commit_timezone = 0
        commit.encoding = b"UTF-8"
        commit.message = message.encode("utf-8")
        store.add_object(commit)

        if index.head is None:
            updated = repo.refs.add_if_new(BRANCH_REF, commit.id)
        else:
            updated = repo.refs.set_if_equals(BRANCH_REF, index.head, commit.id)
        if not updated:
            raise RuntimeError("notes branch was changed by another writer")
        return CommitResult(commit.id.decode("ascii"))

    @staticmethod
    def _now() -> float:
        """コミット時刻（UNIX 時刻）を返す（テストで差し替えやすいよう関数にしておく）。"""
        return time.time()

    async def commit(self, changes: dict[str, str | None], message: str) -> CommitResult:
        """変更を 1 コミットにする（値が None のパスは削除）。中身が変わらなければコミットしない。

        `write_lock` は取らない（検査と合わせて呼び出し側が取る）。

        Args:
            changes: パス → 新しい本文（None は削除）。
            message: コミットメッセージ（先頭は `REASONS` のどれか + `: ` + パス）。

        Raises:
            RuntimeError: ブランチがよそから同時に動かされた場合。
        """
        return await asyncio.to_thread(self._commit_sync, changes, message)
