"""`lilla_core.extensions.notes.git_http`（notes の Git smart HTTP）のテスト。

共有 HTTP サーバーのアプリケーション（認証ミドルウェアつき）に notes のルートを載せて listen し、
本物の `git` コマンドで clone / fetch / push する。`git` が無い環境では該当のテストを飛ばす。
"""
from __future__ import annotations

import asyncio
import gzip
import os
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from lilla_core.extensions import notes
from lilla_core.extensions.notes import git_http, service
from lilla_core.handlers import http_server

TOKEN = "git-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@pytest.fixture
def notes_config() -> notes.NotesConfig:
    """push の検査と大量削除の通知を確かめやすい小さめの設定。"""
    return notes.NotesConfig(max_content_chars=200, deletion_alert_lines=3)


@pytest.fixture
async def server(notes_repo, notes_config, monkeypatch: pytest.MonkeyPatch, alert):
    """notes のルートを載せた共有 HTTP サーバーを listen し、テストクライアントを返す。"""
    cfg = MagicMock()
    cfg.http.cors_allowed_origins = []
    monkeypatch.setattr(http_server, "get_config", lambda: cfg)
    monkeypatch.setattr(
        http_server, "verify_client_token", AsyncMock(side_effect=lambda t: t == TOKEN)
    )
    monkeypatch.setattr(http_server, "get_http_routes", git_http.build_routes)
    monkeypatch.setattr(git_http, "_notes_repo", lambda: notes_repo)
    monkeypatch.setattr(git_http, "_load_config", lambda: notes_config)
    client = TestClient(TestServer(http_server.build_http_app()))
    await client.start_server()
    yield client
    await client.close()


@pytest.fixture
def git_env(tmp_path: Path) -> dict[str, str]:
    """利用者の設定・プロキシに左右されない `git` の環境変数。"""
    env = {
        k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")
    }
    home = tmp_path / "home"
    home.mkdir()
    env.update(
        HOME=str(home),
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_TERMINAL_PROMPT="0",
        GIT_AUTHOR_NAME="Tester",
        GIT_AUTHOR_EMAIL="tester@example.invalid",
        GIT_COMMITTER_NAME="Tester",
        GIT_COMMITTER_EMAIL="tester@example.invalid",
        NO_PROXY="*",
        no_proxy="*",
    )
    return env


