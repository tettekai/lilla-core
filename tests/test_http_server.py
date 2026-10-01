"""`lilla_core.handlers.http_server` のテスト。

共有 HTTP サーバーの認証ミドルウェア・CORS・コアのルート・拡張ルートの載せ方を、
本物の aiohttp アプリケーションに対してテストクライアントで確かめる。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from lilla_core.core import extension as ext_module
from lilla_core.handlers import http_server
from lilla_core.services.system_checks import CheckResult
from lilla_core.testing import use_extensions

VALID_TOKEN = "valid-token"
AUTH = {"Authorization": f"Bearer {VALID_TOKEN}"}


@pytest.fixture
def mock_cfg() -> MagicMock:
    """http_server が参照する設定のモック（CORS は既定で無効）。"""
    cfg = MagicMock()
    cfg.http.host = "127.0.0.1"
    cfg.http.port = 0
    cfg.http.cors_allowed_origins = []
    return cfg


@pytest.fixture
def mock_verify(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """トークン照合を差し替える（`VALID_TOKEN` だけを発行済みとみなす）。"""
    mock = AsyncMock(side_effect=lambda token: token == VALID_TOKEN)
    monkeypatch.setattr(http_server, "verify_client_token", mock)
    return mock


@pytest.fixture
def mock_mongo_check(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """生存確認の MongoDB チェックを差し替える（既定は成功）。"""
    mock = AsyncMock(return_value=CheckResult(name="mongodb", ok=True, detail="ok", elapsed_ms=1))
    monkeypatch.setattr(http_server, "check_mongodb", mock)
    return mock


@pytest.fixture(autouse=True)
def patched(monkeypatch: pytest.MonkeyPatch, mock_cfg: MagicMock, mock_verify, mock_mongo_check):
    """設定・トークン照合・MongoDB チェックをまとめて差し替える。"""
    monkeypatch.setattr(http_server, "get_config", lambda: mock_cfg)
    yield


@pytest.fixture
async def make_client():
    """アプリケーションを組み立ててテストクライアントを返すファクトリ（後片付けつき）。"""
    clients: list[TestClient] = []

    async def _make(tools: dict | None = None, llm_tools: dict | None = None, bot=None):
        app = http_server.build_http_app(tools, llm_tools, bot)
        client = TestClient(TestServer(app))
        await client.start_server()
        clients.append(client)
        return client

    yield _make
    for client in clients:
        await client.close()


@pytest.fixture
async def client(make_client):
    """拡張ルートの無いアプリケーションのクライアント。"""
    return await make_client()


def _handler(body: str):
    """固定の本文を返す aiohttp ハンドラーを作る。"""

    async def handler(request: web.Request) -> web.Response:
        return web.Response(text=body)

    return handler


# ---------------------------------------------------------------------------
# TestLiveness
# ---------------------------------------------------------------------------


class TestLiveness:
    """`GET /` の生存確認。"""

    async def test_ok_without_token(self, client: TestClient) -> None:
        """公開ルートなのでトークン無しで 200 `{"status": "ok"}` を返す。"""
        resp = await client.get("/")

        assert resp.status == 200
        assert await resp.json() == {"status": "ok"}

    async def test_unhealthy_hides_details(
        self, client: TestClient, mock_mongo_check: AsyncMock
    ) -> None:
        """MongoDB に届かなければ 503 で、詳細は本文に出さない。"""
        mock_mongo_check.return_value = CheckResult(
            name="mongodb", ok=False, detail="secret detail", elapsed_ms=3000
        )

        resp = await client.get("/")

        assert resp.status == 503
        assert await resp.json() == {"status": "unhealthy"}

    async def test_head_is_public_too(self, client: TestClient) -> None:
        """GET に自動で載る HEAD も同じ認証方式（公開）になる。"""
        resp = await client.head("/")

        assert resp.status == 200


# ---------------------------------------------------------------------------
# TestAuthMiddleware
# ---------------------------------------------------------------------------


class TestAuthMiddleware:
    """既定拒否の Bearer 認証。"""

    async def test_unknown_path_without_token_is_401(self, client: TestClient) -> None:
        """未登録パスもトークン無しなら 401（存在の有無を漏らさない）。"""
        resp = await client.get("/nope")

        assert resp.status == 401
        assert await resp.json() == {"error": "unauthorized"}

    async def test_unknown_path_with_token_is_404(self, client: TestClient) -> None:
        """認証を通れば未登録パスは 404。"""
        resp = await client.get("/nope", headers=AUTH)

        assert resp.status == 404

    @pytest.mark.parametrize(
        "headers",
        [{}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer wrong"}],
    )
    async def test_core_api_rejects_missing_or_bad_token(
        self, client: TestClient, headers: dict
    ) -> None:
        """ヘッダー無し・形式違い・未登録トークンはいずれも 401。"""
        resp = await client.post("/api/tools/call", json={"tool": "x"}, headers=headers)

        assert resp.status == 401

    async def test_runtask_requires_token(self, client: TestClient) -> None:
        """`/api/runtask` も未認証では届かない。"""
        resp = await client.post("/api/runtask", json={"tool": "x"})

        assert resp.status == 401


# ---------------------------------------------------------------------------
# TestToolsCall
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_execute(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """ツール実行とツール context の組み立てを差し替える。"""
    mock = AsyncMock(return_value={"success": True, "tool_name": "llm_x", "data": {"v": 1}})
    monkeypatch.setattr(http_server, "execute_tool_call", mock)
    monkeypatch.setattr(http_server, "build_tool_context", lambda: {"ctx": 1})
    return mock


LLM_TOOLS = {"llm_x": {"schema": {"function": {"name": "get_x"}}}}


class TestToolsCall:
    """`POST /api/tools/call`。"""

    async def test_calls_tool_by_key(self, make_client, mock_execute: AsyncMock) -> None:
        """レジストリのキーで呼べ、結果を `success` / `data` で返す。"""
        c = await make_client(llm_tools=LLM_TOOLS)
        resp = await c.post(
            "/api/tools/call", json={"tool": "llm_x", "parameters": {"a": 1}}, headers=AUTH
        )
        assert resp.status == 200
        assert await resp.json() == {"success": True, "tool_name": "llm_x", "data": {"v": 1}}
        mock_execute.assert_awaited_once_with("llm_x", {"a": 1}, LLM_TOOLS, {"ctx": 1})

    async def test_resolves_schema_function_name(
        self, make_client, mock_execute: AsyncMock
    ) -> None:
        """SCHEMA の関数名でもレジストリのキーへ解決して呼ぶ。"""
        c = await make_client(llm_tools=LLM_TOOLS)
        await c.post("/api/tools/call", json={"tool": "get_x"}, headers=AUTH)
        assert mock_execute.await_args.args[0] == "llm_x"

    async def test_failure_returns_error(self, make_client, mock_execute: AsyncMock) -> None:
        """ツールが失敗を返したら `success: false` と `error` を返す。"""
        mock_execute.return_value = {"success": False, "error": "boom"}
        c = await make_client(llm_tools=LLM_TOOLS)
        resp = await c.post("/api/tools/call", json={"tool": "llm_x"}, headers=AUTH)
        assert await resp.json() == {"success": False, "tool_name": "llm_x", "error": "boom"}

    async def test_unknown_tool_is_404(self, make_client, mock_execute: AsyncMock) -> None:
        """レジストリに無いツールは 404。"""
        c = await make_client(llm_tools=LLM_TOOLS)
        resp = await c.post("/api/tools/call", json={"tool": "missing"}, headers=AUTH)
        assert resp.status == 404
        mock_execute.assert_not_awaited()

    @pytest.mark.parametrize(
        "body",
        ["not json", {"tool": ""}, {"tool": "llm_x", "parameters": [1]}],
    )
    async def test_bad_body_is_400(self, make_client, mock_execute: AsyncMock, body) -> None:
        """壊れた JSON・空のツール名・オブジェクトでない parameters は 400。"""
        c = await make_client(llm_tools=LLM_TOOLS)
        if isinstance(body, str):
            resp = await c.post("/api/tools/call", data=body, headers=AUTH)
        else:
            resp = await c.post("/api/tools/call", json=body, headers=AUTH)
        assert resp.status == 400


# ---------------------------------------------------------------------------
# TestRunTask
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_run_task(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """`run_task` を差し替える。"""
    mock = AsyncMock()
    monkeypatch.setattr(http_server.runtask, "run_task", mock)
    return mock


TOOLS = {"task_x": {"trigger": "task"}, "llm_y": {"trigger": "llm"}}


class TestRunTask:
    """`POST /api/runtask`。"""

    async def test_starts_task_and_returns_202(self, make_client, mock_run_task: AsyncMock) -> None:
        """起動時に渡したレジストリと bot で `run_task` を投げ、待たずに 202 を返す。"""
        bot = MagicMock()
        c = await make_client(tools=TOOLS, bot=bot)
        resp = await c.post(
            "/api/runtask", json={"tool": " task_x ", "params": {"k": "v"}}, headers=AUTH
        )
        assert resp.status == 202
        # create_task で投げたコルーチンが走るまで 1 回譲る
        await http_server.asyncio.sleep(0)
        mock_run_task.assert_awaited_once_with("task_x", TOOLS, bot, params={"k": "v"})

    @pytest.mark.parametrize(
        ("body", "status"),
        [
            ({"tool": "missing"}, 404),
            ({"tool": "llm_y"}, 400),
            ({"tool": ""}, 400),
            ({"tool": "task_x", "params": [1]}, 400),
        ],
    )
    async def test_rejects_invalid_requests(
        self, make_client, mock_run_task: AsyncMock, body: dict, status: int
    ) -> None:
        """不在は 404、手動実行に対応しないツール・不正なボディは 400。"""
        c = await make_client(tools=TOOLS)
        resp = await c.post("/api/runtask", json=body, headers=AUTH)
        assert resp.status == status
        mock_run_task.assert_not_awaited()


# ---------------------------------------------------------------------------
# TestExtensionRoutes
# ---------------------------------------------------------------------------


class TestExtensionRoutes:
    """拡張が `http_routes()` で申告したルートの載せ方と認証方式。"""

    @pytest.fixture
    async def ext_client(self, make_client, make_extension, mock_verify: AsyncMock):
        """公開・後回し・Bearer の 3 種類のルートを申告した拡張を載せたクライアント。"""
        ext = make_extension(
            "sample",
            http_routes=[
                ext_module.HttpRoute("GET", "/media/files/{filename}", _handler("file"), auth="public"),
                ext_module.HttpRoute("GET", "/ws", _handler("ws"), auth="deferred"),
                ext_module.HttpRoute("GET", "/api/conversations", _handler("conv")),
            ],
        )
        with use_extensions(ext):
            yield await make_client()

    async def test_public_route_skips_auth(self, ext_client: TestClient, mock_verify) -> None:
        """`public` はトークン無しで届き、照合もしない（パステンプレートも使える）。"""
        resp = await ext_client.get("/media/files/a.png")

        assert resp.status == 200
        assert await resp.text() == "file"
        mock_verify.assert_not_awaited()

    async def test_deferred_route_reaches_handler(self, ext_client: TestClient) -> None:
        """`deferred` はミドルウェアを素通しし、保護はハンドラ側に任せる。"""
        resp = await ext_client.get("/ws")

        assert resp.status == 200
        assert await resp.text() == "ws"

    async def test_bearer_route_requires_token(self, ext_client: TestClient) -> None:
        """既定（`bearer`）のルートはトークン無しで 401、有効なトークンで届く。"""
        assert (await ext_client.get("/api/conversations")).status == 401

        resp = await ext_client.get("/api/conversations", headers=AUTH)
        assert resp.status == 200
        assert await resp.text() == "conv"

    async def test_core_routes_are_still_served(self, ext_client: TestClient) -> None:
        """拡張のルートを足してもコアのルートはそのまま。"""
        assert (await ext_client.get("/")).status == 200


# ---------------------------------------------------------------------------
# TestCors
# ---------------------------------------------------------------------------


class TestCors:
    """CORS ミドルウェア（認証の外側）。"""

    async def test_preflight_is_answered_before_auth(
        self, client: TestClient, mock_cfg: MagicMock, mock_verify: AsyncMock
    ) -> None:
        """許可オリジンの OPTIONS はトークン無しで 200 と CORS ヘッダーを返す。"""
        mock_cfg.http.cors_allowed_origins = ["http://localhost:3000"]

        resp = await client.options(
            "/api/tools/call", headers={"Origin": "http://localhost:3000"}
        )

        assert resp.status == 200
        assert resp.headers["Access-Control-Allow-Origin"] == "http://localhost:3000"
        assert resp.headers["Vary"] == "Origin"
        assert "Authorization" in resp.headers["Access-Control-Allow-Headers"]
        mock_verify.assert_not_awaited()

    async def test_wildcard_allows_any_origin(
        self, client: TestClient, mock_cfg: MagicMock
    ) -> None:
        """`*` を含めば全オリジンに `*` を返す。"""
        mock_cfg.http.cors_allowed_origins = ["*"]

        resp = await client.get("/", headers={"Origin": "http://elsewhere"})

        assert resp.headers["Access-Control-Allow-Origin"] == "*"

    async def test_unlisted_origin_gets_no_headers(
        self, client: TestClient, mock_cfg: MagicMock
    ) -> None:
        """許可していないオリジンには CORS ヘッダーを付けない。"""
        mock_cfg.http.cors_allowed_origins = ["http://localhost:3000"]

        resp = await client.get("/", headers={"Origin": "http://evil"})

        assert "Access-Control-Allow-Origin" not in resp.headers

    async def test_disabled_cors_sends_preflight_through_auth(self, client: TestClient) -> None:
        """許可オリジンが空なら CORS は無効で、OPTIONS も認証にかかる。"""
        resp = await client.options("/api/tools/call", headers={"Origin": "http://x"})

        assert resp.status == 401
        assert "Access-Control-Allow-Origin" not in resp.headers


# ---------------------------------------------------------------------------
# TestStartStop
# ---------------------------------------------------------------------------


class TestStartStop:
    """`start_http_server()` / `stop_http_server()` のライフサイクル。"""

    async def test_creates_indexes_listens_and_stops(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """インデックスを作ってから listen し、停止で後片付けする（2 回目の停止は何もしない）。"""
        repo = MagicMock()
        repo.ensure_indexes = AsyncMock()
        monkeypatch.setattr(http_server, "get_client_token_repo", lambda: repo)

        await http_server.start_http_server({}, {}, None)
        try:
            repo.ensure_indexes.assert_awaited_once()
            assert http_server._runner is not None
        finally:
            await http_server.stop_http_server()

        assert http_server._runner is None
        await http_server.stop_http_server()

    async def test_stop_without_start_is_noop(self) -> None:
        """起動していなければ何もしない。"""
        assert http_server._runner is None
        await http_server.stop_http_server()
