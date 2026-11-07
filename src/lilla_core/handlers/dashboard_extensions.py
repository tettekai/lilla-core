"""観測用ダッシュボードの「拡張の上書き」編集 API。

`handlers/dashboard_server.py` の `start_dashboard_server()` が `register_routes()` で
載せる。どのルートもセッション認証の内側（`auth_middleware` が既定拒否）にある。

- ``GET /api/admin/extensions``: ロード済みの拡張と、編集できるものの目印
- ``GET /api/admin/extensions/{name}``: プロンプト・タスクのデフォルト・上書き・実効値
- ``PATCH /api/admin/extensions/{name}/overrides``: 上書きの保存（タスクを変えたら
  稼働中のスケジューラへ反映する）
- ``POST /api/admin/extensions/{name}/tasks/apply``: 保存済みのタスクの上書きの再反映
- ``PUT /api/admin/extensions/{name}/settings``: 画面管理の設定（設定の本体。
  `extension_settings`）の一式の保存

拡張の API は ``/api/{name}`` に載るため、衝突しないよう ``/api/admin/`` の下に置く。
上書きの検証・組み立ては `services/extension_overrides.py`、保存は
`repository/extension_override_repository.py`、反映は `handlers/task_handler.py`、
画面管理の設定は `services/extension_settings.py` が持つ。
"""
from __future__ import annotations

import logging

from aiohttp import web

from lilla_core.handlers.request_params import parse_json_body

logger = logging.getLogger(__name__)

#: ツールのレジストリ（`load_all_tools()` の結果）を置くアプリのキー。
#: ダッシュボードはスケジューラより先に起動するため、タスクの一覧はここから引く。
TOOLS_KEY = web.AppKey("dashboard_tools", dict)


def register_routes(app: web.Application, tools: dict | None) -> None:
    """拡張の上書き編集 API をアプリへ載せる。

    Args:
        app: ダッシュボードのアプリ。
        tools: ツールのレジストリ。`None` なら空（タスクは編集対象にならない）。
    """
    app[TOOLS_KEY] = tools or {}
    app.router.add_get("/api/admin/extensions", handle_list)
    app.router.add_get("/api/admin/extensions/{name}", handle_detail)
    app.router.add_patch("/api/admin/extensions/{name}/overrides", handle_update)
    app.router.add_post("/api/admin/extensions/{name}/tasks/apply", handle_apply)
    app.router.add_put("/api/admin/extensions/{name}/settings", handle_settings_update)


def _error(status: int, message: str) -> web.Response:
    """`{"error": message}` の JSON エラー応答を作る。"""
    return web.json_response({"error": message}, status=status)


async def _detail(name: str, tools: dict) -> dict:
    """上書きを読んで編集画面の内容を組み立てる（読み込みの失敗はそのまま伝える）。"""
    from lilla_core.core.config import get_config
    from lilla_core.handlers import task_handler
    from lilla_core.repository.extension_override_repository import get_extension_override_repo
    from lilla_core.services.extension_overrides import build_detail

    from lilla_core.services.extension_settings import build_settings_detail

    doc = await get_extension_override_repo().find_one(name)
    detail = build_detail(
        name,
        tools,
        doc,
        get_config().ui.locale,
        task_handler.get_applied_schedules(),
        task_handler.is_scheduler_running(),
        task_handler.is_scheduler_enabled(),
    )
    # 設定の読み込み失敗は節の `error` に入れ、上書きの編集は続けられるようにする
    detail["settings"] = await build_settings_detail(name)
    return detail


async def handle_list(request: web.Request) -> web.Response:
    """GET /api/admin/extensions - ロード済みの拡張を、編集できるかの目印つきで返す。"""
    from lilla_core.services.extension_overrides import list_editable

    return web.json_response({"items": list_editable(request.app[TOOLS_KEY])})


async def handle_detail(request: web.Request) -> web.Response:
    """GET /api/admin/extensions/{name} - 1 つの拡張の上書きの編集画面の内容を返す。

    ロード済みでない拡張は 404。上書きの読み込みに失敗したら 503 で、空の上書きと
    取り違えないようにする。
    """
    from lilla_core.services.extension_overrides import find_extension

    name = request.match_info["name"]
    if find_extension(name) is None:
        return _error(404, f"Extension '{name}' is not loaded")
    try:
        detail = await _detail(name, request.app[TOOLS_KEY])
    except Exception as e:
        logger.warning("Failed to load extension overrides of '%s': %s", name, e)
        return _error(503, "Failed to load extension overrides")
    return web.json_response(detail)


