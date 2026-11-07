"""`lilla_core.handlers.dashboard_extensions` のテスト。

拡張の上書き編集 API を、本物の aiohttp アプリケーションに対してテストクライアントで
確かめる。リポジトリ・スケジューラへの反映はモックに差し替える。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from lilla_core.handlers import dashboard_extensions, task_handler

PROMPTS = {"ja": "日本語のデフォルト"}


def _tool(schedule: str, extension: str | None) -> dict:
    """定期タスクのエントリを作る。"""
    return {"instance": SimpleNamespace(schedule=schedule), "scheduled": True, "extension": extension}


TOOLS = {
    "task_a": _tool("0 22 * * *", "diary"),
    "task_core": _tool("0 2 * * *", None),
}


@pytest.fixture
def repo(monkeypatch) -> MagicMock:
    """上書きのリポジトリのモック（`doc` を返し、`update_overrides` を記録する）。"""
    repo = MagicMock()
    repo.doc = None
    repo.find_one = AsyncMock(side_effect=lambda name: repo.doc)
    repo.update_overrides = AsyncMock()
    monkeypatch.setattr(
        "lilla_core.repository.extension_override_repository.get_extension_override_repo",
        lambda: repo,
    )
    return repo


@pytest.fixture
def apply(monkeypatch) -> AsyncMock:
    """スケジューラへの反映のモック（既定は反映済み）。"""
    mock = AsyncMock(return_value=task_handler.APPLY_APPLIED)
    monkeypatch.setattr(task_handler, "apply_task_overrides", mock)
    monkeypatch.setattr(task_handler, "get_applied_schedules", lambda: {"task_a": "0 22 * * *"})
    monkeypatch.setattr(task_handler, "is_scheduler_running", lambda: True)
    return mock


@pytest.fixture(autouse=True)
def registered(make_extension, use_extensions, monkeypatch):
    """`diary`（プロンプトとタスクあり）と `plain`（何も無し）を登録し、ロケールを固定する。"""
    use_extensions(make_extension("diary", default_prompt=PROMPTS), make_extension("plain"))
    cfg = SimpleNamespace(ui=SimpleNamespace(locale="ja", timezone="Asia/Tokyo"))
    monkeypatch.setattr("lilla_core.core.config.get_config", lambda: cfg)


@pytest.fixture
async def client(repo, apply):
    """編集 API だけを載せたアプリのクライアント（認証はここでは見ない）。"""
    app = web.Application()
    dashboard_extensions.register_routes(app, TOOLS)
    client = TestClient(TestServer(app))
    await client.start_server()
    yield client
    await client.close()


class TestList:
    """GET /api/admin/extensions"""

    async def test_lists_loaded_extensions(self, client) -> None:
        """ロード済みの拡張を、編集できるかの目印つきで返す。"""
        res = await client.get("/api/admin/extensions")

        assert res.status == 200
        items = {item["name"]: item for item in (await res.json())["items"]}
        assert items["diary"]["editable"] is True
        assert items["diary"]["task_count"] == 1
        assert items["plain"]["editable"] is False


class TestDetail:
    """GET /api/admin/extensions/{name}"""

    async def test_returns_detail(self, client, repo) -> None:
        """プロンプトとタスクのデフォルト・上書き・実効値を返す。"""
        repo.doc = {"extension": "diary", "prompt": "上書き"}

        res = await client.get("/api/admin/extensions/diary")

        assert res.status == 200
        data = await res.json()
        assert data["prompt"]["effective"] == "上書き"
        assert data["prompt"]["default"] == PROMPTS["ja"]
        assert [task["name"] for task in data["tasks"]] == ["task_a"]
        assert data["tasks"][0]["applied_schedule"] == "0 22 * * *"

    async def test_unknown_extension_is_404(self, client) -> None:
        """ロードしていない拡張は 404。"""
        res = await client.get("/api/admin/extensions/missing")

        assert res.status == 404

    async def test_read_failure_is_503(self, client, repo) -> None:
        """上書きの読み込みに失敗したら、空の上書きと取り違えないよう 503。"""
        repo.find_one.side_effect = RuntimeError("db down")

        res = await client.get("/api/admin/extensions/diary")

        assert res.status == 503


class TestUpdate:
    """PATCH /api/admin/extensions/{name}/overrides"""

    async def test_prompt_save_does_not_apply_tasks(self, client, repo, apply) -> None:
        """プロンプトだけの保存はプロンプトのキーだけを書き、スケジューラには触れない。"""
        res = await client.patch("/api/admin/extensions/diary/overrides", json={"prompt": ""})

        assert res.status == 200
        data = await res.json()
        assert data["saved"] is True
        assert data["tasks_apply"] is None
        repo.update_overrides.assert_awaited_once_with("diary", {"prompt": ""}, [])
        apply.assert_not_awaited()

    async def test_task_save_applies_strictly(self, client, repo, apply) -> None:
        """タスクを保存したら、その拡張の上書きを strict に反映して結果を返す。"""
        res = await client.patch(
            "/api/admin/extensions/diary/overrides",
            json={"tasks": {"task_a": {"schedule": "15 21 * * *", "enabled": None}}},
        )

        assert res.status == 200
        data = await res.json()
        assert data["tasks_apply"] == "applied"
        assert data["detail"]["name"] == "diary"
        repo.update_overrides.assert_awaited_once_with(
            "diary", {"tasks.task_a.schedule": "15 21 * * *"}, ["tasks.task_a.enabled"]
        )
        apply.assert_awaited_once_with("diary", strict=True)

    async def test_apply_failure_is_not_success(self, client, apply) -> None:
        """保存できても反映に失敗したら、`tasks_apply` で失敗を伝える。"""
        apply.return_value = task_handler.APPLY_FAILED

        res = await client.patch(
            "/api/admin/extensions/diary/overrides",
            json={"tasks": {"task_a": {"enabled": False}}},
        )

        data = await res.json()
        assert data["saved"] is True
        assert data["tasks_apply"] == "failed"

    async def test_invalid_request_saves_nothing(self, client, repo, apply) -> None:
        """1 か所でも不正なら 400 で、保存も反映もしない。"""
        res = await client.patch(
            "/api/admin/extensions/diary/overrides",
            json={"prompt": "ok", "tasks": {"task_core": {"enabled": False}}},
        )

        assert res.status == 400
        assert "task_core" in (await res.json())["error"]
        repo.update_overrides.assert_not_awaited()
        apply.assert_not_awaited()

    async def test_invalid_json_is_400(self, client, repo) -> None:
        """JSON として読めないボディは 400。"""
        res = await client.patch(
            "/api/admin/extensions/diary/overrides",
            data="{", headers={"Content-Type": "application/json"},
        )

        assert res.status == 400
        repo.update_overrides.assert_not_awaited()

    async def test_unknown_extension_is_404(self, client, repo) -> None:
        """ロードしていない拡張は 404 で保存しない。"""
        res = await client.patch("/api/admin/extensions/missing/overrides", json={"prompt": "x"})

        assert res.status == 404
        repo.update_overrides.assert_not_awaited()

    async def test_save_failure_does_not_apply(self, client, repo, apply) -> None:
        """保存に失敗したら 500 で、反映しない。"""
        repo.update_overrides.side_effect = RuntimeError("db down")

        res = await client.patch(
            "/api/admin/extensions/diary/overrides",
            json={"tasks": {"task_a": {"enabled": False}}},
        )

        assert res.status == 500
        apply.assert_not_awaited()


class TestApply:
    """POST /api/admin/extensions/{name}/tasks/apply"""

    async def test_reapply(self, client, apply) -> None:
        """保存済みの上書きを strict に反映し直す。"""
        res = await client.post("/api/admin/extensions/diary/tasks/apply")

        assert res.status == 200
        assert (await res.json())["status"] == "applied"
        apply.assert_awaited_once_with("diary", strict=True)

    async def test_reapply_failure_is_503(self, client, apply) -> None:
        """反映に失敗したら 503。"""
        apply.return_value = task_handler.APPLY_FAILED

        res = await client.post("/api/admin/extensions/diary/tasks/apply")

        assert res.status == 503

    async def test_extension_without_tasks_is_404(self, client, apply) -> None:
        """定期タスクを持たない拡張は 404。"""
        res = await client.post("/api/admin/extensions/plain/tasks/apply")

        assert res.status == 404
        apply.assert_not_awaited()


class TestAuthentication:
    """ダッシュボードの認証ミドルウェアの内側に載ること。"""

    async def test_unauthenticated_requests_are_rejected(self, repo, apply, monkeypatch) -> None:
        """セッションが無ければ、どの編集 API も 401 で処理しない。"""
        import importlib
        import sys

        # 他のテストが `web` をモックへ差し替えたモジュールを残しうるため、読み直す
        monkeypatch.delitem(sys.modules, "lilla_core.handlers.dashboard_server", raising=False)
        dashboard_server = importlib.import_module("lilla_core.handlers.dashboard_server")

        monkeypatch.setattr(dashboard_server, "_get_valid_session", AsyncMock(return_value=None))
        app = web.Application(middlewares=[dashboard_server.auth_middleware])
        dashboard_extensions.register_routes(app, TOOLS)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            for method, path, body in (
                ("GET", "/api/admin/extensions", None),
                ("GET", "/api/admin/extensions/diary", None),
                ("PATCH", "/api/admin/extensions/diary/overrides", {"prompt": "x"}),
                ("POST", "/api/admin/extensions/diary/tasks/apply", None),
            ):
                res = await client.request(method, path, json=body)
                assert res.status == 401, path
        finally:
            await client.close()
        repo.update_overrides.assert_not_awaited()
        apply.assert_not_awaited()