@pytest.fixture
def git(git_env, tmp_path: Path):
    """`git` を実行して `(終了コード, 標準出力 + 標準エラー)` を返す関数を返す。"""

    async def _run(*args: str, cwd: Path | None = None, token: str | None = TOKEN) -> tuple[int, str]:
        options = ["-c", "init.defaultBranch=main"]
        if token is not None:
            options += ["-c", f"http.extraHeader=Authorization: Bearer {token}"]
        proc = await asyncio.create_subprocess_exec(
            "git", *options, *args,
            cwd=str(cwd or tmp_path),
            env=git_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        return proc.returncode, out.decode("utf-8", "replace")

    return _run


def _url(server: TestClient) -> str:
    """notes の Git の URL を返す。"""
    return str(server.make_url(git_http.GIT_HTTP_PREFIX))


async def _clone(git, server: TestClient, tmp_path: Path, name: str = "work") -> Path:
    """clone して作業ディレクトリを返す。"""
    code, out = await git("clone", _url(server), name)
    assert code == 0, out
    return tmp_path / name


async def _commit(git, work: Path, files: dict[str, str | None], message: str = "edit") -> None:
    """作業ディレクトリのファイルを書き換えて（None は削除）コミットする。"""
    for path, content in files.items():
        target = work / path
        if content is None:
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    code, out = await git("add", "-A", cwd=work)
    assert code == 0, out
    code, out = await git("commit", "-q", "-m", message, cwd=work)
    assert code == 0, out


@requires_git
class TestCloneAndFetch:
    """clone / fetch。"""

    async def test_clone_sees_notes(self, server, git, tmp_path: Path) -> None:
        await service.write_note("user/memo.md", "hello")
        work = await _clone(git, server, tmp_path)
        assert (work / "user" / "memo.md").read_text(encoding="utf-8") == "hello"

    async def test_fetch_sees_new_writes(self, server, git, tmp_path: Path) -> None:
        await service.write_note("user/memo.md", "v1")
        work = await _clone(git, server, tmp_path)
        await service.write_note("user/memo.md", "v2")
        code, out = await git("pull", "-q", cwd=work)
        assert code == 0, out
        assert (work / "user" / "memo.md").read_text(encoding="utf-8") == "v2"

    async def test_clone_empty_repository(self, server, git, tmp_path: Path) -> None:
        work = await _clone(git, server, tmp_path)
        assert (work / ".git").is_dir()


@requires_git
class TestPush:
    """push の成功と拒否。"""

    async def test_push_to_empty_repository(self, server, git, tmp_path: Path, notes_repo) -> None:
        work = await _clone(git, server, tmp_path)
        await _commit(git, work, {"user/a.md": "first"})
        code, out = await git("push", "origin", "HEAD:main", cwd=work)
        assert code == 0, out
        assert (await notes_repo.get("user/a.md")).content == "first"

    async def test_push_updates_index(self, server, git, tmp_path: Path, notes_repo) -> None:
        await service.write_note("user/a.md", "one")
        work = await _clone(git, server, tmp_path)
        await _commit(git, work, {"user/a.md": "two", "user/b.md": "new"})
        code, out = await git("push", cwd=work)
        assert code == 0, out
        assert (await notes_repo.get("user/a.md")).content == "two"
        assert [m.path for m in await notes_repo.list_under("user")] == ["user/a.md", "user/b.md"]
        matches, _ = await service.search("", "new")
        assert [m.path for m in matches] == ["user/b.md"]
        # push のあとのリラ側の書き込みも新しい先頭に続く
        await service.write_note("user/c.md", "after")
        code, out = await git("pull", "-q", cwd=work)
        assert code == 0, out
        assert (work / "user" / "c.md").exists()

    async def test_push_to_declared_directory(
        self, server, git, tmp_path: Path, notes_repo, journal_dir
    ) -> None:
        await service.write_note("user/a.md", "one")
        work = await _clone(git, server, tmp_path)
        await _commit(git, work, {"journal/day.md": "ok"})
        code, out = await git("push", cwd=work)
        assert code == 0, out
        assert (await notes_repo.get("journal/day.md")).content == "ok"

    async def test_push_deleting_root_file_is_allowed(
        self, server, git, tmp_path: Path, notes_repo
    ) -> None:
        await notes_repo.commit({"legacy.md": "old", "user/a.md": "a"}, "create: legacy.md")
        work = await _clone(git, server, tmp_path)
        await _commit(git, work, {"legacy.md": None})
        code, out = await git("push", cwd=work)
        assert code == 0, out
        assert await notes_repo.get("legacy.md") is None

    async def test_push_waits_for_write_lock(
        self, server, git, tmp_path: Path, notes_repo
    ) -> None:
        await service.write_note("user/a.md", "one")
        work = await _clone(git, server, tmp_path)
        await _commit(git, work, {"user/a.md": "two"})
        async with notes_repo.write_lock:
            push = asyncio.create_task(git("push", cwd=work))
            await asyncio.sleep(0.5)
            assert not push.done()
            assert (await notes_repo.get("user/a.md")).content == "one"
        code, out = await push
        assert code == 0, out
        assert (await notes_repo.get("user/a.md")).content == "two"

    async def test_rejects_other_ref(self, server, git, tmp_path: Path, notes_repo) -> None:
        await service.write_note("user/a.md", "one")
        work = await _clone(git, server, tmp_path)
        await _commit(git, work, {"user/a.md": "two"})
        code, out = await git("push", "origin", "HEAD:refs/heads/other", cwd=work)
        assert code != 0
        assert "only refs/heads/main can be pushed" in out
        assert (await notes_repo.get("user/a.md")).content == "one"

    async def test_rejects_ref_deletion(self, server, git, tmp_path: Path, notes_repo) -> None:
        await service.write_note("user/a.md", "one")
        work = await _clone(git, server, tmp_path)
        code, out = await git("push", "origin", ":main", cwd=work)
        assert code != 0
        assert "deleting refs/heads/main is not allowed" in out
        assert (await notes_repo.get("user/a.md")).content == "one"

    async def test_rejects_non_fast_forward(self, server, git, tmp_path: Path, notes_repo) -> None:
        await service.write_note("user/a.md", "one")
        await service.write_note("user/a.md", "two")
        work = await _clone(git, server, tmp_path)
        code, out = await git("reset", "-q", "--hard", "HEAD~1", cwd=work)
        assert code == 0, out
        await _commit(git, work, {"user/a.md": "rewritten"})
        code, out = await git("push", "--force", cwd=work)
        assert code != 0
        assert "non-fast-forward updates are not allowed" in out
        assert (await notes_repo.get("user/a.md")).content == "two"

    async def test_rejects_root_file(self, server, git, tmp_path: Path, notes_repo) -> None:
        await service.write_note("user/a.md", "one")
        work = await _clone(git, server, tmp_path)
        await _commit(git, work, {"root.md": "no"})
        code, out = await git("push", cwd=work)
        assert code != 0
        assert "path is not writable: root.md" in out
        assert await notes_repo.get("root.md") is None

    async def test_rejects_undeclared_top_level(
        self, server, git, tmp_path: Path, notes_repo
    ) -> None:
        await service.write_note("user/a.md", "one")
        work = await _clone(git, server, tmp_path)
        await _commit(git, work, {"user/b.md": "ok", "secret/x.md": "no"})
        code, out = await git("push", cwd=work)
        assert code != 0
        assert "path is not writable: secret/x.md" in out
        # 書ける変更が一緒でも push 全体を断る
        assert await notes_repo.get("user/b.md") is None

    async def test_rejects_bad_path_in_intermediate_commit(
        self, server, git, tmp_path: Path, notes_repo
    ) -> None:
        await service.write_note("user/a.md", "one")
        work = await _clone(git, server, tmp_path)
        await _commit(git, work, {"root.md": "no"}, "add")
        await _commit(git, work, {"root.md": None}, "remove")
        code, out = await git("push", cwd=work)
        assert code != 0
        assert "path is not writable: root.md" in out

    async def test_rejects_too_large_file(self, server, git, tmp_path: Path, notes_repo) -> None:
        await service.write_note("user/a.md", "one")
        work = await _clone(git, server, tmp_path)
        await _commit(git, work, {"user/big.md": "x" * 201})
        code, out = await git("push", cwd=work)
        assert code != 0
        assert "file is too large: user/big.md" in out

    async def test_rejects_binary_file(self, server, git, tmp_path: Path) -> None:
        await service.write_note("user/a.md", "one")
        work = await _clone(git, server, tmp_path)
        (work / "user" / "bin.dat").write_bytes(b"\xff\xfe\x00")
        await _commit(git, work, {})
        code, out = await git("push", cwd=work)
        assert code != 0
        assert "file is not UTF-8 text: user/bin.dat" in out

    async def test_rejects_symlink(self, server, git, tmp_path: Path) -> None:
        await service.write_note("user/a.md", "one")
        work = await _clone(git, server, tmp_path)
        (work / "user" / "link.md").symlink_to("a.md")
        await _commit(git, work, {})
        code, out = await git("push", cwd=work)
        assert code != 0
        assert "only regular files are allowed: user/link.md" in out

    @pytest.fixture
    def plain_messages(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """文言はキーと引数をそのまま並べた形にする（カタログの中身に依らず確かめるため）。"""
        from lilla_core.ui import messages

        monkeypatch.setattr(
            messages,
            "t",
            lambda key, **params: " ".join([key, *(f"{k}={v}" for k, v in sorted(params.items()))]),
        )

    async def test_large_deletion_alerts(
        self, server, git, tmp_path: Path, alert, plain_messages
    ) -> None:
        await service.write_note("user/a.md", "1\n2\n3\n4\n")
        await service.write_note("user/b.md", "x\n")
        work = await _clone(git, server, tmp_path)
        await _commit(git, work, {"user/a.md": None, "user/b.md": None})
        code, out = await git("push", cwd=work)
        assert code == 0, out
        alert.assert_awaited_once()
        message = alert.await_args.args[1]
        assert message.startswith("notes.alert.large_deletion_files ")
        assert "files=2" in message and "removed=5" in message
        assert alert.await_args.kwargs["attachment_text"].count("-1\n") == 1

    async def test_small_deletion_does_not_alert(self, server, git, tmp_path: Path, alert) -> None:
        await service.write_note("user/a.md", "1\n2\n3\n")
        work = await _clone(git, server, tmp_path)
        await _commit(git, work, {"user/a.md": "1\n2\n3\n4\n"})
        code, out = await git("push", cwd=work)
        assert code == 0, out
        alert.assert_not_awaited()

    async def test_alert_failure_keeps_push(self, server, git, tmp_path: Path, alert, notes_repo) -> None:
        alert.side_effect = RuntimeError("discord down")
        await service.write_note("user/a.md", "1\n2\n3\n4\n")
        work = await _clone(git, server, tmp_path)
        await _commit(git, work, {"user/a.md": None})
        code, out = await git("push", cwd=work)
        assert code == 0, out
        assert await notes_repo.get("user/a.md") is None


@requires_git
class TestAuthentication:
    """認証（共有 HTTP サーバーの Bearer）。"""

    async def test_clone_without_token_fails(self, server, git, tmp_path: Path) -> None:
        code, _ = await git("clone", _url(server), "work", token=None)
        assert code != 0
        assert not (tmp_path / "work").exists()

    async def test_clone_with_wrong_token_fails(self, server, git, tmp_path: Path) -> None:
        code, _ = await git("clone", _url(server), "work", token="wrong")
        assert code != 0


class TestHttpErrors:
    """HTTP の層の応答（`git` は使わない）。"""

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/notes.git/info/refs?service=git-upload-pack"),
            ("POST", "/notes.git/git-upload-pack"),
            ("POST", "/notes.git/git-receive-pack"),
        ],
    )
    async def test_unauthenticated_is_401(self, server, method: str, path: str) -> None:
        resp = await server.request(method, path)
        assert resp.status == 401
        assert await resp.json() == {"error": "unauthorized"}

    async def test_basic_auth_is_401(self, server) -> None:
        resp = await server.get(
            "/notes.git/info/refs?service=git-upload-pack",
            headers={"Authorization": "Basic eDp4"},
        )
        assert resp.status == 401

    async def test_info_refs_without_service_is_403(self, server) -> None:
        resp = await server.get("/notes.git/info/refs", headers=AUTH)
        assert resp.status == 403

    @pytest.mark.parametrize("service_name", ["git-upload-pack", "git-receive-pack"])
    async def test_advertises_both_services(self, server, service_name: str) -> None:
        resp = await server.get(f"/notes.git/info/refs?service={service_name}", headers=AUTH)
        assert resp.status == 200
        assert resp.content_type == f"application/x-{service_name}-advertisement"
        assert f"# service={service_name}".encode() in await resp.read()

    async def test_wrong_content_type_is_415(self, server) -> None:
        resp = await server.post(
            "/notes.git/git-upload-pack", data=b"0000", headers={**AUTH, "Content-Type": "text/plain"}
        )
        assert resp.status == 415

    async def test_gzip_request_body(self, server) -> None:
        await service.write_note("user/a.md", "one")
        resp = await server.post(
            "/notes.git/git-upload-pack",
            data=gzip.compress(b"0000"),
            headers={
                **AUTH,
                "Content-Type": "application/x-git-upload-pack-request",
                "Content-Encoding": "gzip",
            },
        )
        assert resp.status == 200
        assert resp.content_type == "application/x-git-upload-pack-result"

    async def test_too_large_body_is_413(self, server, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(git_http, "MAX_REQUEST_BYTES", 10)
        resp = await server.post(
            "/notes.git/git-receive-pack",
            data=b"x" * 100,
            headers={**AUTH, "Content-Type": "application/x-git-receive-pack-request"},
        )
        assert resp.status == 413


class TestCheckRefUpdate:
    """ref の更新の検査（`check_ref_update`）。"""

    @pytest.fixture
    async def repo_with_head(self, notes_repo):
        """コミットを 2 つ持つリポジトリと、その 2 つの ID を返す。"""
        from dulwich.repo import Repo

        first = (await notes_repo.commit({"user/a.md": "1"}, "create: user/a.md")).commit_id
        second = (await notes_repo.commit({"user/a.md": "2"}, "overwrite: user/a.md")).commit_id
        repo = Repo(str(notes_repo.path))
        yield repo, first.encode(), second.encode()
        repo.close()

    def test_rejects_stale_old_value(self, repo_with_head) -> None:
        repo, first, second = repo_with_head
        reason = git_http.check_ref_update(repo, second, b"refs/heads/main", first, second)
        assert reason == "remote main has changed; fetch and try again"

    def test_rejects_creating_when_main_exists(self, repo_with_head) -> None:
        repo, _, second = repo_with_head
        reason = git_http.check_ref_update(repo, second, b"refs/heads/main", b"0" * 40, second)
        assert reason == "remote main has changed; fetch and try again"

    def test_rejects_missing_commit(self, repo_with_head) -> None:
        repo, _, second = repo_with_head
        reason = git_http.check_ref_update(repo, second, b"refs/heads/main", second, b"1" * 40)
        assert reason == "pushed commit is missing"

    def test_accepts_fast_forward(self, repo_with_head) -> None:
        repo, first, second = repo_with_head
        assert git_http.check_ref_update(repo, first, b"refs/heads/main", first, second) is None

    def test_rejects_backwards(self, repo_with_head) -> None:
        repo, first, second = repo_with_head
        reason = git_http.check_ref_update(repo, second, b"refs/heads/main", second, first)
        assert reason == "non-fast-forward updates are not allowed"


class TestCheckPushedCommits:
    """push で増えるコミットの中身の検査（`check_pushed_commits`）。"""

    async def test_rejects_non_nfc_path(self, notes_repo) -> None:
        from dulwich.repo import Repo

        import unicodedata

        nfd = unicodedata.normalize("NFD", "user/が.md")
        # NotesRepository は NFC を前提にしないので、そのまま Git に書ける
        head = (await notes_repo.commit({nfd: "x"}, "create: x")).commit_id
        repo = Repo(str(notes_repo.path))
        try:
            reason = git_http.check_pushed_commits(repo, None, head.encode(), 100)
        finally:
            repo.close()
        assert reason is not None and reason.startswith("path must be NFC-normalized")

    async def test_ignores_commits_already_on_main(self, notes_repo) -> None:
        from dulwich.repo import Repo

        old = (await notes_repo.commit({"legacy.md": "x"}, "create: legacy.md")).commit_id
        new = (await notes_repo.commit({"user/a.md": "y"}, "create: user/a.md")).commit_id
        repo = Repo(str(notes_repo.path))
        try:
            assert git_http.check_pushed_commits(repo, old.encode(), new.encode(), 100) is None
            assert git_http.check_pushed_commits(repo, None, new.encode(), 100) is not None
        finally:
            repo.close()


class TestNotesExtensionRoutes:
    """`NotesExtension.http_routes()` の申告。"""

    def test_routes_use_bearer(self) -> None:
        routes = notes.extension.http_routes()
        assert {(r.method, r.path) for r in routes} == {
            ("GET", "/notes.git/info/refs"),
            ("POST", "/notes.git/git-upload-pack"),
            ("POST", "/notes.git/git-receive-pack"),
        }
        assert {r.auth for r in routes} == {"bearer"}
