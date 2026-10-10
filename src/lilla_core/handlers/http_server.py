"""機械向けクライアントの共有 HTTP サーバー。

観測用ダッシュボード（``handlers/dashboard_server.py``。人がパスワードでログインする
管理画面）とは別のポート・別の認証で、ホストのアプリ（モバイル / デスクトップの
クライアントなど）やローカルのスクリプトが HTTP で叩く入口になる。

コアが持つのは次の 4 本だけで、クライアント固有のルート（メディア配信・WebSocket など）は
拡張が ``Extension.http_routes()`` で申告して載せる。

- ``GET /``: 生存確認（liveness。公開）
- ``GET /api/selftest``: 自己診断（``!selftest`` 相当。Bearer 必須）
- ``POST /api/tools/call``: LLM ツールの直接呼び出し（Bearer 必須）
- ``POST /api/runtask``: task ツールの手動実行（Bearer 必須）

認証は既定拒否の Bearer トークン（``auth_middleware``）。ルートごとに申告された
``HttpRoute.auth`` を見て、``public`` は素通し、``deferred`` はハンドラ側の別方式に
任せ、それ以外（未登録パスを含む）は ``Authorization: Bearer <token>`` を
``repository/client_token_repository.py`` の ``verify_client_token`` で照合する。
トークンは DB に SHA-256 ハッシュだけを持ち、発行の仕組み（CLI など）はホストが用意する。

listen 先と CORS は ``lilla.yaml`` の ``http:``（``HttpConfig``）で決める。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import web

from lilla_core.commands import runtask
from lilla_core.core.config import get_config
from lilla_core.core.extension import HttpRoute, build_tool_context, get_http_routes
from lilla_core.handlers.request_params import parse_json_body
from lilla_core.loaders.llm_tool_loader import execute_tool_call
from lilla_core.repository.client_token_repository import (
    get_client_token_repo,
    verify_client_token,
)
from lilla_core.services.system_checks import check_mongodb, run_selftest_checks

logger = logging.getLogger(__name__)

#: ``GET /api/selftest`` の ``full`` クエリで「真」と解釈する値（大文字小文字は区別しない）。
#: 値を書かない ``?full`` も真として扱う（aiohttp は値なしのクエリを空文字で渡すため）。
_FULL_TRUE_VALUES = frozenset({"", "1", "true", "yes", "on"})

#: 起動時に渡された LLM ツールのレジストリ。拡張が `http_routes()` で載せたハンドラが
#: LLM にツールを渡したいときなど、`request.app[LLM_TOOLS_KEY]` で読んでよい公開名
#: （文字列 `"llm_tools"` では別キーになるため読めない。`AppKey` の中身・格納の仕方は
#: 変えないこと）。
LLM_TOOLS_KEY = web.AppKey("llm_tools", dict)
#: 起動時に渡された task ツールのレジストリと Discord クライアント。拡張のハンドラには
#: 渡さない（コアのハンドラだけが読む）。
TOOLS_KEY = web.AppKey("tools", dict)
BOT_KEY = web.AppKey("bot", object)
#: ルートオブジェクト → 認証方式（`HttpRoute.auth`）。載っていないルート（未登録パス）は Bearer。
ROUTE_AUTH_KEY = web.AppKey("route_auth", dict)

_CORS_ALLOW_METHODS = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
_CORS_ALLOW_HEADERS = "Content-Type, Authorization"

#: `asyncio.create_task` で投げた手動実行タスクへの参照（完了前に GC されないよう保持する）。
_background_tasks: set[asyncio.Task] = set()

#: 起動中のサーバー。`stop_http_server()` が後片付けに使う。
_runner: web.AppRunner | None = None


@web.middleware
async def cors_middleware(request: web.Request, handler) -> web.StreamResponse:
    """CORS ヘッダーを付与するミドルウェア。認証ミドルウェアの **外側** に置く。

    ``http.cors_allowed_origins`` が空なら何もしない。``"*"`` を含めば
    ``Access-Control-Allow-Origin: *`` を、それ以外は ``Origin`` が一致したときだけ
    そのオリジンを返す。``OPTIONS`` のプリフライトは認証より前に 200 で即答する
    （ブラウザはプリフライトに ``Authorization`` を付けないため、内側に置くと 401 になる）。

    Args:
        request: aiohttp のリクエスト。
        handler: 後続のハンドラー。

    Returns:
        CORS ヘッダーを付けた（または付けない）レスポンス。
    """
    origins = get_config().http.cors_allowed_origins
    if not origins:
        return await handler(request)

    if request.method == "OPTIONS":
        resp: web.StreamResponse = web.Response(status=200)
    else:
        resp = await handler(request)

    # WebSocket など送信済みのレスポンスにはヘッダーを足せない
    if resp.prepared:
        return resp

    origin = request.headers.get("Origin", "")
    if "*" in origins:
        resp.headers["Access-Control-Allow-Origin"] = "*"
    elif origin in origins:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
    else:
        return resp

    resp.headers["Access-Control-Allow-Methods"] = _CORS_ALLOW_METHODS
    resp.headers["Access-Control-Allow-Headers"] = _CORS_ALLOW_HEADERS
    return resp


@web.middleware
async def auth_middleware(request: web.Request, handler) -> web.StreamResponse:
    """既定拒否の Bearer トークン認証ミドルウェア。

    マッチしたルートの認証方式が ``public`` / ``deferred`` なら素通しする。
    それ以外（``bearer`` と、どのルートにもマッチしない未登録パス）は
    ``Authorization: Bearer <token>`` が必須で、ヘッダー無し・形式違い・未登録
    トークンはいずれも 401 を返す。

    Args:
        request: aiohttp のリクエスト。
        handler: 後続のハンドラー。

    Returns:
        後続ハンドラーのレスポンス、または 401 の JSON レスポンス。
    """
    route_auth: dict = request.app[ROUTE_AUTH_KEY]
    mode = route_auth.get(request.match_info.route, "bearer")
    if mode in ("public", "deferred"):
        return await handler(request)

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return web.json_response({"error": "unauthorized"}, status=401)

    token = auth_header.removeprefix("Bearer ")
    if not await verify_client_token(token):
        return web.json_response({"error": "unauthorized"}, status=401)

    return await handler(request)


async def handle_root(request: web.Request) -> web.Response:
    """生存確認（liveness）エンドポイント（公開）。

    Docker / systemd などが「再起動すべきか」を判断する材料として使うため、
    再起動で直らない問題（コマンドレジストリ・タスクツール・LLM 疎通など）は
    見ない。チェック項目は MongoDB 疎通のみ（3 秒タイムアウト付き）。

    認証の掛かっていない公開エンドポイントのため、本文は ``{"status": ...}`` のみとし、
    チェックの詳細やエラー内容は外に出さない（失敗時の詳細はサーバーログにだけ出す）。

    Args:
        request: aiohttp のリクエスト（未使用）。

    Returns:
        疎通できていれば 200 ``{"status": "ok"}``、失敗していれば
        503 ``{"status": "unhealthy"}``。
    """
    result = await check_mongodb()
    if not result.ok:
        logger.error("[HEALTH] MongoDB connectivity check failed: %s", result.detail)
    return web.json_response(
        {"status": "ok" if result.ok else "unhealthy"},
        status=200 if result.ok else 503,
    )


async def handle_api_selftest(request: web.Request) -> web.Response:
    """GET /api/selftest - 自己診断（``!selftest`` / ``!selftest full`` 相当。Bearer 必須）。

    ``?full=true`` を付けると LLM 疎通確認も実行する（**LLM API の課金が 1 往復分
    発生する**）。値を書かない ``?full`` も同じ扱いで、``full=false`` のような
    ``_FULL_TRUE_VALUES`` に無い値は通常モードになる。

    チェックの組み合わせは ``services/system_checks.py`` の ``run_selftest_checks()``
    に持たせて ``!selftest`` と共有するため、Discord と HTTP で結果が食い違わない。
    このエンドポイントはコンテナやプロセスに対して何も作用せず、結果を報告するだけ。

    公開の生存確認（``GET /``）とは役割が別で、チェックの詳細（``detail``）を返すのは
    認証の内側であるこちらだけ。

    Args:
        request: aiohttp のリクエスト。``full`` クエリパラメータだけを読む。

    Returns:
        全チェックが成功なら 200、1 件でも失敗していれば 503 の JSON。本文は
        ``!selftest`` の Discord 本文（要約）と添付ファイル（詳細）に相当する
        ``{"ok", "mode", "summary", "checks"}``。
    """
    full_mode = request.query.get("full", "false").strip().lower() in _FULL_TRUE_VALUES

    tools = request.app[TOOLS_KEY]
    results = await run_selftest_checks(tools, full_mode)

    ok_count = sum(1 for r in results if r.ok)
    all_ok = ok_count == len(results)
    return web.json_response(
        {
            "ok": all_ok,
            "mode": "full" if full_mode else "normal",
            "summary": {"ok": ok_count, "total": len(results)},
            "checks": [
                {
                    "name": r.name,
                    "ok": r.ok,
                    "detail": r.detail,
                    "elapsed_ms": r.elapsed_ms,
                }
                for r in results
            ],
        },
        status=200 if all_ok else 503,
    )


async def handle_api_tools_call(request: web.Request) -> web.Response:
    """POST /api/tools/call - 指定された LLM ツールを直接呼び出す。

    ボディは ``{"tool": "<名前>", "parameters": {...}}``。名前は YAML の stem
    （レジストリのキー）か、SCHEMA の関数名のどちらでもよい（キー一致を優先）。
    実行 context は ``build_tool_context()``（拡張の ``tool_context_providers()``）。

    Args:
        request: aiohttp のリクエスト。

    Returns:
        ``{"success", "tool_name", "data"}`` または ``{"success", "tool_name", "error"}``
        の JSON。ボディ不正は 400、ツール不在は 404。
    """
    body, ok = await parse_json_body(request)
    if not ok or not isinstance(body, dict):
        return web.Response(status=400, text="Invalid JSON body")

    tool_name = body.get("tool", "")
    if not isinstance(tool_name, str) or not tool_name:
        return web.Response(status=400, text="'tool' field is required")

    parameters = body.get("parameters") or {}
    if not isinstance(parameters, dict):
        return web.Response(status=400, text="'parameters' must be an object")

    llm_tools = request.app[LLM_TOOLS_KEY]

    # キー一致 → schema name 一致の順で検索
    if tool_name not in llm_tools:
        for key, entry in llm_tools.items():
            if entry["schema"].get("function", {}).get("name") == tool_name:
                tool_name = key
                break
        else:
            return web.Response(status=404, text=f"Tool '{tool_name}' not found")

    context = build_tool_context()
    result = await execute_tool_call(tool_name, parameters, llm_tools, context)

    if result.get("success"):
        return web.json_response({
            "success": True,
            "tool_name": result.get("tool_name", tool_name),
            "data": result.get("data"),
        })
    return web.json_response({
        "success": False,
        "tool_name": result.get("tool_name", tool_name),
        "error": result.get("error", "Unknown error"),
    })


async def handle_api_runtask(request: web.Request) -> web.Response:
    """POST /api/runtask - task ツールを非同期で起動する（``!runtask`` 相当）。

    ボディは ``{"tool": "<名前>", "params": {...}}``。実行は待たずに 202 を返す。

    Args:
        request: aiohttp のリクエスト。

    Returns:
        受け付けたら 202。ボディ不正・手動実行に対応しないツールは 400、ツール不在は 404。
    """
    tools = request.app[TOOLS_KEY]
    bot = request.app[BOT_KEY]

    body, ok = await parse_json_body(request)
    if not ok or not isinstance(body, dict):
        return web.Response(status=400, text="Invalid JSON body")

    tool_name = body.get("tool", "")
    if not isinstance(tool_name, str) or not tool_name.strip():
        return web.Response(status=400, text="'tool' field is required")
    tool_name = tool_name.strip()

    if tool_name not in tools:
        return web.Response(status=404, text=f"Tool '{tool_name}' not found")

    if tools[tool_name]["trigger"] != "task":
        return web.Response(
            status=400, text=f"Tool '{tool_name}' does not support manual execution"
        )

    params = body.get("params", {})
    if not isinstance(params, dict):
        return web.Response(status=400, text="'params' must be an object")

    task = asyncio.create_task(runtask.run_task(tool_name, tools, bot, params=params))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return web.Response(status=202, text="Accepted")


#: コアが持つルート。`core/extension.py` の `CORE_HTTP_ROUTES` と一致させること
#: （拡張の申告との重なりはそちらで検査する）。
_CORE_ROUTES: tuple[HttpRoute, ...] = (
    HttpRoute("GET", "/", handle_root, auth="public"),
    HttpRoute("GET", "/api/selftest", handle_api_selftest),
    HttpRoute("POST", "/api/tools/call", handle_api_tools_call),
    HttpRoute("POST", "/api/runtask", handle_api_runtask),
)


def _add_route(app: web.Application, route: HttpRoute) -> None:
    """1 本のルートを載せ、認証方式をルートオブジェクトごとに記録する。

    GET は HEAD も自動で載る（aiohttp の既定）ため、同じ認証方式を両方に記録する。

    Args:
        app: 載せる先のアプリケーション。
        route: 載せるルート。
    """
    route_auth: dict = app[ROUTE_AUTH_KEY]
    added = app.router.add_route(route.method, route.path, route.handler)
    route_auth[added] = route.auth
    if route.method == "GET":
        head = app.router.add_route("HEAD", route.path, route.handler)
        route_auth[head] = route.auth


def build_http_app(
    tools: dict | None = None, llm_tools: dict | None = None, bot: Any = None
) -> web.Application:
    """共有 HTTP サーバーの aiohttp アプリケーションを組み立てる（listen はしない）。

    コアのルートに続けて、拡張が ``http_routes()`` で申告したルートをロード順に載せる。
    重なりは ``set_extensions()`` の時点で検査済み。

    Args:
        tools: task ツールのレジストリ（``/api/runtask`` が使う）。
        llm_tools: LLM ツールのレジストリ（``/api/tools/call`` が使う）。
        bot: Discord クライアント（``/api/runtask`` がツールへ渡す）。

    Returns:
        ミドルウェアとルートを設定したアプリケーション。
    """
    # cors_middleware を外側に置くこと（OPTIONS プリフライトを認証より前に返すため）
    app = web.Application(middlewares=[cors_middleware, auth_middleware])
    app[TOOLS_KEY] = tools or {}
    app[LLM_TOOLS_KEY] = llm_tools or {}
    app[BOT_KEY] = bot
    app[ROUTE_AUTH_KEY] = {}
    for route in (*_CORE_ROUTES, *get_http_routes()):
        _add_route(app, route)
    return app


async def start_http_server(
    tools: dict | None = None, llm_tools: dict | None = None, bot: Any = None
) -> None:
    """共有 HTTP サーバーを起動する。

    ``client_tokens`` のインデックスを作ってから、``http.host`` / ``http.port`` で
    listen する。拡張の申告（``get_http_routes()``）を読むため、``load_extensions()``
    が終わったあとに呼ぶこと。

    Args:
        tools: task ツールのレジストリ。
        llm_tools: LLM ツールのレジストリ。
        bot: Discord クライアント。
    """
    global _runner

    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

    await get_client_token_repo().ensure_indexes()

    app = build_http_app(tools, llm_tools, bot)
    runner = web.AppRunner(app)
    await runner.setup()
    http = get_config().http
    site = web.TCPSite(runner, http.host, http.port)
    await site.start()
    _runner = runner
    logger.info("HTTP server started on %s:%d", http.host, http.port)


async def stop_http_server() -> None:
    """起動中の共有 HTTP サーバーを止める。起動していなければ何もしない。"""
    global _runner

    if _runner is None:
        return
    runner, _runner = _runner, None
    await runner.cleanup()
    logger.info("HTTP server stopped")
