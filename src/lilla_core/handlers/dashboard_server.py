"""観測用ダッシュボードの HTTP サーバー。

コアが所有する状態（会話履歴・ユーザーメモ・ログ・管理セッション）を人が見るための
窓で、`bot.py` が `main()` の中で（拡張の `setup()` を終えたあと、Discord へ
接続する前に）起動する。用途特化の HTTP サーバー（機械向けの Bearer API など）は
コアには持たず、拡張が `Extension.setup()` で自前のリスナーを起こす。

設定は `AppConfig` の `dashboard:`（`host` / `port` / `cookie_secure`）で、全項目に
既定があるため `lilla.yaml` に節が無くても起動する。

ダッシュボードは管理者本人だけが使う前提のため、パスワード認証で保護する。
認証まわりの構成は以下の通り。

- 初期設定（``POST /api/setup``）: ``admin_credentials`` が未登録のときだけ通り、
  bcrypt ハッシュを 1 件だけ登録する。登録後は 403 で恒久的にブロックする。
- ログイン（``POST /api/login``）: ``bcrypt.checkpw`` で検証し、成功したら
  ``secrets.token_urlsafe(32)`` のセッション ID を発行して SHA-256 ハッシュを
  ``admin_sessions`` に保存し、生の ID を HttpOnly Cookie で返す。
- 認証判定（``auth_middleware``）: リクエストごとに Cookie のセッションを引き、
  ``expires_at`` を毎回比較する。期限切れ・未発行はすべて 401。

画面（ナビ）は組み込みの 4 つだけでなく、拡張が ``Extension.dashboard_page()`` で
申告したページも ``GET /api/dashboard/nav`` のカタログに載せる。拡張のセッション API
（``/api/{name}``）は Cookie の内側、公開コールバック（``/oauth/{name}``）と静的ファイル
（``/static/ext/{name}/``）は外側に載せる。経路は lilla-core が ``Extension.name`` から
導出したものを使い、こちら側で組み立て直さない。
"""
import asyncio
import hashlib
import logging
import math
import secrets
import time
from datetime import datetime, timedelta
from pathlib import Path

import bcrypt
from aiohttp import web
from bson import ObjectId

from lilla_core.repository.motor_client import create_motor_client
from lilla_core.utils.datetime_utils import parse_iso_utc, utc_now

from lilla_core.handlers.request_params import parse_int_param, parse_json_body, parse_object_id
from lilla_core.core.config import get_config
from lilla_core.core.extension import (
    get_dashboard_pages,
    get_dashboard_public_routes,
    get_dashboard_routes,
    get_dashboard_static_mounts,
)

logger = logging.getLogger(__name__)

#: SPA（`index.html` / `app.js` / `style.css`）の同梱先。`locales/` と同じく
#: パッケージ内に置き、hatchling が wheel へ含める。
DASHBOARD_DIR = Path(__file__).parent.parent / "dashboard"

# --- 認証まわりの定数 ---

#: セッション ID を入れる Cookie 名
SESSION_COOKIE_NAME = "lilla_session"
#: セッション Cookie の max-age（秒）。DB 側の expires_at と揃える
SESSION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
#: パスワードの最小文字数
MIN_PASSWORD_LENGTH = 12
#: パスワードの最大バイト数（bcrypt が 72 バイトを超える入力を受け付けないため）
MAX_PASSWORD_BYTES = 72
#: ログイン失敗のカウント対象とする時間窓（秒）
LOGIN_ATTEMPT_WINDOW_SECONDS = 60
#: 時間窓内に許容するログイン失敗回数。これを超えると一時ブロックする
LOGIN_MAX_FAILURES = 5

#: 認証を要求しないパス（完全一致）
_AUTH_EXEMPT_PATHS = frozenset({"/", "/api/auth/status", "/api/login"})
#: 認証を要求しないパスの接頭辞。``/oauth/`` は拡張が
#: ``dashboard_public_routes()`` で申告する公開コールバック（OAuth の戻り先など）で、
#: ブラウザがセッションを持たない状態で叩くためセッション Cookie を要求できない。
#: 前段（Cloudflare Zero Trust など）で ``/oauth/`` だけをバイパスする想定で、
#: ``state`` の検証は拡張側の責任。
_AUTH_EXEMPT_PREFIXES = ("/static/", "/oauth/")

