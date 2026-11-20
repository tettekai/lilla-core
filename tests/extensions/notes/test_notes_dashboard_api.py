"""`lilla_core.extensions.notes.dashboard_api` のテスト。"""
from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


@pytest.fixture
async def client(notes_repo):
    """ノート画面の API だけを載せたテスト用クライアント。"""
    from lilla_core.extensions.notes.dashboard_api import build_routes

    app = web.Application()
    for route in build_routes():
        app.router.add_route(route.method, route.path, route.handler)
    async with TestClient(TestServer(app)) as test_client:
        yield test_client


class TestNotesDashboardApi:
    """作成・閲覧・検索・削除・復元。"""

    async def test_crud_flow(self, client) -> None:
        """作って、一覧・閲覧・上書き・直前の版・削除・復元ができる。"""
        res = await client.put("/api/notes/file", json={"path": "user/a.md", "content": "v1", "mode": "create"})
        assert res.status == 201
        res = await client.put("/api/notes/file", json={"path": "user/a.md", "content": "v2"})
        assert res.status == 200

        res = await client.get("/api/notes/files")
        assert [i["path"] for i in (await res.json())["items"]] == ["user/a.md"]

        res = await client.get("/api/notes/file", params={"path": "user/a.md"})
        body = await res.json()
        assert (body["content"], body["size"]) == ("v2", 2)

        res = await client.get("/api/notes/history", params={"path": "user/a.md"})
        assert (await res.json())["content"] == "v1"

        res = await client.delete("/api/notes/file", params={"path": "user/a.md"})
        assert res.status == 204
        res = await client.get("/api/notes/file", params={"path": "user/a.md"})
        assert res.status == 404

        res = await client.post("/api/notes/history/restore", json={"path": "user/a.md"})
        assert res.status == 204
        res = await client.get("/api/notes/file", params={"path": "user/a.md"})
        assert (await res.json())["content"] == "v2"

    async def test_search(self, client) -> None:
        """部分一致で検索できる。q が空なら 400。"""
        await client.put("/api/notes/file", json={"path": "user/a.md", "content": "Hello"})
        res = await client.get("/api/notes/search", params={"q": "hell"})
        assert (await res.json())["matches"] == [{"path": "user/a.md", "line": 1, "text": "Hello"}]
        res = await client.get("/api/notes/search", params={"q": " "})
        assert res.status == 400

    @pytest.mark.parametrize(
        "payload, status",
        [
            ({"path": "../a", "content": "x"}, 400),
            ({"path": "user/a.md", "content": "x", "mode": "append"}, 400),
            ({"path": "user/a.md", "content": "x" * 101}, 413),
        ],
    )
    async def test_write_errors(self, client, payload, status) -> None:
        """不正なパス・mode は 400、大きすぎるのは 413。"""
        res = await client.put("/api/notes/file", json=payload)
        assert res.status == status
        assert "error" in await res.json()

    @pytest.mark.parametrize("path", ["a.md", "other/a.md"])
    async def test_rejects_root_and_unknown_top_level(self, client, path) -> None:
        """ルート直下・許可リスト外のトップレベルへの作成は 400。"""
        res = await client.put("/api/notes/file", json={"path": path, "content": "x", "mode": "create"})
        assert res.status == 400
        assert "user/" in (await res.json())["error"]

    async def test_existing_root_file_can_be_read_and_deleted(self, client, notes_repo) -> None:
        """ルート直下の既存ファイルは閲覧・削除できる。"""
        await notes_repo.commit({"old.md": "legacy"}, "create: old.md")
        res = await client.get("/api/notes/file", params={"path": "old.md"})
        assert (await res.json())["content"] == "legacy"
        res = await client.delete("/api/notes/file", params={"path": "old.md"})
        assert res.status == 204

    async def test_create_conflict(self, client) -> None:
        """create で既にあれば 409。"""
        await client.put("/api/notes/file", json={"path": "user/a.md", "content": "x", "mode": "create"})
        res = await client.put("/api/notes/file", json={"path": "user/a.md", "content": "y", "mode": "create"})
        assert res.status == 409

    async def test_invalid_body(self, client) -> None:
        """JSON でない・オブジェクトでないボディは 400。"""
        res = await client.put("/api/notes/file", data="nope")
        assert res.status == 400
        res = await client.post("/api/notes/history/restore", json=[1])
        assert res.status == 400

    async def test_missing_history(self, client) -> None:
        """直前の版が無ければ 404。"""
        res = await client.get("/api/notes/history", params={"path": "user/a.md"})
        assert res.status == 404
