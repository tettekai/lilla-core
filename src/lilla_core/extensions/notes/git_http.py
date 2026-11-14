"""notes の bare Git リポジトリを Git smart HTTP で公開する（clone / fetch / push）。

共有 HTTP サーバー（`handlers/http_server.py`）に `NotesExtension.http_routes()` で載せる。
認証は共有 HTTP サーバーの既定の Bearer（`HttpRoute.auth` の `bearer`）で、ハンドラは
認証を自分では見ない。

dulwich の `UploadPackHandler` / `ReceivePackHandler` を aiohttp のハンドラから直接呼ぶ
（WSGI アダプタは使わない）。dulwich の処理は同期なので、リクエストの本文を一時ファイルへ
読み込んでからスレッドで処理し、応答も一時ファイルへ書いてから送り返す。リクエストごとに
`Repo` を開き直し、`NotesRepository` が持つ `Repo` とキャッシュを別スレッドから触らない
（オブジェクトと ref はディスク上で共有するため、push の結果は次の読み取りから見える）。

push は「外からのコミット」として、LLM ツール・ダッシュボード・保存先 `notes` と同じ守りを
通す。`NotesRepository.write_lock` の中で検査から ref の更新までを行い、リラ側の書き込みと
直列化する。拒否の理由は report-status の `ng refs/heads/main <理由>` で返すため、
`git push` の出力に `! [remote rejected] main -> main (<理由>)` として出る。
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from aiohttp import web
from dulwich.diff_tree import CHANGE_DELETE, tree_changes
from dulwich.graph import can_fast_forward
from dulwich.objects import Commit
from dulwich.protocol import ReceivableProtocol
from dulwich.repo import Repo
from dulwich.server import Backend, ReceivePackHandler, UploadPackHandler
from dulwich.walk import Walker

from lilla_core.core.extension import HttpRoute

from .errors import NotesPathError
from .paths import check_writable_path, normalize_path
from .repository import BRANCH_REF

if TYPE_CHECKING:
    from lilla_core.extensions.notes import NotesConfig

logger = logging.getLogger(__name__)

#: 公開するパスの接頭辞。
GIT_HTTP_PREFIX = "/notes.git"
#: 扱うサービス。
UPLOAD_PACK = "git-upload-pack"
RECEIVE_PACK = "git-receive-pack"
#: リクエストの本文（展開後も同じ）の上限（バイト）。超えたら 413。
MAX_REQUEST_BYTES = 256 * 1024 * 1024
#: 本文・応答を一時ファイルへ移すまでにメモリに置く大きさ（バイト）。
_SPOOL_BYTES = 8 * 1024 * 1024
#: 応答を送るときの 1 回の大きさ（バイト）。
_CHUNK_BYTES = 64 * 1024
#: 1 回の push で検査するコミットの上限（超えたら push を断る）。
MAX_PUSH_COMMITS = 10_000
#: 受け付けるファイルのモード（通常ファイルと実行可能ファイル。シンボリックリンク・サブモジュールは断る）。
_FILE_MODES = (0o100644, 0o100755)
#: 削除・未作成を表す ID。
_ZERO_SHA = b"0" * 40


class _RequestTooLarge(Exception):
    """リクエストの本文が上限を超えた。"""


class _SingleRepoBackend(Backend):
    """どのパスでも 1 つのリポジトリを返す dulwich のバックエンド。"""

    def __init__(self, repo: Repo) -> None:
        self._repo = repo

    def open_repository(self, path: str) -> Any:
        """パスによらず保持しているリポジトリを返す。"""
        return self._repo


@dataclass(frozen=True)
class PushResult:
    """push 1 回の結果（応答と、`main` が実際に進んだか）。"""

    #: 送り返す応答の本文（一時ファイル。読み終えたら閉じる）。
    response: IO[bytes]
    #: `main` が進む前のコミット（無ければ None）。
    old_head: bytes | None
    #: `main` が進んだ後のコミット（進まなかったら None）。
    new_head: bytes | None


# --- push の検査（純粋な処理） -------------------------------------------------


def check_ref_update(
    repo: Repo, current_head: bytes | None, ref: bytes, old_sha: bytes, new_sha: bytes
) -> str | None:
    """ref の更新を受け付けてよいかを確かめ、だめなら理由を返す（よければ None）。

    `main` だけを受け付け、削除・fast-forward でない更新・古い `main` を元にした更新は断る。
    中身（パス・本文）の検査は `check_pushed_commits` が行う。

    Args:
        repo: 送られたオブジェクトを取り込み済みのリポジトリ。
        current_head: いまの `main` の指すコミット（無ければ None）。
        ref: 更新する ref の名前。
        old_sha: クライアントが前提にした古い値（作成なら 0 埋め）。
        new_sha: 新しい値（削除なら 0 埋め）。
    """
    if ref != BRANCH_REF:
        return f"only {BRANCH_REF.decode()} can be pushed"
    if new_sha == _ZERO_SHA:
        return f"deleting {BRANCH_REF.decode()} is not allowed"
    expected = current_head if current_head is not None else _ZERO_SHA
    if old_sha != expected:
        return "remote main has changed; fetch and try again"
    try:
        commit = repo.object_store[new_sha]
    except KeyError:
        return "pushed commit is missing"
    if not isinstance(commit, Commit):
        return "pushed object is not a commit"
    if current_head is not None and not can_fast_forward(repo, current_head, new_sha):
        return "non-fast-forward updates are not allowed"
    return None


def check_pushed_commits(
    repo: Repo, old_head: bytes | None, new_head: bytes, max_content_chars: int
) -> str | None:
    """push で増えるコミットのすべての変更が書ける内容かを確かめ、だめなら理由を返す。

    中間のコミットだけに書けないパスを含んでも履歴に残るため、先端のツリーだけでなく、
    `old_head` から届かない全コミットを第 1 親（ルートのコミットは空のツリー）と比べる。
    追加・変更したファイルには通常の書き込みと同じ規則をかける: パスの形式（NFC で正規化
    済みであること）・書ける場所（`check_writable_path`）・通常ファイルであること・
    UTF-8 のテキストであること・`max_content_chars` 以下であること。削除は通常の削除と
    同じくどこでも許す。ファイルとディレクトリの食い違いは Git のツリーの構造上起きない。
    """
    exclude = [old_head] if old_head is not None else None
    walker = Walker(repo.object_store, [new_head], exclude=exclude, max_entries=MAX_PUSH_COMMITS + 1)
    commits = [entry.commit for entry in walker]
    if len(commits) > MAX_PUSH_COMMITS:
        return f"too many commits in one push (limit {MAX_PUSH_COMMITS})"
    checked: set[bytes] = set()
    for commit in commits:
        parent_tree = repo.object_store[commit.parents[0]].tree if commit.parents else None
        for change in tree_changes(repo.object_store, parent_tree, commit.tree):
            if change.type == CHANGE_DELETE:
                continue
            reason = _check_entry(repo, change.new.path, change.new.mode, change.new.sha,
                                  max_content_chars, checked)
            if reason is not None:
                return f"{reason} (commit {commit.id.decode()[:12]})"
    return None


def _check_entry(
    repo: Repo, raw_path: bytes, mode: int, sha: bytes, max_content_chars: int, checked: set[bytes]
) -> str | None:
    """追加・変更された 1 つのファイルを確かめ、だめなら理由を返す。"""
    try:
        path = raw_path.decode("utf-8")
    except UnicodeDecodeError:
        return "path is not valid UTF-8"
    try:
        if normalize_path(path) != path:
            return f"path must be NFC-normalized: {path}"
        check_writable_path(path)
    except NotesPathError:
        return f"path is not writable: {path} (allowed: {_allowed_dirs()})"
    if mode not in _FILE_MODES:
        return f"only regular files are allowed: {path}"
    if sha in checked:
        return None
    try:
        text = repo.object_store[sha].data.decode("utf-8")
    except UnicodeDecodeError:
        return f"file is not UTF-8 text: {path}"
    if len(text) > max_content_chars:
        return f"file is too large: {path} (limit {max_content_chars} characters)"
    checked.add(sha)
    return None


def _allowed_dirs() -> str:
    """書けるトップレベルディレクトリを `user/, diary/` の形で返す。"""
    from .paths import writable_top_level_dirs

    return ", ".join(f"{name}/" for name in writable_top_level_dirs())


class _GuardedReceivePackHandler(ReceivePackHandler):
    """ref を更新する直前（dulwich の update フック）で notes の守りをかける receive-pack。

    送られたオブジェクトは検査の前にストアへ取り込まれる（dulwich の仕様）。断った push の
    オブジェクトはどの ref からも届かないまま残るが、次の gc で消える。
    """

    max_content_chars: int = 0

    def _on_update(self, ref_name: bytes, old_sha: bytes, new_sha: bytes) -> bytes | None:
        """更新を確かめ、断るなら理由を返す（dulwich がそのまま `ng <ref> <理由>` にする）。"""
        current = _head(self.repo)
        reason = check_ref_update(self.repo, current, ref_name, old_sha, new_sha)
        if reason is None:
            reason = check_pushed_commits(self.repo, current, new_sha, self.max_content_chars)
        if reason is not None:
            logger.info("Rejected notes push to %s: %s", ref_name.decode(errors="replace"), reason)
            return reason.encode("utf-8")
        return None


def _head(repo: Repo) -> bytes | None:
    """`main` の指すコミットを返す（無ければ None）。"""
    try:
        return repo.refs[BRANCH_REF]
    except KeyError:
        return None


# --- 同期の処理（スレッドで動かす） -----------------------------------------------


def _spool() -> IO[bytes]:
    """本文・応答を置く一時ファイルを作る。"""
    return tempfile.SpooledTemporaryFile(max_size=_SPOOL_BYTES)


def _protocol(body: IO[bytes] | None, out: IO[bytes]) -> ReceivableProtocol:
    """本文から読み、`out` へ書く dulwich のプロトコルを作る。"""

    def write(data: bytes) -> int:
        out.write(data)
        return len(data)

    read = body.read if body is not None else (lambda size=-1: b"")
    return ReceivableProtocol(read, write)


def _advertise_sync(repo_path: Path, service: str) -> IO[bytes]:
    """`info/refs?service=...` の応答（ref の広告）を作る。"""
    out = _spool()
    repo = Repo(str(repo_path))
    try:
        handler_cls = UploadPackHandler if service == UPLOAD_PACK else ReceivePackHandler
        proto = _protocol(None, out)
        handler = handler_cls(
            _SingleRepoBackend(repo), [b"/"], proto, stateless_rpc=True, advertise_refs=True
        )
        proto.write_pkt_line(b"# service=" + service.encode("ascii") + b"\n")
        proto.write_pkt_line(None)
        handler.handle()
    finally:
        repo.close()
    out.seek(0)
    return out


def _upload_pack_sync(repo_path: Path, body: IO[bytes]) -> IO[bytes]:
    """clone / fetch（`git-upload-pack`）を処理する。"""
    out = _spool()
    repo = Repo(str(repo_path))
    try:
        handler = UploadPackHandler(
            _SingleRepoBackend(repo), [b"/"], _protocol(body, out), stateless_rpc=True
        )
        handler.handle()
    finally:
        repo.close()
    out.seek(0)
    return out


def _receive_pack_sync(repo_path: Path, body: IO[bytes], max_content_chars: int) -> PushResult:
    """push（`git-receive-pack`）を守りつきで処理する（`write_lock` の中で呼ぶこと）。"""
    out = _spool()
    repo = Repo(str(repo_path))
    try:
        old_head = _head(repo)
        handler = _GuardedReceivePackHandler(
            _SingleRepoBackend(repo), [b"/"], _protocol(body, out), stateless_rpc=True
        )
        handler.max_content_chars = max_content_chars
        handler.handle()
        head = _head(repo)
    finally:
        repo.close()
    out.seek(0)
    return PushResult(out, old_head, head if head != old_head else None)


# --- aiohttp のハンドラ ---------------------------------------------------------


def _load_config() -> NotesConfig:
    """`extensions.notes` の設定を返す。"""
    from lilla_core.extensions.notes.service import load_config

    return load_config()


def _notes_repo() -> Any:
    """`NotesRepository` のシングルトンを返す（テストで差し替えやすいよう関数にしておく）。"""
    from lilla_core.extensions.notes.repository import get_notes_repo

    return get_notes_repo()


async def _open_repo_path() -> Path:
    """リポジトリを開いて（無ければ作って）そのパスを返す。"""
    repo = _notes_repo()
    await repo.init_repository()
    return repo.path


async def _read_body(request: web.Request) -> IO[bytes]:
    """リクエストの本文を一時ファイルへ読んで返す。

    `Content-Encoding: gzip`（Git が大きな要求で使う）は aiohttp が読み取り時に展開するため、
    ここで数えるのは展開後の大きさになる。

    Raises:
        _RequestTooLarge: 本文（展開後）が `MAX_REQUEST_BYTES` を超える場合。
    """
    spool = _spool()
    size = 0
    async for chunk in request.content.iter_chunked(_CHUNK_BYTES):
        size += len(chunk)
        if size > MAX_REQUEST_BYTES:
            spool.close()
            raise _RequestTooLarge()
        spool.write(chunk)
    spool.seek(0)
    return spool


async def _send(request: web.Request, body: IO[bytes], content_type: str) -> web.StreamResponse:
    """一時ファイルの中身を応答として送る（送り終えたら閉じる）。"""
    response = web.StreamResponse(
        headers={
            "Content-Type": content_type,
            "Cache-Control": "no-cache, max-age=0, must-revalidate",
            "Expires": "Fri, 01 Jan 1980 00:00:00 GMT",
            "Pragma": "no-cache",
        }
    )
    await response.prepare(request)
    try:
        while True:
            chunk = await asyncio.to_thread(body.read, _CHUNK_BYTES)
            if not chunk:
                break
            await response.write(chunk)
    finally:
        body.close()
    await response.write_eof()
    return response


def _error(status: int, message: str) -> web.Response:
    """Git クライアントにも読める短いテキストのエラー応答を返す。"""
    return web.Response(status=status, text=message + "\n", content_type="text/plain")


async def handle_info_refs(request: web.Request) -> web.StreamResponse:
    """`GET /notes.git/info/refs?service=...` — ref を広告する（smart HTTP だけ）。"""
    service = request.query.get("service")
    if service not in (UPLOAD_PACK, RECEIVE_PACK):
        return _error(403, "only smart HTTP (git-upload-pack / git-receive-pack) is supported")
    repo_path = await _open_repo_path()
    out = await asyncio.to_thread(_advertise_sync, repo_path, service)
    return await _send(request, out, f"application/x-{service}-advertisement")


def _check_content_type(request: web.Request, service: str) -> web.Response | None:
    """Git の smart HTTP の Content-Type でなければ 415 を返す。"""
    if request.content_type != f"application/x-{service}-request":
        return _error(415, "invalid Content-Type")
    return None


async def handle_upload_pack(request: web.Request) -> web.StreamResponse:
    """`POST /notes.git/git-upload-pack` — clone / fetch。

    読み取りだけなので `write_lock` は取らない（オブジェクトは不変で、ref の更新は原子的）。
    """
    if (bad := _check_content_type(request, UPLOAD_PACK)) is not None:
        return bad
    try:
        body = await _read_body(request)
    except _RequestTooLarge:
        return _error(413, "request body is too large")
    repo_path = await _open_repo_path()
    try:
        out = await asyncio.to_thread(_upload_pack_sync, repo_path, body)
    finally:
        body.close()
    return await _send(request, out, f"application/x-{UPLOAD_PACK}-result")


async def handle_receive_pack(request: web.Request) -> web.StreamResponse:
    """`POST /notes.git/git-receive-pack` — push（守りつき）。

    検査から ref の更新までを `write_lock` の中で行う。`main` が進んだら、通常の書き込みと
    同じ大量削除の通知を出す（通知の失敗で push の成否は変えない）。
    """
    if (bad := _check_content_type(request, RECEIVE_PACK)) is not None:
        return bad
    try:
        body = await _read_body(request)
    except _RequestTooLarge:
        return _error(413, "request body is too large")
    config = _load_config()
    repo = _notes_repo()
    await repo.init_repository()
    try:
        async with repo.write_lock:
            result = await asyncio.to_thread(
                _receive_pack_sync, repo.path, body, config.max_content_chars
            )
    finally:
        body.close()
    if result.new_head is not None:
        logger.info(
            "Notes main updated by git push: %s -> %s",
            result.old_head.decode()[:12] if result.old_head else "(none)",
            result.new_head.decode()[:12],
        )
        if result.old_head is not None:
            await _alert_push(repo.path, result.old_head, result.new_head, config)
    return await _send(request, result.response, f"application/x-{RECEIVE_PACK}-result")


def _push_changes_sync(repo_path: Path, old_head: bytes, new_head: bytes) -> list[tuple[str, str, str]]:
    """push の前後で中身の変わったファイルを `(パス, 前, 後)` で返す（追加は前が空、削除は後が空）。"""
    repo = Repo(str(repo_path))
    try:
        store = repo.object_store
        changes: list[tuple[str, str, str]] = []
        for change in tree_changes(store, store[old_head].tree, store[new_head].tree):
            if change.old.sha is None:  # 追加では消える行が無い
                continue
            path = change.old.path.decode("utf-8", "replace")
            before = store[change.old.sha].data.decode("utf-8", "replace")
            after = (
                "" if change.type == CHANGE_DELETE
                else store[change.new.sha].data.decode("utf-8", "replace")
            )
            changes.append((path, before, after))
        return changes
    finally:
        repo.close()


async def _alert_push(repo_path: Path, old_head: bytes, new_head: bytes, config: NotesConfig) -> None:
    """push で消えた行の合計が閾値以上なら知らせる（失敗は WARNING ログだけ）。"""
    from lilla_core.extensions.notes.service import alert_if_large_deletion_changes

    try:
        changes = await asyncio.to_thread(_push_changes_sync, repo_path, old_head, new_head)
    except Exception as e:  # 通知の失敗で push の結果を変えない
        logger.warning("Failed to read notes push changes for alert: %s", e)
        return
    await alert_if_large_deletion_changes(changes, new_head.decode("ascii"), config)


def build_routes() -> list[HttpRoute]:
    """共有 HTTP サーバーへ載せるルート（認証は既定の Bearer）を返す。"""
    return [
        HttpRoute("GET", f"{GIT_HTTP_PREFIX}/info/refs", handle_info_refs),
        HttpRoute("POST", f"{GIT_HTTP_PREFIX}/{UPLOAD_PACK}", handle_upload_pack),
        HttpRoute("POST", f"{GIT_HTTP_PREFIX}/{RECEIVE_PACK}", handle_receive_pack),
    ]