#: ダッシュボード組み込みの 4 画面。拡張のページと同じ形で ``/api/dashboard/nav``
#: に並べ、フロントは組み込みと拡張を区別せずナビを組む。``name`` は
#: lilla-core が ``RESERVED_EXTENSION_NAMES`` で押さえているので拡張と衝突しない。
#: ``module`` が None のものは ``index.html`` に DOM を持つ組み込み画面。
_BUILTIN_PAGES: tuple[dict, ...] = (
    {"name": "home", "label": "Home", "group": "main", "hash": "#/", "module": None},
    {
        "name": "conversations",
        "label": "Conversations",
        "group": "main",
        "hash": "#/conversations",
        "module": None,
    },
    {"name": "memos", "label": "Memos", "group": "main", "hash": "#/memos", "module": None},
    {
        "name": "logs",
        "label": "Admin",
        "group": "admin",
        "hash": "#/admin/logs",
        "module": None,
    },
)

#: 失敗記録の一括掃除を始めるエントリ数（メモリの上限を設けるためのしきい値）
_LOGIN_FAILURES_MAX_ENTRIES = 1000

#: IP ごとのログイン失敗時刻（``time.monotonic()`` 秒）のリスト。プロセス内メモリ
#: のみで、再起動でリセットされる（Issue で許容されている仕様）。
_login_failures: dict[str, list[float]] = {}


def _get_collection(collection_name: str):
    """MongoDB コレクションを取得する。

    設定はモジュールの import 時ではなく呼び出しのたびに引く。コアの他の初期化より
    先に `load_extensions()` が設定を合成する契約のため、import 時に束縛すると
    合成前のインスタンスを掴みうる。
    """
    config = get_config()
    client = create_motor_client(config.env.mongodb_uri)
    return client[config.mongodb.db_name][collection_name]


def _parse_datetime(value: str | None) -> datetime | None:
    """ISO8601 文字列を UTC datetime に変換する。パース失敗時は None を返す。"""
    if not value:
        return None
    try:
        return parse_iso_utc(value)
    except (ValueError, TypeError):
        return None


def _serialize_doc(doc: dict) -> dict:
    """MongoDB ドキュメントの datetime・ObjectId フィールドをシリアライズ可能な型に変換する。"""
    result = {}
    for key, val in doc.items():
        if isinstance(val, datetime):
            result[key] = val.isoformat()
        elif isinstance(val, ObjectId):
            result[key] = str(val)
        elif isinstance(val, dict):
            result[key] = _serialize_doc(val)
        else:
            result[key] = val
    return result


async def handle_index(request: web.Request) -> web.FileResponse:
    """GET / - index.html を返す。"""
    return web.FileResponse(DASHBOARD_DIR / "index.html")


async def handle_api_logs(request: web.Request) -> web.Response:
    """GET /api/admin/logs - ログ一覧を返す。

    クエリパラメータ:
    - level: ログレベル（省略時は全件）
    - keyword: メッセージの部分一致
    - date_from: ISO8601 開始日時
    - date_to: ISO8601 終了日時
    - page: ページ番号（1始まり、デフォルト1）
    - page_size: 件数（デフォルト50、最大200）
    """
    params = request.rel_url.query
    level = params.get("level")
    keyword = params.get("keyword")
    date_from = _parse_datetime(params.get("date_from"))
    date_to = _parse_datetime(params.get("date_to"))
    page = parse_int_param(params, "page", 1, minimum=1)
    page_size = parse_int_param(params, "page_size", 50, minimum=1, maximum=200)

    query: dict = {}
    if level:
        query["levelname"] = level
    if keyword:
        query["message"] = {"$regex": keyword}
    date_filter: dict = {}
    if date_from:
        date_filter["$gte"] = date_from
    if date_to:
        date_filter["$lte"] = date_to
    if date_filter:
        query["created_at"] = date_filter

    collection = _get_collection("logs")
    total = await collection.count_documents(query)
    skip = (page - 1) * page_size
    cursor = collection.find(query, {"_id": 0}).sort("created_at", -1).skip(skip).limit(page_size)
    docs = await cursor.to_list(length=page_size)

    return web.json_response({
        "items": [_serialize_doc(doc) for doc in docs],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": math.ceil(total / page_size) if total > 0 else 1,
    })