async def handle_update(request: web.Request) -> web.Response:
    """PATCH /api/admin/extensions/{name}/overrides - 上書きを保存する。

    リクエスト全体を検証してから 1 回の更新で保存し（不正なら 400 で何も保存しない）、
    タスクを変えたときだけ稼働中のスケジューラへ反映する。応答の `saved` は保存の成否、
    `tasks_apply` は反映の結果（`applied` / `scheduler_not_running` /
    `scheduler_disabled` / `failed`。
    タスクを変えていなければ `None`）で、保存できたが反映に失敗したことを成功と
    取り違えないよう分けて返す。保存に失敗したら 500 で、反映もしない。
    """
    from lilla_core.handlers import task_handler
    from lilla_core.repository.extension_override_repository import get_extension_override_repo
    from lilla_core.services.extension_overrides import (
        OverrideValidationError,
        find_extension,
        parse_update,
    )

    name = request.match_info["name"]
    if find_extension(name) is None:
        return _error(404, f"Extension '{name}' is not loaded")
    body, ok = await parse_json_body(request)
    if not ok:
        return _error(400, "Invalid JSON body")
    tools = request.app[TOOLS_KEY]
    try:
        update = parse_update(name, body, tools)
    except OverrideValidationError as e:
        return _error(400, str(e))

    try:
        await get_extension_override_repo().update_overrides(
            name, update.set_fields, update.unset_fields
        )
    except Exception as e:
        logger.error("Failed to save extension overrides of '%s': %s", name, e)
        return _error(500, "Failed to save extension overrides")
    logger.info(
        "Extension overrides saved: %s (set=%s, unset=%s)",
        name, sorted(update.set_fields), sorted(update.unset_fields),
    )

    tasks_apply = None
    if update.task_names:
        tasks_apply = await task_handler.apply_task_overrides(name, strict=True)

    try:
        detail = await _detail(name, tools)
    except Exception as e:
        logger.warning("Failed to reload extension overrides of '%s': %s", name, e)
        detail = None
    return web.json_response({"saved": True, "tasks_apply": tasks_apply, "detail": detail})


async def handle_apply(request: web.Request) -> web.Response:
    """POST /api/admin/extensions/{name}/tasks/apply - 保存済みのタスクの上書きを反映し直す。

    保存はできたが反映に失敗したときのやり直し用。反映に失敗したら 503、
    スケジューラが動いていなければ 200 で `scheduler_not_running`（起動時に効く）、
    `scheduler.enabled: false` なら 200 で `scheduler_disabled`（`GET /api/tasks` に出る）を返す。
    対象のタスクを持たない拡張は 404。
    """
    from lilla_core.handlers import task_handler
    from lilla_core.services.extension_overrides import find_extension, task_entries_of

    name = request.match_info["name"]
    if find_extension(name) is None:
        return _error(404, f"Extension '{name}' is not loaded")
    if not task_entries_of(name, request.app[TOOLS_KEY]):
        return _error(404, f"Extension '{name}' has no scheduled tasks")
    status = await task_handler.apply_task_overrides(name, strict=True)
    if status == task_handler.APPLY_FAILED:
        return web.json_response({"status": status}, status=503)
    return web.json_response({"status": status})


async def handle_settings_update(request: web.Request) -> web.Response:
    """PUT /api/admin/extensions/{name}/settings - 画面管理の設定の一式を保存する。

    ボディは ``{"values": {項目名: 値}}``。一式を検証してから 1 回の書き込みで保存し、
    1 か所でも不正なら 400（`errors` に項目ごとの理由）で何も保存しない。保存に
    失敗したら 500。設定を宣言していない拡張・ロードしていない拡張は 404。
    保存した値は、拡張が次に `load_settings()` を呼んだ処理から使われる
    （実行中の処理には影響しない）。
    """
    from lilla_core.services.extension_overrides import find_extension
    from lilla_core.services.extension_settings import (
        SettingsValidationError,
        build_settings_detail,
        get_settings_model,
        save_settings,
    )

    name = request.match_info["name"]
    if find_extension(name) is None:
        return _error(404, f"Extension '{name}' is not loaded")
    if get_settings_model(name) is None:
        return _error(404, f"Extension '{name}' has no dashboard-managed settings")
    body, ok = await parse_json_body(request)
    if not ok:
        return _error(400, "Invalid JSON body")
    if not isinstance(body, dict) or set(body) != {"values"}:
        return _error(400, "Request body must be an object with only 'values'")
    try:
        await save_settings(name, body["values"])
    except SettingsValidationError as e:
        return web.json_response({"error": str(e), "errors": e.errors}, status=400)
    except Exception as e:
        logger.error("Failed to save settings of extension '%s': %s", name, e)
        return _error(500, "Failed to save settings")
    logger.info("Extension settings saved: %s", name)
    return web.json_response({"saved": True, "settings": await build_settings_detail(name)})
