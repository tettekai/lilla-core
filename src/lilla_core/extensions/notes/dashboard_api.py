"""ダッシュボードの「ノート」画面の API（`/api/notes` 配下。セッション認証の内側）。

| メソッド | パス | 内容 |
|----------|------|------|
| GET | `/api/notes/files` | すべてのノートの一覧（本文なし。ツリー用） |
| GET | `/api/notes/file?path=` | ノート 1 件（本文つき） |
| PUT | `/api/notes/file` | 書く（`{"path", "content", "mode"}`。`mode` は `create` か `overwrite`、既定 `overwrite`） |
| DELETE | `/api/notes/file?path=` | 削除する（消す前の内容は直前の版に残る） |
| GET | `/api/notes/search?q=&path=` | 本文の部分一致検索 |
| GET | `/api/notes/history?path=` | 直前の版 |
| POST | `/api/notes/history/restore` | 直前の版へ戻す（`{"path"}`） |

パスに `/` が入るため、パス変数ではなくクエリ・ボディで受け取る。検証は LLM ツールと
同じ `service.py` を通す。失敗は `{"error": 文言}` で返す（パスの不正 400・不在 404・
既存 409・大きすぎる 413）。
"""
from __future__ import annotations

from typing import Any

from aiohttp import web

from lilla_core.core.extension import DashboardRoute
from lilla_core.handlers.request_params import parse_json_body

from . import service
from .errors import NotesError, NotesExistsError, NotesNotFoundError, NotesTooLargeError

_PREFIX = "/api/notes"


def _time(value) -> str | None:
    """datetime を ISO 8601 文字列にする（無ければ None）。"""
    return value.isoformat() if value is not None else None


def _error(status: int, message: str) -> web.Response:
    """`{"error": 文言}` の応答を返す。"""
    return web.json_response({"error": message}, status=status)


def _from_notes_error(e: NotesError) -> web.Response:
    """ノートの例外を HTTP の応答にする。"""
    if isinstance(e, NotesNotFoundError):
        return _error(404, str(e))
    if isinstance(e, NotesExistsError):
        return _error(409, str(e))
    if isinstance(e, NotesTooLargeError):
        return _error(413, str(e))
    return _error(400, str(e))


async def _json_object(request: web.Request) -> dict[str, Any] | None:
    """ボディを JSON オブジェクトとして読む（オブジェクトでなければ None）。"""
    body, ok = await parse_json_body(request)
    return body if ok and isinstance(body, dict) else None


async def handle_files(request: web.Request) -> web.Response:
    """GET /api/notes/files - すべてのノートの一覧（パス順・本文なし）を返す。"""
    entries = await service.list_all()
    return web.json_response(
        {
            "items": [
                {"path": e.path, "size": e.size, "updated_at": _time(e.modified_time)}
                for e in entries
            ]
        }
    )


async def handle_file(request: web.Request) -> web.Response:
    """GET / PUT / DELETE /api/notes/file - ノート 1 件の取得・書き込み・削除。"""
    try:
        if request.method == "GET":
            note = await service.read_note(request.query.get("path"))
            return web.json_response(
                {
                    "path": note.path,
                    "content": note.content,
                    "size": note.size,
                    "created_at": _time(note.created_at),
                    "updated_at": _time(note.updated_at),
                }
            )
        if request.method == "DELETE":
            await service.delete_note(request.query.get("path"))
            return web.Response(status=204)

        # PUT
        body = await _json_object(request)
        if body is None:
            return _error(400, "Invalid JSON body")
        mode = body.get("mode", "overwrite")
        if mode not in ("create", "overwrite"):
            return _error(400, "mode must be create or overwrite")
        result = await service.write_note(body.get("path"), body.get("content"), mode)
        return web.json_response(
            {"path": result.path, "created": result.created, "size": result.size},
            status=201 if result.created else 200,
        )
    except NotesError as e:
        return _from_notes_error(e)


async def handle_search(request: web.Request) -> web.Response:
    """GET /api/notes/search?q=&path= - 本文を部分一致で検索する。"""
    query = request.query.get("q", "")
    if not query.strip():
        return _error(400, "q is required")
    try:
        matches, truncated = await service.search(request.query.get("path", ""), query)
    except NotesError as e:
        return _from_notes_error(e)
    return web.json_response(
        {
            "matches": [{"path": m.path, "line": m.line, "text": m.text} for m in matches],
            "truncated": truncated,
        }
    )


async def handle_history(request: web.Request) -> web.Response:
    """GET /api/notes/history?path= - 直前の版を返す。"""
    try:
        version = await service.get_previous_version(request.query.get("path"))
    except NotesError as e:
        return _from_notes_error(e)
    return web.json_response(
        {
            "path": version.path,
            "content": version.content,
            "reason": version.reason,
            "saved_at": _time(version.saved_at),
        }
    )


async def handle_restore(request: web.Request) -> web.Response:
    """POST /api/notes/history/restore - 直前の版へ戻す。"""
    body = await _json_object(request)
    if body is None:
        return _error(400, "Invalid JSON body")
    try:
        await service.restore_previous_version(body.get("path"))
    except NotesError as e:
        return _from_notes_error(e)
    return web.Response(status=204)


def build_routes() -> list[DashboardRoute]:
    """ノート画面の API のルートを返す。"""
    return [
        DashboardRoute("GET", f"{_PREFIX}/files", handle_files),
        DashboardRoute("GET", f"{_PREFIX}/file", handle_file),
        DashboardRoute("PUT", f"{_PREFIX}/file", handle_file),
        DashboardRoute("DELETE", f"{_PREFIX}/file", handle_file),
        DashboardRoute("GET", f"{_PREFIX}/search", handle_search),
        DashboardRoute("GET", f"{_PREFIX}/history", handle_history),
        DashboardRoute("POST", f"{_PREFIX}/history/restore", handle_restore),
    ]