async def handle_api_logs_levels(request: web.Request) -> web.Response:
    """GET /api/admin/logs/levels - ログレベル一覧を返す。"""
    collection = _get_collection("logs")
    levels = await collection.distinct("levelname")
    return web.json_response({"levels": sorted(levels)})


async def handle_api_logs_stats(request: web.Request) -> web.Response:
    """GET /api/admin/logs/stats - レベル別件数を返す。"""
    collection = _get_collection("logs")
    pipeline = [
        {"$group": {"_id": "$levelname", "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]
    cursor = collection.aggregate(pipeline)
    docs = await cursor.to_list(length=None)
    stats = {doc["_id"]: doc["count"] for doc in docs}
    return web.json_response({"stats": stats})


async def handle_api_conversations(request: web.Request) -> web.Response:
    """GET /api/conversations - 会話履歴一覧を返す。

    クエリパラメータ:
    - date_from: ISO8601 開始日時（デフォルト: 過去10日）
    - date_to: ISO8601 終了日時
    - page: ページ番号（1始まり、デフォルト1）
    - page_size: 件数（デフォルト50、最大200）
    """
    params = request.rel_url.query
    date_from = _parse_datetime(params.get("date_from"))
    date_to = _parse_datetime(params.get("date_to"))

    if date_from is None:
        date_from = utc_now() - timedelta(days=10)

    page = parse_int_param(params, "page", 1, minimum=1)
    page_size = parse_int_param(params, "page_size", 50, minimum=1, maximum=200)

    query: dict = {"time": {"$gte": date_from}}
    if date_to:
        query["time"]["$lte"] = date_to

    collection = _get_collection("conversations")
    total = await collection.count_documents(query)
    skip = (page - 1) * page_size
    cursor = collection.find(query).sort("time", -1).skip(skip).limit(page_size)
    docs = await cursor.to_list(length=page_size)

    return web.json_response({
        "items": [_serialize_doc(doc) for doc in docs],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": math.ceil(total / page_size) if total > 0 else 1,
    })


async def handle_api_conversations_delete(request: web.Request) -> web.Response:
    """DELETE /api/conversations/{id} - 指定 ID の会話を削除する。"""
    conv_id = request.match_info["id"]
    oid = parse_object_id(conv_id)
    if oid is None:
        return web.Response(status=400, text="Invalid ID format")

    collection = _get_collection("conversations")
    result = await collection.delete_one({"_id": oid})
    if result.deleted_count == 0:
        return web.Response(status=404, text="Conversation not found")
    return web.Response(status=204)


async def handle_api_user_memos(request: web.Request) -> web.Response:
    """GET /api/user-memos - 全メモを返す。POST /api/user-memos - メモを追加する。"""
    from lilla_core.repository.user_memo_repository import get_user_memo_repo

    repo = get_user_memo_repo()

    if request.method == "GET":
        memos = await repo.get_all()
        return web.json_response({"items": [_serialize_doc(m) for m in memos]})

    # POST
    body, ok = await parse_json_body(request)
    if not ok:
        return web.Response(status=400, text="Invalid JSON body")

    content = body.get("content", "").strip()
    if not content:
        return web.Response(status=400, text="content is required")

    doc = await repo.add(content)
    return web.json_response(_serialize_doc(doc), status=201)


async def handle_api_user_memo_detail(request: web.Request) -> web.Response:
    """PATCH /api/user-memos/{id} - メモを更新する。DELETE /api/user-memos/{id} - メモを削除する。"""
    from lilla_core.repository.user_memo_repository import get_user_memo_repo

    memo_id = request.match_info["id"]
    if parse_object_id(memo_id) is None:
        return web.Response(status=400, text="Invalid ID format")

    repo = get_user_memo_repo()

    if request.method == "DELETE":
        deleted = await repo.delete(memo_id)
        if not deleted:
            return web.Response(status=404, text="Memo not found")
        return web.Response(status=204)

    # PATCH
    body, ok = await parse_json_body(request)
    if not ok:
        return web.Response(status=400, text="Invalid JSON body")

    content = body.get("content")
    enabled = body.get("enabled")
    if content is not None:
        content = content.strip()
        if not content:
            return web.Response(status=400, text="content must not be empty")

    updated = await repo.update(memo_id, content, enabled)
    if not updated:
        return web.Response(status=404, text="Memo not found")
    return web.Response(status=204)


# ---------------------------------------------------------------------------
# 認証ヘルパー
# ---------------------------------------------------------------------------


def validate_password(password: object) -> str | None:
    """パスワードがポリシーを満たすか検証し、エラーメッセージを返す。

    ポリシーは「最小 ``MIN_PASSWORD_LENGTH`` 文字」のみで、文字種の強制は行わない。
    bcrypt は 72 バイトを超える入力で例外を送出するため、上限も併せて検証する。

    Args:
        password: 検証するパスワード（型不正も呼び出し側でチェックせずに渡してよい）。

    Returns:
        問題があればユーザー向けのエラーメッセージ。問題なければ None。
    """
    if not isinstance(password, str):
        return "password is required"
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"パスワードは{MIN_PASSWORD_LENGTH}文字以上で設定してください"
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        return f"パスワードは{MAX_PASSWORD_BYTES}バイト以内で設定してください"
    return None


def _hash_session_id(session_id: str) -> str:
    """セッション ID の SHA-256 ハッシュ（16 進文字列）を返す。

    生のセッション ID は Cookie にしか置かず、DB にはハッシュだけを保存する。

    Args:
        session_id: ``secrets.token_urlsafe`` で発行した生のセッション ID。

    Returns:
        SHA-256 ハッシュの 16 進文字列。
    """
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _client_ip(request: web.Request) -> str:
    """レート制限のキーに使うクライアント IP を返す。

    ``X-Forwarded-For`` は詐称可能でブロックの回避に使えてしまうため参照せず、
    TCP 接続元（``request.remote``）のみを使う。

    Args:
        request: aiohttp のリクエスト。

    Returns:
        クライアント IP。取得できない場合は ``"unknown"``。
    """
    return request.remote or "unknown"


def _is_login_blocked(ip: str) -> bool:
    """直近の失敗回数からログインを一時ブロックすべきかを返す。

    ``LOGIN_ATTEMPT_WINDOW_SECONDS`` 秒より古い失敗記録は捨てたうえで、残った
    件数が ``LOGIN_MAX_FAILURES`` 以上ならブロックする。時間窓から失敗が抜けると
    自動的にブロックが解除される。

    Args:
        ip: クライアント IP。

    Returns:
        ブロックすべきなら True。
    """
    now = time.monotonic()
    failures = [t for t in _login_failures.get(ip, []) if now - t < LOGIN_ATTEMPT_WINDOW_SECONDS]
    if failures:
        _login_failures[ip] = failures
    else:
        _login_failures.pop(ip, None)
    return len(failures) >= LOGIN_MAX_FAILURES


def _record_login_failure(ip: str) -> None:
    """ログイン失敗を記録する。

    IP を変えながら試行され続けると dict が際限なく育つため、エントリ数が
    ``_LOGIN_FAILURES_MAX_ENTRIES`` を超えたら時間窓を過ぎた記録をまとめて捨てる。

    Args:
        ip: クライアント IP。
    """
    now = time.monotonic()
    if len(_login_failures) > _LOGIN_FAILURES_MAX_ENTRIES:
        for key, times in list(_login_failures.items()):
            if all(now - t >= LOGIN_ATTEMPT_WINDOW_SECONDS for t in times):
                del _login_failures[key]
    _login_failures.setdefault(ip, []).append(now)


def _clear_login_failures(ip: str) -> None:
    """ログイン成功時に失敗記録を消す。

    Args:
        ip: クライアント IP。
    """
    _login_failures.pop(ip, None)


async def _hash_password(password: str) -> bytes:
    """パスワードの bcrypt ハッシュを生成する。

    bcrypt は意図的に低速（cost factor はデフォルトの 12）なので、イベント
    ループを止めないようスレッドプールで実行する。

    Args:
        password: 平文パスワード。

    Returns:
        bcrypt ハッシュ（bytes）。
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, lambda: bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    )


async def _check_password(password: str, password_hash: bytes) -> bool:
    """パスワードが bcrypt ハッシュと一致するかを返す。

    ``_hash_password`` と同じ理由でスレッドプールで実行する。

    Args:
        password: 平文パスワード。
        password_hash: 保存済みの bcrypt ハッシュ。

    Returns:
        一致すれば True。
    """
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        # bcrypt が ValueError を投げるため、検証前に不一致として扱う
        return False
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: bcrypt.checkpw(encoded, password_hash))


async def _is_setup_required() -> bool:
    """初期パスワードが未設定かを返す。

    Returns:
        未設定（``/setup`` を通すべき）なら True。
    """
    from lilla_core.repository.admin_credential_repository import get_admin_credential_repo

    return not await get_admin_credential_repo().exists()


async def _get_valid_session(request: web.Request) -> dict | None:
    """リクエストの Cookie から有効なセッションを取得する。

    Args:
        request: aiohttp のリクエスト。

    Returns:
        有効なセッションのドキュメント。Cookie 無し・期限切れなどの場合は None。
    """
    from lilla_core.repository.admin_session_repository import get_admin_session_repo

    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        return None
    return await get_admin_session_repo().find_valid(_hash_session_id(session_id))


def _is_auth_exempt(path: str) -> bool:
    """認証を要求しないパスかを返す。

    ``/`` と ``/static/*`` は SPA の箱と静的ファイルを返すだけ、``/api/auth/status`` は
    画面分岐用、``/api/login`` はログイン自体のため、いずれも認証対象外とする。
    ``/oauth/*`` は拡張の公開コールバックで、ブラウザがセッションを持たない状態で
    叩くため同じく対象外にする（申告されていないパスはルーターが 404 を返す）。

    Args:
        path: リクエストパス。

    Returns:
        認証不要なら True。
    """
    if path in _AUTH_EXEMPT_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in _AUTH_EXEMPT_PREFIXES)


@web.middleware
async def auth_middleware(request: web.Request, handler):
    """セッション Cookie による認証を強制するミドルウェア。

    振り分けは以下の通り（既定は「拒否」で、明示的に許可したものだけ通す）。

    - ``/`` ``/static/*`` ``/oauth/*`` ``/api/auth/status`` ``/api/login``: 認証対象外
    - ``/api/setup``: パスワード登録済みなら 403、未登録なら通す
    - それ以外: セッション Cookie 必須。未認証は 401

    Args:
        request: aiohttp のリクエスト。
        handler: 後続のハンドラー。

    Returns:
        後続ハンドラーのレスポンス、または 401 / 403 のレスポンス。
    """
    path = request.path

    if _is_auth_exempt(path):
        return await handler(request)

    if path == "/api/setup":
        if not await _is_setup_required():
            return web.Response(status=403, text="Setup already completed")
        return await handler(request)

    if await _get_valid_session(request) is None:
        return web.Response(status=401, text="Unauthorized")

    return await handler(request)


# ---------------------------------------------------------------------------
# 認証エンドポイント
# ---------------------------------------------------------------------------


async def handle_api_auth_status(request: web.Request) -> web.Response:
    """GET /api/auth/status - 初期設定の要否とログイン状態を返す。

    フロントの画面分岐に使う。``setup_required`` が True のときは、古いセッション
    Cookie が残っていても ``/setup`` を優先させるため ``authenticated`` は常に
    False を返す。
    """
    setup_required = await _is_setup_required()
    if setup_required:
        return web.json_response({"setup_required": True, "authenticated": False})

    authenticated = await _get_valid_session(request) is not None
    return web.json_response({"setup_required": False, "authenticated": authenticated})


async def handle_api_setup(request: web.Request) -> web.Response:
    """POST /api/setup - 初期パスワードを登録する。

    登録済みかどうかの判定は ``auth_middleware`` 側で行っており、ここへ到達する
    のは未登録のときだけ。競合で二重に登録されないよう、リポジトリ側でも
    ``$setOnInsert`` により後勝ちの上書きを防いでいる。
    """
    from lilla_core.repository.admin_credential_repository import get_admin_credential_repo

    body, ok = await parse_json_body(request)
    if not ok or not isinstance(body, dict):
        return web.Response(status=400, text="Invalid JSON body")

    password = body.get("password")
    error = validate_password(password)
    if error:
        return web.Response(status=400, text=error)

    password_hash = await _hash_password(password)
    saved = await get_admin_credential_repo().save_password_hash(password_hash)
    if not saved:
        return web.Response(status=403, text="Setup already completed")

    logger.info("Registered dashboard admin password")
    return web.json_response({"ok": True}, status=201)


async def handle_api_login(request: web.Request) -> web.Response:
    """POST /api/login - パスワードを検証してセッション Cookie を発行する。

    IP ごとに直近 ``LOGIN_ATTEMPT_WINDOW_SECONDS`` 秒で ``LOGIN_MAX_FAILURES``
    回失敗すると 429 で一時ブロックする（カウンタはプロセス内メモリのみ）。
    """
    from lilla_core.repository.admin_credential_repository import get_admin_credential_repo
    from lilla_core.repository.admin_session_repository import get_admin_session_repo

    ip = _client_ip(request)
    if _is_login_blocked(ip):
        logger.warning("Temporarily blocked due to too many login attempts: ip=%s", ip)
        return web.Response(status=429, text="Too many failed attempts. Try again later.")

    body, ok = await parse_json_body(request)
    if not ok or not isinstance(body, dict):
        return web.Response(status=400, text="Invalid JSON body")

    password = body.get("password")
    password_hash = await get_admin_credential_repo().get_password_hash()
    if password_hash is None:
        return web.Response(status=403, text="Setup required")

    if not isinstance(password, str) or not await _check_password(password, password_hash):
        _record_login_failure(ip)
        logger.warning("Dashboard login failed: ip=%s", ip)
        return web.Response(status=401, text="パスワードが違います")

    _clear_login_failures(ip)

    session_id = secrets.token_urlsafe(32)
    repo = get_admin_session_repo()
    await repo.create(_hash_session_id(session_id), repo.default_expires_at())

    response = web.json_response({"ok": True})
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_id,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="Strict",
        secure=bool(get_config().dashboard.cookie_secure),
        path="/",
    )
    logger.info("Logged in to dashboard: ip=%s", ip)
    return response


async def handle_api_logout(request: web.Request) -> web.Response:
    """POST /api/logout - セッションを削除して Cookie を失効させる。"""
    from lilla_core.repository.admin_session_repository import get_admin_session_repo

    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id:
        await get_admin_session_repo().delete(_hash_session_id(session_id))

    response = web.json_response({"ok": True})
    # ブラウザによっては発行時と属性が揃っていないと Cookie を消してくれない
    # （特に Secure 付き）ため、set_cookie と同じ属性を明示する。
    response.del_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="Strict",
        secure=bool(get_config().dashboard.cookie_secure),
    )
    return response


async def handle_api_dashboard_nav(request: web.Request) -> web.Response:
    """GET /api/dashboard/nav - ナビに並べる画面のカタログを返す。

    組み込みの 4 画面に、拡張が ``Extension.dashboard_page()`` で申告したページを
    ロード順で足して返す。経路（ハッシュ・API 接頭辞・JS モジュール URL）は
    lilla-core が ``Extension.name`` から導出したものをそのまま載せ、ここでも
    フロントでも組み立て直さない。

    Args:
        request: aiohttp のリクエスト（未使用）。

    Returns:
        ``{"pages": [...]}`` の JSON レスポンス。
    """
    pages: list[dict] = [dict(page) for page in _BUILTIN_PAGES]
    for entry in get_dashboard_pages():
        pages.append(
            {
                "name": entry.name,
                "label": entry.label,
                "group": entry.group,
                "hash": entry.hash,
                "module": entry.module_url,
                "api_prefix": entry.api_prefix,
            }
        )
    return web.json_response({"pages": pages})


def _add_extension_routes(app: web.Application) -> None:
    """拡張が申告したダッシュボードのルートと静的ファイルを載せる。

    静的ファイルは ``/static`` の一括配信より **先** に登録する。aiohttp のルーターは
    登録順に最初にマッチしたリソースを使い、``/static`` の ``StaticResource`` は
    ``/static/ext/...`` にもマッチしてしまうため、後から足すと拡張のファイルへ
    到達できない。

    パスの検査（``/api/{name}`` / ``/oauth/{name}`` 配下であること）は lilla-core が
    拡張のロード時に済ませているので、ここでは載せるだけにする。

    Args:
        app: ルートを登録する aiohttp アプリケーション。
    """
    for mount in get_dashboard_static_mounts():
        if not mount.directory.is_dir():
            logger.warning(
                "Skipped dashboard static dir of extension '%s' (not found): %s",
                mount.name,
                mount.directory,
            )
            continue
        app.router.add_static(mount.url_prefix.rstrip("/"), mount.directory)

    # セッション Cookie の内側（auth_middleware が既定で認証を要求する）
    for route in get_dashboard_routes():
        app.router.add_route(route.method, route.path, route.handler)

    # セッション Cookie の外側（`_AUTH_EXEMPT_PREFIXES` の `/oauth/` で素通しする）
    for route in get_dashboard_public_routes():
        app.router.add_route(route.method, route.path, route.handler)


async def start_dashboard_server() -> None:
    """ダッシュボードサーバーを起動する。

    認証ミドルウェア・認証 API・ログ・会話履歴 API と静的ファイル配信に加えて、
    拡張が申告したページのカタログ（``/api/dashboard/nav``）とルートを設定して、
    ``dashboard.host`` / ``dashboard.port`` で listen します。

    拡張の申告（``get_dashboard_*()``）を読むため、``load_extensions()`` が
    終わったあとに呼ぶこと。
    """

    # aiohttp のアクセスログを抑制
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

    app = web.Application(middlewares=[auth_middleware])
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/auth/status", handle_api_auth_status)
    app.router.add_post("/api/setup", handle_api_setup)
    app.router.add_post("/api/login", handle_api_login)
    app.router.add_post("/api/logout", handle_api_logout)
    app.router.add_get("/api/admin/logs", handle_api_logs)
    app.router.add_get("/api/admin/logs/levels", handle_api_logs_levels)
    app.router.add_get("/api/admin/logs/stats", handle_api_logs_stats)
    app.router.add_get("/api/conversations", handle_api_conversations)
    app.router.add_delete("/api/conversations/{id}", handle_api_conversations_delete)
    app.router.add_get("/api/user-memos", handle_api_user_memos)
    app.router.add_post("/api/user-memos", handle_api_user_memos)
    app.router.add_patch("/api/user-memos/{id}", handle_api_user_memo_detail)
    app.router.add_delete("/api/user-memos/{id}", handle_api_user_memo_detail)
    app.router.add_get("/api/dashboard/nav", handle_api_dashboard_nav)
    _add_extension_routes(app)
    app.router.add_static("/static", DASHBOARD_DIR)

    runner = web.AppRunner(app)
    await runner.setup()
    dashboard = get_config().dashboard
    site = web.TCPSite(runner, dashboard.host, dashboard.port)
    await site.start()
    logger.info(
        "Dashboard server started on %s:%d", dashboard.host, dashboard.port
    )
