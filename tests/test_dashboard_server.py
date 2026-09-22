"""`lilla_core.handlers.dashboard_server` のテスト。

観測用ダッシュボードの API ハンドラー・認証ミドルウェア・ルート登録のテスト。
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

@pytest.fixture
def mock_cfg() -> MagicMock:
    """dashboard_server が参照する AppConfig モック。"""
    cfg = MagicMock()
    cfg.env.mongodb_uri = "mongodb://localhost:27017"
    cfg.mongodb.db_name = "test_db"
    cfg.dashboard.host = "0.0.0.0"
    cfg.dashboard.port = 8765
    cfg.dashboard.cookie_secure = True
    return cfg


@pytest.fixture
def mock_web() -> MagicMock:
    """aiohttp.web モック。"""
    return MagicMock()


@pytest.fixture
def with_mocked_modules(mock_cfg: MagicMock):
    """依存モジュールを patch.dict で差し替える。"""
    mock_motor = MagicMock()
    with patch.dict(
        sys.modules,
        {
            "lilla_core.core.config": MagicMock(get_config=lambda: mock_cfg),
            "motor": MagicMock(),
            "motor.motor_asyncio": mock_motor,
        },
    ):
        yield


@pytest.fixture
def dashboard_server(with_mocked_modules, mock_web: MagicMock):
    """patch.dict 有効後に dashboard_server をロードする。"""
    sys.modules.pop("lilla_core.handlers.dashboard_server", None)
    import lilla_core.handlers.dashboard_server as loaded
    loaded.web = mock_web
    yield loaded
    sys.modules.pop("lilla_core.handlers.dashboard_server", None)


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


def _make_request(query_params: dict = None) -> MagicMock:
    """テスト用リクエストモックを生成する。"""
    req = MagicMock()
    req.rel_url.query = query_params or {}
    return req


def _make_collection(
    docs: list = None,
    total: int = 0,
    levels: list = None,
    pipeline_result: list = None,
) -> MagicMock:
    """MongoDB コレクションのモックを生成する。"""
    col = MagicMock()
    col.count_documents = AsyncMock(return_value=total)

    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.skip.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(return_value=docs or [])
    col.find.return_value = cursor

    col.distinct = AsyncMock(return_value=levels or [])

    agg_cursor = MagicMock()
    agg_cursor.to_list = AsyncMock(return_value=pipeline_result or [])
    col.aggregate.return_value = agg_cursor

    return col


# ---------------------------------------------------------------------------
# TestParseDateTime
# ---------------------------------------------------------------------------


class TestParseDateTime:
    def test_returns_none_for_none(self, dashboard_server, mock_web) -> None:
        """None を渡すと None を返す。"""
        assert dashboard_server._parse_datetime(None) is None

    def test_returns_none_for_empty_string(self, dashboard_server, mock_web) -> None:
        """空文字列を渡すと None を返す。"""
        assert dashboard_server._parse_datetime("") is None

    def test_parses_iso_datetime_with_tz(self, dashboard_server, mock_web) -> None:
        """タイムゾーン付き ISO8601 文字列を UTC datetime に変換する。"""
        result = dashboard_server._parse_datetime("2024-01-15T10:30:00+00:00")
        assert result is not None
        assert result.tzinfo == timezone.utc
        assert result.year == 2024

    def test_parses_iso_datetime_without_tz(self, dashboard_server, mock_web) -> None:
        """タイムゾーンなし ISO8601 文字列を UTC datetime に変換する。"""
        result = dashboard_server._parse_datetime("2024-01-15T10:30:00")
        assert result is not None

    def test_returns_none_for_invalid_string(self, dashboard_server, mock_web) -> None:
        """不正な文字列を渡すと None を返す。"""
        assert dashboard_server._parse_datetime("not-a-date") is None


# ---------------------------------------------------------------------------
# TestSerializeDoc
# ---------------------------------------------------------------------------


class TestSerializeDoc:
    def test_converts_datetime_to_isoformat(self, dashboard_server, mock_web) -> None:
        """datetime フィールドを ISO8601 文字列に変換する。"""
        dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        doc = {"message": "test", "created_at": dt}
        result = dashboard_server._serialize_doc(doc)
        assert result["created_at"] == dt.isoformat()
        assert result["message"] == "test"

    def test_converts_nested_datetime(self, dashboard_server, mock_web) -> None:
        """ネストされた dict 内の datetime も変換する。"""
        dt = datetime(2024, 1, 15, tzinfo=timezone.utc)
        doc = {"outer": {"inner_dt": dt}}
        result = dashboard_server._serialize_doc(doc)
        assert result["outer"]["inner_dt"] == dt.isoformat()

    def test_preserves_non_datetime_fields(self, dashboard_server, mock_web) -> None:
        """datetime 以外のフィールドはそのまま残す。"""
        doc = {"level": "INFO", "count": 42}
        result = dashboard_server._serialize_doc(doc)
        assert result == doc


# ---------------------------------------------------------------------------
# TestHandleIndex
# ---------------------------------------------------------------------------


class TestHandleIndex:
    async def test_returns_file_response(self, dashboard_server, mock_web) -> None:
        """/ エンドポイントが FileResponse を返す。"""
        mock_web.FileResponse.reset_mock()
        req = _make_request()

        await dashboard_server.handle_index(req)

        mock_web.FileResponse.assert_called_once()
        called_path = mock_web.FileResponse.call_args[0][0]
        assert called_path.name == "index.html"


# ---------------------------------------------------------------------------
# TestHandleApiLogs
# ---------------------------------------------------------------------------


class TestHandleApiLogs:
    async def test_calls_json_response(self, dashboard_server, mock_web, monkeypatch: pytest.MonkeyPatch) -> None:
        """ログ一覧 API が json_response を返す。"""
        col = _make_collection(docs=[], total=0)
        monkeypatch.setattr(dashboard_server, "_get_collection", lambda name: col)
        mock_web.json_response.reset_mock()

        await dashboard_server.handle_api_logs(_make_request())

        mock_web.json_response.assert_called_once()
        data = mock_web.json_response.call_args[0][0]
        assert "items" in data
        assert "total" in data
        assert data["total"] == 0

    async def test_filters_by_level(self, dashboard_server, mock_web, monkeypatch: pytest.MonkeyPatch) -> None:
        """level クエリパラメータで絞り込みクエリが組み立てられる。"""
        col = _make_collection(docs=[], total=0)
        monkeypatch.setattr(dashboard_server, "_get_collection", lambda name: col)

        await dashboard_server.handle_api_logs(_make_request({"level": "ERROR"}))

        query = col.find.call_args[0][0]
        assert query.get("levelname") == "ERROR"

    async def test_filters_by_keyword(self, dashboard_server, mock_web, monkeypatch: pytest.MonkeyPatch) -> None:
        """keyword クエリパラメータで正規表現フィルタが組み立てられる。"""
        col = _make_collection(docs=[], total=0)
        monkeypatch.setattr(dashboard_server, "_get_collection", lambda name: col)

        await dashboard_server.handle_api_logs(_make_request({"keyword": "error occurred"}))

        query = col.find.call_args[0][0]
        assert "$regex" in query.get("message", {})

    async def test_pagination_defaults(self, dashboard_server, mock_web, monkeypatch: pytest.MonkeyPatch) -> None:
        """ページネーションのデフォルト値が正しい。"""
        col = _make_collection(docs=[], total=100)
        monkeypatch.setattr(dashboard_server, "_get_collection", lambda name: col)
        mock_web.json_response.reset_mock()

        await dashboard_server.handle_api_logs(_make_request())

        data = mock_web.json_response.call_args[0][0]
        assert data["page"] == 1
        assert data["page_size"] == 50
        assert data["total_pages"] == 2

    async def test_serializes_datetime_in_docs(self, dashboard_server, mock_web, monkeypatch: pytest.MonkeyPatch) -> None:
        """ドキュメント内の datetime が ISO8601 文字列に変換される。"""
        dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        doc = {"levelname": "INFO", "message": "test", "created_at": dt}
        col = _make_collection(docs=[doc], total=1)
        monkeypatch.setattr(dashboard_server, "_get_collection", lambda name: col)
        mock_web.json_response.reset_mock()

        await dashboard_server.handle_api_logs(_make_request())

        data = mock_web.json_response.call_args[0][0]
        assert data["items"][0]["created_at"] == dt.isoformat()

    async def test_date_filter_applied(self, dashboard_server, mock_web, monkeypatch: pytest.MonkeyPatch) -> None:
        """date_from / date_to で created_at フィルタが組み立てられる。"""
        col = _make_collection(docs=[], total=0)
        monkeypatch.setattr(dashboard_server, "_get_collection", lambda name: col)

        await dashboard_server.handle_api_logs(
            _make_request({"date_from": "2024-01-01T00:00:00+00:00"})
        )

        query = col.find.call_args[0][0]
        assert "created_at" in query
        assert "$gte" in query["created_at"]

    async def test_invalid_page_defaults_to_1(self, dashboard_server, mock_web, monkeypatch: pytest.MonkeyPatch) -> None:
        """page に不正な値が来ても 1 にフォールバックする。"""
        col = _make_collection(docs=[], total=0)
        monkeypatch.setattr(dashboard_server, "_get_collection", lambda name: col)
        mock_web.json_response.reset_mock()

        await dashboard_server.handle_api_logs(_make_request({"page": "abc"}))

        data = mock_web.json_response.call_args[0][0]
        assert data["page"] == 1


# ---------------------------------------------------------------------------
# TestHandleApiLogsLevels
# ---------------------------------------------------------------------------


class TestHandleApiLogsLevels:
    async def test_returns_sorted_levels(self, dashboard_server, mock_web, monkeypatch: pytest.MonkeyPatch) -> None:
        """ログレベル一覧がソートされて返される。"""
        col = _make_collection(levels=["WARNING", "ERROR", "INFO"])
        monkeypatch.setattr(dashboard_server, "_get_collection", lambda name: col)
        mock_web.json_response.reset_mock()

        await dashboard_server.handle_api_logs_levels(_make_request())

        data = mock_web.json_response.call_args[0][0]
        assert data["levels"] == ["ERROR", "INFO", "WARNING"]

    async def test_returns_empty_list_when_no_logs(self, dashboard_server, mock_web, monkeypatch: pytest.MonkeyPatch) -> None:
        """ログがない場合は空リストを返す。"""
        col = _make_collection(levels=[])
        monkeypatch.setattr(dashboard_server, "_get_collection", lambda name: col)
        mock_web.json_response.reset_mock()

        await dashboard_server.handle_api_logs_levels(_make_request())

        data = mock_web.json_response.call_args[0][0]
        assert data["levels"] == []


# ---------------------------------------------------------------------------
# TestHandleApiLogsStats
# ---------------------------------------------------------------------------


class TestHandleApiLogsStats:
    async def test_returns_stats_dict(self, dashboard_server, mock_web, monkeypatch: pytest.MonkeyPatch) -> None:
        """レベル別件数が辞書形式で返される。"""
        pipeline_result = [
            {"_id": "ERROR", "count": 5},
            {"_id": "INFO", "count": 100},
        ]
        col = _make_collection(pipeline_result=pipeline_result)
        monkeypatch.setattr(dashboard_server, "_get_collection", lambda name: col)
        mock_web.json_response.reset_mock()

        await dashboard_server.handle_api_logs_stats(_make_request())

        data = mock_web.json_response.call_args[0][0]
        assert data["stats"]["ERROR"] == 5
        assert data["stats"]["INFO"] == 100

    async def test_returns_empty_stats_when_no_logs(self, dashboard_server, mock_web, monkeypatch: pytest.MonkeyPatch) -> None:
        """ログがない場合は空の stats を返す。"""
        col = _make_collection(pipeline_result=[])
        monkeypatch.setattr(dashboard_server, "_get_collection", lambda name: col)
        mock_web.json_response.reset_mock()

        await dashboard_server.handle_api_logs_stats(_make_request())

        data = mock_web.json_response.call_args[0][0]
        assert data["stats"] == {}


# ---------------------------------------------------------------------------
# TestHandleApiConversations
# ---------------------------------------------------------------------------


class TestHandleApiConversations:
    async def test_uses_default_date_from(self, dashboard_server, mock_web, monkeypatch: pytest.MonkeyPatch) -> None:
        """date_from 未指定時は過去10日がデフォルトになる。"""
        col = _make_collection(docs=[], total=0)
        monkeypatch.setattr(dashboard_server, "_get_collection", lambda name: col)

        await dashboard_server.handle_api_conversations(_make_request())

        query = col.find.call_args[0][0]
        assert "time" in query
        assert "$gte" in query["time"]

    async def test_uses_custom_date_from(self, dashboard_server, mock_web, monkeypatch: pytest.MonkeyPatch) -> None:
        """date_from を指定するとそれが使われる。"""
        col = _make_collection(docs=[], total=0)
        monkeypatch.setattr(dashboard_server, "_get_collection", lambda name: col)

        await dashboard_server.handle_api_conversations(
            _make_request({"date_from": "2024-01-01T00:00:00+00:00"})
        )

        query = col.find.call_args[0][0]
        assert query["time"]["$gte"] == datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    async def test_date_to_added_when_provided(self, dashboard_server, mock_web, monkeypatch: pytest.MonkeyPatch) -> None:
        """date_to を指定すると $lte が追加される。"""
        col = _make_collection(docs=[], total=0)
        monkeypatch.setattr(dashboard_server, "_get_collection", lambda name: col)

        await dashboard_server.handle_api_conversations(
            _make_request({"date_to": "2024-01-31T23:59:59+00:00"})
        )

        query = col.find.call_args[0][0]
        assert "$lte" in query["time"]

    async def test_returns_pagination_info(self, dashboard_server, mock_web, monkeypatch: pytest.MonkeyPatch) -> None:
        """ページネーション情報が含まれる。"""
        col = _make_collection(docs=[], total=0)
        monkeypatch.setattr(dashboard_server, "_get_collection", lambda name: col)
        mock_web.json_response.reset_mock()

        await dashboard_server.handle_api_conversations(_make_request())

        data = mock_web.json_response.call_args[0][0]
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "page_size" in data
        assert "total_pages" in data

    async def test_serializes_datetime_in_docs(self, dashboard_server, mock_web, monkeypatch: pytest.MonkeyPatch) -> None:
        """ドキュメント内の datetime が ISO8601 文字列に変換される。"""
        dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        doc = {"message": {"role": "user", "content": "hello"}, "time": dt}
        col = _make_collection(docs=[doc], total=1)
        monkeypatch.setattr(dashboard_server, "_get_collection", lambda name: col)
        mock_web.json_response.reset_mock()

        await dashboard_server.handle_api_conversations(_make_request())

        data = mock_web.json_response.call_args[0][0]
        assert data["items"][0]["time"] == dt.isoformat()

    async def test_page_size_clamped(self, dashboard_server, mock_web, monkeypatch: pytest.MonkeyPatch) -> None:
        """page_size が最大値（200）に制限される。"""
        col = _make_collection(docs=[], total=0)
        monkeypatch.setattr(dashboard_server, "_get_collection", lambda name: col)
        mock_web.json_response.reset_mock()

        await dashboard_server.handle_api_conversations(_make_request({"page_size": "999"}))

        data = mock_web.json_response.call_args[0][0]
        assert data["page_size"] == 200

    async def test_includes_id_in_items(self, dashboard_server, mock_web, monkeypatch: pytest.MonkeyPatch) -> None:
        """_id フィールドが文字列として items に含まれる。"""
        from bson import ObjectId
        oid = ObjectId()
        doc = {"_id": oid, "message": {"role": "user", "content": "hello"}, "time": datetime(2024, 1, 1, tzinfo=timezone.utc)}
        col = _make_collection(docs=[doc], total=1)
        monkeypatch.setattr(dashboard_server, "_get_collection", lambda name: col)
        mock_web.json_response.reset_mock()

        await dashboard_server.handle_api_conversations(_make_request())

        data = mock_web.json_response.call_args[0][0]
        assert data["items"][0]["_id"] == str(oid)


# ---------------------------------------------------------------------------
# TestHandleApiConversationsDelete
# ---------------------------------------------------------------------------


def _make_delete_request(conv_id: str) -> MagicMock:
    """削除エンドポイント用リクエストモックを生成する。"""
    req = MagicMock()
    req.match_info = {"id": conv_id}
    return req


class TestHandleApiConversationsDelete:
    async def test_deletes_existing_conversation(self, dashboard_server, mock_web, monkeypatch: pytest.MonkeyPatch) -> None:
        """存在する会話を削除すると 204 を返す。"""
        from bson import ObjectId
        oid = ObjectId()
        delete_result = MagicMock()
        delete_result.deleted_count = 1
        col = _make_collection()
        col.delete_one = AsyncMock(return_value=delete_result)
        monkeypatch.setattr(dashboard_server, "_get_collection", lambda name: col)
        mock_web.Response.reset_mock()

        await dashboard_server.handle_api_conversations_delete(_make_delete_request(str(oid)))

        col.delete_one.assert_called_once_with({"_id": oid})
        mock_web.Response.assert_called_once_with(status=204)

    async def test_returns_404_when_not_found(self, dashboard_server, mock_web, monkeypatch: pytest.MonkeyPatch) -> None:
        """存在しない会話の削除は 404 を返す。"""
        from bson import ObjectId
        delete_result = MagicMock()
        delete_result.deleted_count = 0
        col = _make_collection()
        col.delete_one = AsyncMock(return_value=delete_result)
        monkeypatch.setattr(dashboard_server, "_get_collection", lambda name: col)
        mock_web.Response.reset_mock()

        await dashboard_server.handle_api_conversations_delete(_make_delete_request(str(ObjectId())))

        call_kwargs = mock_web.Response.call_args[1]
        assert call_kwargs["status"] == 404

    async def test_returns_400_for_invalid_id(self, dashboard_server, mock_web, monkeypatch: pytest.MonkeyPatch) -> None:
        """不正な ID 形式は 400 を返す。"""
        col = _make_collection()
        monkeypatch.setattr(dashboard_server, "_get_collection", lambda name: col)
        mock_web.Response.reset_mock()

        await dashboard_server.handle_api_conversations_delete(_make_delete_request("invalid-id"))

        call_kwargs = mock_web.Response.call_args[1]
        assert call_kwargs["status"] == 400


# ---------------------------------------------------------------------------
# TestHandleApiUserMemos
# ---------------------------------------------------------------------------


def _make_mock_user_memo_repo(memos=None):
    """UserMemoRepository のモックを生成する。"""
    repo = MagicMock()
    repo.get_all = AsyncMock(return_value=memos or [])
    repo.add = AsyncMock()
    repo.update = AsyncMock(return_value=True)
    repo.delete = AsyncMock(return_value=True)
    return repo


def _patch_user_memo_repo(monkeypatch: pytest.MonkeyPatch, repo: MagicMock) -> None:
    """sys.modules の lilla_core.repository.user_memo_repository を差し替える。"""
    mock_module = MagicMock()
    mock_module.get_user_memo_repo = MagicMock(return_value=repo)
    monkeypatch.setitem(sys.modules, "lilla_core.repository.user_memo_repository", mock_module)


class TestHandleApiUserMemos:
    def _make_get_request(self) -> MagicMock:
        req = MagicMock()
        req.method = "GET"
        req.rel_url.query = {}
        return req

    def _make_post_request(self, body: dict) -> MagicMock:
        req = MagicMock()
        req.method = "POST"
        req.json = AsyncMock(return_value=body)
        return req

    async def test_get_returns_all_memos(self, dashboard_server, mock_web, monkeypatch: pytest.MonkeyPatch) -> None:
        """GET /api/user-memos が全件を返す。"""
        from datetime import datetime, timezone
        dt = datetime(2026, 4, 2, 10, 0, 0, tzinfo=timezone.utc)
        memos = [{"content": "memo1", "enabled": True, "created_at": dt}]
        repo = _make_mock_user_memo_repo(memos=memos)
        _patch_user_memo_repo(monkeypatch, repo)
        mock_web.json_response.reset_mock()

        await dashboard_server.handle_api_user_memos(self._make_get_request())

        mock_web.json_response.assert_called_once()
        data = mock_web.json_response.call_args[0][0]
        assert "items" in data
        assert len(data["items"]) == 1

    async def test_post_adds_memo(self, dashboard_server, mock_web, monkeypatch: pytest.MonkeyPatch) -> None:
        """POST /api/user-memos が新しいメモを追加する。"""
        from datetime import datetime, timezone
        dt = datetime(2026, 4, 2, 10, 0, 0, tzinfo=timezone.utc)
        new_memo = {"content": "新メモ", "enabled": True, "created_at": dt}
        repo = _make_mock_user_memo_repo()
        repo.add = AsyncMock(return_value=new_memo)
        _patch_user_memo_repo(monkeypatch, repo)
        mock_web.json_response.reset_mock()

        await dashboard_server.handle_api_user_memos(self._make_post_request({"content": "新メモ"}))

        repo.add.assert_called_once_with("新メモ")
        mock_web.json_response.assert_called_once()

    async def test_post_returns_400_for_empty_content(
        self, dashboard_server, mock_web, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """POST で content が空の場合 400 を返す。"""
        repo = _make_mock_user_memo_repo()
        _patch_user_memo_repo(monkeypatch, repo)
        mock_web.Response.reset_mock()

        await dashboard_server.handle_api_user_memos(self._make_post_request({"content": ""}))

        mock_web.Response.assert_called_once()
        assert mock_web.Response.call_args[1].get("status") == 400


class TestHandleApiUserMemoDetail:
    def _make_patch_request(self, memo_id: str, body: dict) -> MagicMock:
        req = MagicMock()
        req.method = "PATCH"
        req.match_info = {"id": memo_id}
        req.json = AsyncMock(return_value=body)
        return req

    def _make_delete_request(self, memo_id: str) -> MagicMock:
        req = MagicMock()
        req.method = "DELETE"
        req.match_info = {"id": memo_id}
        return req

    async def test_patch_updates_memo(self, dashboard_server, mock_web, monkeypatch: pytest.MonkeyPatch) -> None:
        """PATCH /api/user-memos/{id} がメモを更新する。"""
        from bson import ObjectId
        oid = str(ObjectId())
        repo = _make_mock_user_memo_repo()
        _patch_user_memo_repo(monkeypatch, repo)
        mock_web.Response.reset_mock()

        await dashboard_server.handle_api_user_memo_detail(
            self._make_patch_request(oid, {"content": "更新内容"})
        )

        repo.update.assert_called_once()
        mock_web.Response.assert_called_once_with(status=204)

    async def test_patch_returns_404_when_not_found(
        self, dashboard_server, mock_web, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """対象メモが存在しない場合 404 を返す。"""
        from bson import ObjectId
        oid = str(ObjectId())
        repo = _make_mock_user_memo_repo()
        repo.update = AsyncMock(return_value=False)
        _patch_user_memo_repo(monkeypatch, repo)
        mock_web.Response.reset_mock()

        await dashboard_server.handle_api_user_memo_detail(
            self._make_patch_request(oid, {"content": "内容"})
        )

        call_kwargs = mock_web.Response.call_args[1]
        assert call_kwargs["status"] == 404

    async def test_delete_removes_memo(self, dashboard_server, mock_web, monkeypatch: pytest.MonkeyPatch) -> None:
        """DELETE /api/user-memos/{id} がメモを削除する。"""
        from bson import ObjectId
        oid = str(ObjectId())
        repo = _make_mock_user_memo_repo()
        _patch_user_memo_repo(monkeypatch, repo)
        mock_web.Response.reset_mock()

        await dashboard_server.handle_api_user_memo_detail(self._make_delete_request(oid))

        repo.delete.assert_called_once_with(oid)
        mock_web.Response.assert_called_once_with(status=204)

    async def test_delete_returns_404_when_not_found(
        self, dashboard_server, mock_web, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """存在しないメモの削除は 404 を返す。"""
        from bson import ObjectId
        oid = str(ObjectId())
        repo = _make_mock_user_memo_repo()
        repo.delete = AsyncMock(return_value=False)
        _patch_user_memo_repo(monkeypatch, repo)
        mock_web.Response.reset_mock()

        await dashboard_server.handle_api_user_memo_detail(self._make_delete_request(oid))

        call_kwargs = mock_web.Response.call_args[1]
        assert call_kwargs["status"] == 404

    async def test_returns_400_for_invalid_id(self, dashboard_server, mock_web, monkeypatch: pytest.MonkeyPatch) -> None:
        """不正な ID は 400 を返す。"""
        repo = _make_mock_user_memo_repo()
        _patch_user_memo_repo(monkeypatch, repo)
        req = self._make_patch_request("invalid-id", {"content": "内容"})
        mock_web.Response.reset_mock()

        await dashboard_server.handle_api_user_memo_detail(req)

        call_kwargs = mock_web.Response.call_args[1]
        assert call_kwargs["status"] == 400


# ---------------------------------------------------------------------------
# 認証まわり
# ---------------------------------------------------------------------------


def _make_admin_cred_repo(exists: bool = False, password_hash: bytes | None = None) -> MagicMock:
    """AdminCredentialRepository のモックを生成する。"""
    repo = MagicMock()
    repo.exists = AsyncMock(return_value=exists)
    repo.get_password_hash = AsyncMock(return_value=password_hash)
    repo.save_password_hash = AsyncMock(return_value=True)
    return repo


def _make_admin_session_repo(session: dict | None = None) -> MagicMock:
    """AdminSessionRepository のモックを生成する。"""
    repo = MagicMock()
    repo.find_valid = AsyncMock(return_value=session)
    repo.create = AsyncMock()
    repo.delete = AsyncMock(return_value=True)
    repo.default_expires_at = MagicMock(
        return_value=datetime(2026, 9, 24, tzinfo=timezone.utc)
    )
    return repo


def _patch_admin_repos(
    monkeypatch: pytest.MonkeyPatch,
    cred_repo: MagicMock,
    session_repo: MagicMock,
) -> None:
    """sys.modules の admin 系リポジトリモジュールを差し替える。"""
    cred_module = MagicMock()
    cred_module.get_admin_credential_repo = MagicMock(return_value=cred_repo)
    session_module = MagicMock()
    session_module.get_admin_session_repo = MagicMock(return_value=session_repo)
    monkeypatch.setitem(sys.modules, "lilla_core.repository.admin_credential_repository", cred_module)
    monkeypatch.setitem(sys.modules, "lilla_core.repository.admin_session_repository", session_module)


def _make_auth_request(
    path: str = "/api/admin/logs",
    cookies: dict | None = None,
    body: dict | None = None,
    remote: str = "192.168.50.10",
) -> MagicMock:
    """認証系ハンドラー・ミドルウェア用のリクエストモックを生成する。"""
    req = MagicMock()
    req.path = path
    req.cookies = cookies or {}
    req.remote = remote
    req.json = AsyncMock(return_value=body if body is not None else {})
    return req


def _hash_for(password: str) -> bytes:
    """テスト用に低コストの bcrypt ハッシュを生成する（実行時間短縮のため）。"""
    import bcrypt

    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=4))


# ---------------------------------------------------------------------------
# TestValidatePassword
# ---------------------------------------------------------------------------


class TestValidatePassword:
    def test_accepts_12_chars(self, dashboard_server, mock_web) -> None:
        """12 文字ちょうどのパスワードは受け付ける。"""
        assert dashboard_server.validate_password("a" * 12) is None

    def test_rejects_11_chars(self, dashboard_server, mock_web) -> None:
        """11 文字のパスワードはエラーメッセージを返す。"""
        error = dashboard_server.validate_password("a" * 11)
        assert error is not None
        assert "12" in error

    def test_rejects_non_string(self, dashboard_server, mock_web) -> None:
        """文字列以外はエラーメッセージを返す。"""
        assert dashboard_server.validate_password(None) is not None

    def test_rejects_over_72_bytes(self, dashboard_server, mock_web) -> None:
        """bcrypt の上限（72 バイト）を超えるパスワードはエラーメッセージを返す。"""
        assert dashboard_server.validate_password("a" * 73) is not None

    def test_accepts_symbols_and_multibyte(self, dashboard_server, mock_web) -> None:
        """記号・マルチバイト文字を含んでも 12 文字以上なら通す。"""
        assert dashboard_server.validate_password("パスワード!#$%_ab") is None


# ---------------------------------------------------------------------------
# TestLoginRateLimit
# ---------------------------------------------------------------------------


class TestLoginRateLimit:
    def test_not_blocked_initially(self, dashboard_server, mock_web) -> None:
        """失敗記録が無い IP はブロックされない。"""
        assert dashboard_server._is_login_blocked("1.2.3.4") is False

    def test_blocked_after_five_failures(self, dashboard_server, mock_web) -> None:
        """60 秒以内に 5 回失敗するとブロックされる。"""
        for _ in range(5):
            dashboard_server._record_login_failure("1.2.3.4")
        assert dashboard_server._is_login_blocked("1.2.3.4") is True

    def test_not_blocked_after_four_failures(self, dashboard_server, mock_web) -> None:
        """4 回の失敗ではまだブロックされない。"""
        for _ in range(4):
            dashboard_server._record_login_failure("1.2.3.4")
        assert dashboard_server._is_login_blocked("1.2.3.4") is False

    def test_old_failures_expire(self, dashboard_server, mock_web) -> None:
        """時間窓より古い失敗記録は無視される。"""
        import time

        old = time.monotonic() - dashboard_server.LOGIN_ATTEMPT_WINDOW_SECONDS - 1
        dashboard_server._login_failures["1.2.3.4"] = [old] * 5
        assert dashboard_server._is_login_blocked("1.2.3.4") is False

    def test_success_clears_failures(self, dashboard_server, mock_web) -> None:
        """ログイン成功で失敗記録がリセットされる。"""
        for _ in range(5):
            dashboard_server._record_login_failure("1.2.3.4")
        dashboard_server._clear_login_failures("1.2.3.4")
        assert dashboard_server._is_login_blocked("1.2.3.4") is False

    def test_blocks_per_ip(self, dashboard_server, mock_web) -> None:
        """ブロックは IP ごとに独立している。"""
        for _ in range(5):
            dashboard_server._record_login_failure("1.2.3.4")
        assert dashboard_server._is_login_blocked("5.6.7.8") is False

    def test_stale_entries_are_pruned(self, dashboard_server, mock_web) -> None:
        """エントリ数が上限を超えると時間窓を過ぎた記録がまとめて捨てられる。"""
        import time

        old = time.monotonic() - dashboard_server.LOGIN_ATTEMPT_WINDOW_SECONDS - 1
        for i in range(dashboard_server._LOGIN_FAILURES_MAX_ENTRIES + 1):
            dashboard_server._login_failures[f"10.0.0.{i}"] = [old]

        dashboard_server._record_login_failure("1.2.3.4")

        assert dashboard_server._login_failures == {"1.2.3.4": [pytest.approx(time.monotonic(), abs=5)]}


# ---------------------------------------------------------------------------
# TestAuthMiddleware
# ---------------------------------------------------------------------------


class TestAuthMiddleware:
    async def _call(self, dashboard_server, request) -> tuple[object, MagicMock]:
        """ミドルウェアを呼び、レスポンスと後続ハンドラーのモックを返す。"""
        handler = AsyncMock(return_value="handler-response")
        result = await dashboard_server.auth_middleware(request, handler)
        return result, handler

    async def test_index_is_exempt(self, dashboard_server, mock_web, monkeypatch) -> None:
        """/ は認証対象外で後続ハンドラーへ通す。"""
        _patch_admin_repos(monkeypatch, _make_admin_cred_repo(), _make_admin_session_repo())

        result, handler = await self._call(dashboard_server, _make_auth_request(path="/"))

        handler.assert_awaited_once()
        assert result == "handler-response"

    async def test_static_is_exempt(self, dashboard_server, mock_web, monkeypatch) -> None:
        """/static/* は認証対象外で後続ハンドラーへ通す。"""
        _patch_admin_repos(monkeypatch, _make_admin_cred_repo(), _make_admin_session_repo())

        _, handler = await self._call(dashboard_server, _make_auth_request(path="/static/app.js"))

        handler.assert_awaited_once()

    async def test_extension_static_is_exempt(
        self, dashboard_server, mock_web, monkeypatch
    ) -> None:
        """拡張の静的ファイル（/static/ext/*）も認証対象外。"""
        _patch_admin_repos(monkeypatch, _make_admin_cred_repo(), _make_admin_session_repo())

        _, handler = await self._call(
            dashboard_server, _make_auth_request(path="/static/ext/habits/page.js")
        )

        handler.assert_awaited_once()

    async def test_public_oauth_callback_is_exempt(
        self, dashboard_server, mock_web, monkeypatch
    ) -> None:
        """拡張の公開コールバック（/oauth/*）は Cookie 無しでも通す。

        ブラウザが外部サービスから戻ってくる GET なので、セッションを要求できない。
        """
        _patch_admin_repos(monkeypatch, _make_admin_cred_repo(exists=True), _make_admin_session_repo())

        _, handler = await self._call(
            dashboard_server, _make_auth_request(path="/oauth/google-oauth/callback")
        )

        handler.assert_awaited_once()

    async def test_extension_api_still_requires_cookie(
        self, dashboard_server, mock_web, monkeypatch
    ) -> None:
        """拡張のセッション API（/api/{name}）は Cookie 必須のまま。"""
        _patch_admin_repos(
            monkeypatch, _make_admin_cred_repo(exists=True), _make_admin_session_repo()
        )
        mock_web.Response.reset_mock()

        _, handler = await self._call(
            dashboard_server, _make_auth_request(path="/api/habits/items")
        )

        handler.assert_not_awaited()
        assert mock_web.Response.call_args[1]["status"] == 401

    async def test_auth_status_is_exempt(self, dashboard_server, mock_web, monkeypatch) -> None:
        """/api/auth/status は認証対象外で後続ハンドラーへ通す。"""
        _patch_admin_repos(monkeypatch, _make_admin_cred_repo(), _make_admin_session_repo())

        _, handler = await self._call(
            dashboard_server, _make_auth_request(path="/api/auth/status")
        )

        handler.assert_awaited_once()

    async def test_login_is_exempt(self, dashboard_server, mock_web, monkeypatch) -> None:
        """/api/login は常に後続ハンドラーへ通す。"""
        _patch_admin_repos(monkeypatch, _make_admin_cred_repo(), _make_admin_session_repo())

        _, handler = await self._call(dashboard_server, _make_auth_request(path="/api/login"))

        handler.assert_awaited_once()

    async def test_setup_allowed_when_not_configured(
        self, dashboard_server, mock_web, monkeypatch
    ) -> None:
        """パスワード未登録なら /api/setup を通す。"""
        _patch_admin_repos(
            monkeypatch, _make_admin_cred_repo(exists=False), _make_admin_session_repo()
        )

        _, handler = await self._call(dashboard_server, _make_auth_request(path="/api/setup"))

        handler.assert_awaited_once()

    async def test_setup_blocked_when_configured(
        self, dashboard_server, mock_web, monkeypatch
    ) -> None:
        """パスワード登録済みなら /api/setup は 403 でブロックする。"""
        _patch_admin_repos(
            monkeypatch, _make_admin_cred_repo(exists=True), _make_admin_session_repo()
        )
        mock_web.Response.reset_mock()

        _, handler = await self._call(dashboard_server, _make_auth_request(path="/api/setup"))

        handler.assert_not_awaited()
        assert mock_web.Response.call_args[1]["status"] == 403

    async def test_api_without_cookie_returns_401(
        self, dashboard_server, mock_web, monkeypatch
    ) -> None:
        """Cookie 未保持で /api/admin/logs を叩くと 401 を返す。"""
        _patch_admin_repos(
            monkeypatch, _make_admin_cred_repo(exists=True), _make_admin_session_repo()
        )
        mock_web.Response.reset_mock()

        _, handler = await self._call(dashboard_server, _make_auth_request(path="/api/admin/logs"))

        handler.assert_not_awaited()
        assert mock_web.Response.call_args[1]["status"] == 401

    async def test_api_with_expired_session_returns_401(
        self, dashboard_server, mock_web, monkeypatch
    ) -> None:
        """期限切れセッション（find_valid が None）は 401 を返す。"""
        session_repo = _make_admin_session_repo(session=None)
        _patch_admin_repos(monkeypatch, _make_admin_cred_repo(exists=True), session_repo)
        mock_web.Response.reset_mock()
        req = _make_auth_request(
            path="/api/admin/logs", cookies={dashboard_server.SESSION_COOKIE_NAME: "raw-id"}
        )

        _, handler = await self._call(dashboard_server, req)

        handler.assert_not_awaited()
        assert mock_web.Response.call_args[1]["status"] == 401

    async def test_api_with_valid_session_passes(
        self, dashboard_server, mock_web, monkeypatch
    ) -> None:
        """有効なセッション Cookie があれば後続ハンドラーへ通す。"""
        session_repo = _make_admin_session_repo(session={"_id": "hash"})
        _patch_admin_repos(monkeypatch, _make_admin_cred_repo(exists=True), session_repo)
        req = _make_auth_request(
            path="/api/admin/logs", cookies={dashboard_server.SESSION_COOKIE_NAME: "raw-id"}
        )

        result, handler = await self._call(dashboard_server, req)

        handler.assert_awaited_once()
        assert result == "handler-response"

    async def test_session_id_is_looked_up_by_hash(
        self, dashboard_server, mock_web, monkeypatch
    ) -> None:
        """DB の照会には生のセッション ID ではなく SHA-256 ハッシュを使う。"""
        session_repo = _make_admin_session_repo(session={"_id": "hash"})
        _patch_admin_repos(monkeypatch, _make_admin_cred_repo(exists=True), session_repo)
        req = _make_auth_request(
            path="/api/admin/logs", cookies={dashboard_server.SESSION_COOKIE_NAME: "raw-id"}
        )

        await self._call(dashboard_server, req)

        session_repo.find_valid.assert_awaited_once_with(
            dashboard_server._hash_session_id("raw-id")
        )

    async def test_unknown_path_requires_auth(
        self, dashboard_server, mock_web, monkeypatch
    ) -> None:
        """明示的に許可していないパスは既定で認証を要求する。"""
        _patch_admin_repos(
            monkeypatch, _make_admin_cred_repo(exists=True), _make_admin_session_repo()
        )
        mock_web.Response.reset_mock()

        _, handler = await self._call(dashboard_server, _make_auth_request(path="/api/logout"))

        handler.assert_not_awaited()
        assert mock_web.Response.call_args[1]["status"] == 401


# ---------------------------------------------------------------------------
# TestHandleApiAuthStatus
# ---------------------------------------------------------------------------


class TestHandleApiAuthStatus:
    async def test_setup_required_when_no_credentials(
        self, dashboard_server, mock_web, monkeypatch
    ) -> None:
        """パスワード未登録なら setup_required=True を返す。"""
        _patch_admin_repos(
            monkeypatch, _make_admin_cred_repo(exists=False), _make_admin_session_repo()
        )
        mock_web.json_response.reset_mock()

        await dashboard_server.handle_api_auth_status(_make_auth_request())

        data = mock_web.json_response.call_args[0][0]
        assert data == {"setup_required": True, "authenticated": False}

    async def test_setup_required_wins_over_valid_session(
        self, dashboard_server, mock_web, monkeypatch
    ) -> None:
        """未登録なら有効な Cookie があっても authenticated=False を返す。"""
        session_repo = _make_admin_session_repo(session={"_id": "hash"})
        _patch_admin_repos(monkeypatch, _make_admin_cred_repo(exists=False), session_repo)
        mock_web.json_response.reset_mock()
        req = _make_auth_request(cookies={dashboard_server.SESSION_COOKIE_NAME: "raw-id"})

        await dashboard_server.handle_api_auth_status(req)

        data = mock_web.json_response.call_args[0][0]
        assert data["setup_required"] is True
        assert data["authenticated"] is False
        session_repo.find_valid.assert_not_awaited()

    async def test_authenticated_false_without_cookie(
        self, dashboard_server, mock_web, monkeypatch
    ) -> None:
        """登録済みかつ Cookie 無しなら authenticated=False を返す。"""
        _patch_admin_repos(
            monkeypatch, _make_admin_cred_repo(exists=True), _make_admin_session_repo()
        )
        mock_web.json_response.reset_mock()

        await dashboard_server.handle_api_auth_status(_make_auth_request())

        data = mock_web.json_response.call_args[0][0]
        assert data == {"setup_required": False, "authenticated": False}

    async def test_authenticated_true_with_valid_session(
        self, dashboard_server, mock_web, monkeypatch
    ) -> None:
        """登録済みかつ有効な Cookie があれば authenticated=True を返す。"""
        session_repo = _make_admin_session_repo(session={"_id": "hash"})
        _patch_admin_repos(monkeypatch, _make_admin_cred_repo(exists=True), session_repo)
        mock_web.json_response.reset_mock()
        req = _make_auth_request(cookies={dashboard_server.SESSION_COOKIE_NAME: "raw-id"})

        await dashboard_server.handle_api_auth_status(req)

        data = mock_web.json_response.call_args[0][0]
        assert data == {"setup_required": False, "authenticated": True}


# ---------------------------------------------------------------------------
# TestHandleApiSetup
# ---------------------------------------------------------------------------


class TestHandleApiSetup:
    async def test_saves_bcrypt_hash(self, dashboard_server, mock_web, monkeypatch) -> None:
        """妥当なパスワードで bcrypt ハッシュが保存される。"""
        import bcrypt

        cred_repo = _make_admin_cred_repo(exists=False)
        _patch_admin_repos(monkeypatch, cred_repo, _make_admin_session_repo())
        mock_web.json_response.reset_mock()

        await dashboard_server.handle_api_setup(
            _make_auth_request(path="/api/setup", body={"password": "correct-horse-battery"})
        )

        cred_repo.save_password_hash.assert_awaited_once()
        saved_hash = cred_repo.save_password_hash.call_args[0][0]
        assert bcrypt.checkpw(b"correct-horse-battery", saved_hash)
        assert mock_web.json_response.call_args[1]["status"] == 201

    async def test_uses_default_cost_factor(self, dashboard_server, mock_web, monkeypatch) -> None:
        """bcrypt のデフォルト cost factor（12）でハッシュ化される。"""
        cred_repo = _make_admin_cred_repo(exists=False)
        _patch_admin_repos(monkeypatch, cred_repo, _make_admin_session_repo())

        await dashboard_server.handle_api_setup(
            _make_auth_request(path="/api/setup", body={"password": "correct-horse-battery"})
        )

        saved_hash = cred_repo.save_password_hash.call_args[0][0]
        assert saved_hash.startswith(b"$2b$12$")

    async def test_rejects_short_password(self, dashboard_server, mock_web, monkeypatch) -> None:
        """11 文字のパスワードは 400 で拒否する。"""
        cred_repo = _make_admin_cred_repo(exists=False)
        _patch_admin_repos(monkeypatch, cred_repo, _make_admin_session_repo())
        mock_web.Response.reset_mock()

        await dashboard_server.handle_api_setup(
            _make_auth_request(path="/api/setup", body={"password": "a" * 11})
        )

        assert mock_web.Response.call_args[1]["status"] == 400
        cred_repo.save_password_hash.assert_not_awaited()

    async def test_rejects_missing_password(self, dashboard_server, mock_web, monkeypatch) -> None:
        """password が無いボディは 400 で拒否する。"""
        cred_repo = _make_admin_cred_repo(exists=False)
        _patch_admin_repos(monkeypatch, cred_repo, _make_admin_session_repo())
        mock_web.Response.reset_mock()

        await dashboard_server.handle_api_setup(_make_auth_request(path="/api/setup", body={}))

        assert mock_web.Response.call_args[1]["status"] == 400

    async def test_rejects_invalid_json(self, dashboard_server, mock_web, monkeypatch) -> None:
        """JSON として解析できないボディは 400 で拒否する。"""
        cred_repo = _make_admin_cred_repo(exists=False)
        _patch_admin_repos(monkeypatch, cred_repo, _make_admin_session_repo())
        mock_web.Response.reset_mock()
        req = _make_auth_request(path="/api/setup")
        req.json = AsyncMock(side_effect=ValueError("broken"))

        await dashboard_server.handle_api_setup(req)

        assert mock_web.Response.call_args[1]["status"] == 400

    async def test_returns_403_when_already_saved(
        self, dashboard_server, mock_web, monkeypatch
    ) -> None:
        """競合で既に登録済みだった場合は 403 を返す。"""
        cred_repo = _make_admin_cred_repo(exists=False)
        cred_repo.save_password_hash = AsyncMock(return_value=False)
        _patch_admin_repos(monkeypatch, cred_repo, _make_admin_session_repo())
        mock_web.Response.reset_mock()

        await dashboard_server.handle_api_setup(
            _make_auth_request(path="/api/setup", body={"password": "correct-horse-battery"})
        )

        assert mock_web.Response.call_args[1]["status"] == 403


# ---------------------------------------------------------------------------
# TestHandleApiLogin
# ---------------------------------------------------------------------------


class TestHandleApiLogin:
    async def test_issues_session_cookie_on_success(
        self, dashboard_server, mock_web, monkeypatch
    ) -> None:
        """正しいパスワードでセッションが保存され Cookie が発行される。"""
        password = "correct-horse-battery"
        cred_repo = _make_admin_cred_repo(exists=True, password_hash=_hash_for(password))
        session_repo = _make_admin_session_repo()
        _patch_admin_repos(monkeypatch, cred_repo, session_repo)
        response = MagicMock()
        mock_web.json_response.return_value = response

        await dashboard_server.handle_api_login(
            _make_auth_request(path="/api/login", body={"password": password})
        )

        session_repo.create.assert_awaited_once()
        response.set_cookie.assert_called_once()
        cookie_kwargs = response.set_cookie.call_args[1]
        assert cookie_kwargs["httponly"] is True
        assert cookie_kwargs["samesite"] == "Strict"
        assert cookie_kwargs["max_age"] == dashboard_server.SESSION_MAX_AGE_SECONDS

    async def test_stores_hashed_session_id_only(
        self, dashboard_server, mock_web, monkeypatch
    ) -> None:
        """DB へ保存するのはハッシュで、Cookie の生 ID は保存しない。"""
        password = "correct-horse-battery"
        cred_repo = _make_admin_cred_repo(exists=True, password_hash=_hash_for(password))
        session_repo = _make_admin_session_repo()
        _patch_admin_repos(monkeypatch, cred_repo, session_repo)
        response = MagicMock()
        mock_web.json_response.return_value = response

        await dashboard_server.handle_api_login(
            _make_auth_request(path="/api/login", body={"password": password})
        )

        raw_session_id = response.set_cookie.call_args[0][1]
        stored_hash = session_repo.create.call_args[0][0]
        assert stored_hash == dashboard_server._hash_session_id(raw_session_id)
        assert stored_hash != raw_session_id

    async def test_cookie_secure_true_by_config(
        self, dashboard_server, mock_web, monkeypatch, mock_cfg
    ) -> None:
        """cookie_secure=true なら Secure 属性を付ける。"""
        mock_cfg.dashboard.cookie_secure = True
        password = "correct-horse-battery"
        cred_repo = _make_admin_cred_repo(exists=True, password_hash=_hash_for(password))
        _patch_admin_repos(monkeypatch, cred_repo, _make_admin_session_repo())
        response = MagicMock()
        mock_web.json_response.return_value = response

        await dashboard_server.handle_api_login(
            _make_auth_request(path="/api/login", body={"password": password})
        )

        assert response.set_cookie.call_args[1]["secure"] is True

    async def test_cookie_secure_false_by_config(
        self, dashboard_server, mock_web, monkeypatch, mock_cfg
    ) -> None:
        """cookie_secure=false なら Secure 属性を付けない（LAN 内 HTTP 運用）。"""
        mock_cfg.dashboard.cookie_secure = False
        password = "correct-horse-battery"
        cred_repo = _make_admin_cred_repo(exists=True, password_hash=_hash_for(password))
        _patch_admin_repos(monkeypatch, cred_repo, _make_admin_session_repo())
        response = MagicMock()
        mock_web.json_response.return_value = response

        await dashboard_server.handle_api_login(
            _make_auth_request(path="/api/login", body={"password": password})
        )

        assert response.set_cookie.call_args[1]["secure"] is False

    async def test_wrong_password_returns_401(
        self, dashboard_server, mock_web, monkeypatch
    ) -> None:
        """誤ったパスワードは 401 を返し、セッションを作らない。"""
        cred_repo = _make_admin_cred_repo(exists=True, password_hash=_hash_for("correct-password"))
        session_repo = _make_admin_session_repo()
        _patch_admin_repos(monkeypatch, cred_repo, session_repo)
        mock_web.Response.reset_mock()

        await dashboard_server.handle_api_login(
            _make_auth_request(path="/api/login", body={"password": "wrong-password"})
        )

        assert mock_web.Response.call_args[1]["status"] == 401
        session_repo.create.assert_not_awaited()

    async def test_blocks_after_five_failures(
        self, dashboard_server, mock_web, monkeypatch
    ) -> None:
        """同一 IP から 5 回連続で失敗すると 6 回目は 429 でブロックする。"""
        cred_repo = _make_admin_cred_repo(exists=True, password_hash=_hash_for("correct-password"))
        _patch_admin_repos(monkeypatch, cred_repo, _make_admin_session_repo())

        for _ in range(5):
            await dashboard_server.handle_api_login(
                _make_auth_request(path="/api/login", body={"password": "wrong-password"})
            )

        mock_web.Response.reset_mock()
        await dashboard_server.handle_api_login(
            _make_auth_request(path="/api/login", body={"password": "wrong-password"})
        )

        assert mock_web.Response.call_args[1]["status"] == 429

    async def test_blocked_ip_cannot_login_with_correct_password(
        self, dashboard_server, mock_web, monkeypatch
    ) -> None:
        """ブロック中は正しいパスワードでもログインできない。"""
        password = "correct-horse-battery"
        cred_repo = _make_admin_cred_repo(exists=True, password_hash=_hash_for(password))
        session_repo = _make_admin_session_repo()
        _patch_admin_repos(monkeypatch, cred_repo, session_repo)
        for _ in range(5):
            dashboard_server._record_login_failure("192.168.50.10")
        mock_web.Response.reset_mock()

        await dashboard_server.handle_api_login(
            _make_auth_request(path="/api/login", body={"password": password})
        )

        assert mock_web.Response.call_args[1]["status"] == 429
        session_repo.create.assert_not_awaited()

    async def test_successful_login_clears_failures(
        self, dashboard_server, mock_web, monkeypatch
    ) -> None:
        """ログイン成功で失敗カウンタがリセットされる。"""
        password = "correct-horse-battery"
        cred_repo = _make_admin_cred_repo(exists=True, password_hash=_hash_for(password))
        _patch_admin_repos(monkeypatch, cred_repo, _make_admin_session_repo())
        for _ in range(4):
            await dashboard_server.handle_api_login(
                _make_auth_request(path="/api/login", body={"password": "wrong-password"})
            )

        await dashboard_server.handle_api_login(
            _make_auth_request(path="/api/login", body={"password": password})
        )

        assert dashboard_server._is_login_blocked("192.168.50.10") is False

    async def test_returns_403_when_setup_required(
        self, dashboard_server, mock_web, monkeypatch
    ) -> None:
        """パスワード未登録の状態でのログイン試行は 403 を返す。"""
        cred_repo = _make_admin_cred_repo(exists=False, password_hash=None)
        _patch_admin_repos(monkeypatch, cred_repo, _make_admin_session_repo())
        mock_web.Response.reset_mock()

        await dashboard_server.handle_api_login(
            _make_auth_request(path="/api/login", body={"password": "correct-horse-battery"})
        )

        assert mock_web.Response.call_args[1]["status"] == 403

    async def test_over_length_password_is_rejected_without_error(
        self, dashboard_server, mock_web, monkeypatch
    ) -> None:
        """bcrypt の上限を超えるパスワードは例外にせず 401 で拒否する。"""
        cred_repo = _make_admin_cred_repo(exists=True, password_hash=_hash_for("correct-password"))
        _patch_admin_repos(monkeypatch, cred_repo, _make_admin_session_repo())
        mock_web.Response.reset_mock()

        await dashboard_server.handle_api_login(
            _make_auth_request(path="/api/login", body={"password": "a" * 200})
        )

        assert mock_web.Response.call_args[1]["status"] == 401


# ---------------------------------------------------------------------------
# TestHandleApiLogout
# ---------------------------------------------------------------------------


class TestHandleApiLogout:
    async def test_deletes_session_and_clears_cookie(
        self, dashboard_server, mock_web, monkeypatch
    ) -> None:
        """セッションを削除して Cookie を失効させる。"""
        session_repo = _make_admin_session_repo(session={"_id": "hash"})
        _patch_admin_repos(monkeypatch, _make_admin_cred_repo(exists=True), session_repo)
        response = MagicMock()
        mock_web.json_response.return_value = response
        req = _make_auth_request(
            path="/api/logout", cookies={dashboard_server.SESSION_COOKIE_NAME: "raw-id"}
        )

        await dashboard_server.handle_api_logout(req)

        session_repo.delete.assert_awaited_once_with(
            dashboard_server._hash_session_id("raw-id")
        )
        response.del_cookie.assert_called_once()

    async def test_del_cookie_matches_set_cookie_attributes(
        self, dashboard_server, mock_web, monkeypatch, mock_cfg
    ) -> None:
        """Cookie 失効時は発行時と同じ属性を明示する（消えないブラウザ対策）。"""
        mock_cfg.dashboard.cookie_secure = True
        _patch_admin_repos(
            monkeypatch, _make_admin_cred_repo(exists=True), _make_admin_session_repo()
        )
        response = MagicMock()
        mock_web.json_response.return_value = response
        req = _make_auth_request(
            path="/api/logout", cookies={dashboard_server.SESSION_COOKIE_NAME: "raw-id"}
        )

        await dashboard_server.handle_api_logout(req)

        kwargs = response.del_cookie.call_args[1]
        assert kwargs["path"] == "/"
        assert kwargs["httponly"] is True
        assert kwargs["samesite"] == "Strict"
        assert kwargs["secure"] is True

    async def test_without_cookie_does_not_touch_db(
        self, dashboard_server, mock_web, monkeypatch
    ) -> None:
        """Cookie が無い場合は DB を触らず Cookie 失効だけ行う。"""
        session_repo = _make_admin_session_repo()
        _patch_admin_repos(monkeypatch, _make_admin_cred_repo(exists=True), session_repo)
        response = MagicMock()
        mock_web.json_response.return_value = response

        await dashboard_server.handle_api_logout(_make_auth_request(path="/api/logout"))

        session_repo.delete.assert_not_awaited()
        response.del_cookie.assert_called_once()


# ---------------------------------------------------------------------------
# 拡張のダッシュボード申告
# ---------------------------------------------------------------------------


def _page_entry(name: str, label: str, group: str = "main"):
    """lilla-core の導出済みページエントリを組み立てる。"""
    from lilla_core.core.extension import DashboardPage, DashboardPageEntry

    return DashboardPageEntry.from_page(name, DashboardPage(label, group))


def _static_mount(name: str, directory):
    """lilla-core の静的配信エントリを組み立てる。"""
    from lilla_core.core.extension import DashboardStaticMount

    return DashboardStaticMount(
        name=name, url_prefix=f"/static/ext/{name}/", directory=directory
    )


def _route(method: str, path: str):
    """lilla-core のルート申告を組み立てる。"""
    from lilla_core.core.extension import DashboardRoute

    return DashboardRoute(method, path, AsyncMock())


def _patch_dashboard_contributions(
    monkeypatch,
    dashboard_server,
    pages=(),
    mounts=(),
    routes=(),
    public_routes=(),
) -> None:
    """拡張の申告を返すコアの参照関数を差し替える。"""
    monkeypatch.setattr(dashboard_server, "get_dashboard_pages", lambda: list(pages))
    monkeypatch.setattr(
        dashboard_server, "get_dashboard_static_mounts", lambda: list(mounts)
    )
    monkeypatch.setattr(dashboard_server, "get_dashboard_routes", lambda: list(routes))
    monkeypatch.setattr(
        dashboard_server, "get_dashboard_public_routes", lambda: list(public_routes)
    )


class TestHandleApiDashboardNav:
    """``GET /api/dashboard/nav`` が返すカタログ。"""

    def _payload(self, mock_web: MagicMock) -> dict:
        """`web.json_response` に渡されたボディを取り出す。"""
        return mock_web.json_response.call_args[0][0]

    async def test_returns_builtin_pages(
        self, dashboard_server, mock_web, monkeypatch
    ) -> None:
        """拡張が 0 個でも組み込み 4 画面を返す。"""
        _patch_dashboard_contributions(monkeypatch, dashboard_server)

        await dashboard_server.handle_api_dashboard_nav(_make_request())

        pages = self._payload(mock_web)["pages"]
        assert [p["name"] for p in pages] == ["home", "conversations", "memos", "logs"]
        assert [p["hash"] for p in pages] == [
            "#/",
            "#/conversations",
            "#/memos",
            "#/admin/logs",
        ]
        assert all(p["module"] is None for p in pages)

    async def test_builtin_groups_split_main_and_admin(
        self, dashboard_server, mock_web, monkeypatch
    ) -> None:
        """Logs だけが管理メニュー側に入る。"""
        _patch_dashboard_contributions(monkeypatch, dashboard_server)

        await dashboard_server.handle_api_dashboard_nav(_make_request())

        pages = {p["name"]: p["group"] for p in self._payload(mock_web)["pages"]}
        assert pages == {
            "home": "main",
            "conversations": "main",
            "memos": "main",
            "logs": "admin",
        }

    async def test_extension_pages_follow_builtins_in_load_order(
        self, dashboard_server, mock_web, monkeypatch
    ) -> None:
        """拡張のページは組み込みの後ろに、ロード順で並ぶ。"""
        _patch_dashboard_contributions(
            monkeypatch,
            dashboard_server,
            pages=[_page_entry("habits", "Habits"), _page_entry("cal", "Calendar")],
        )

        await dashboard_server.handle_api_dashboard_nav(_make_request())

        names = [p["name"] for p in self._payload(mock_web)["pages"]]
        assert names == ["home", "conversations", "memos", "logs", "habits", "cal"]

    async def test_extension_paths_come_from_the_core(
        self, dashboard_server, mock_web, monkeypatch
    ) -> None:
        """経路はコアが `name` から導出したものをそのまま載せる。"""
        _patch_dashboard_contributions(
            monkeypatch, dashboard_server, pages=[_page_entry("google-oauth", "Google")]
        )

        await dashboard_server.handle_api_dashboard_nav(_make_request())

        page = self._payload(mock_web)["pages"][-1]
        assert page == {
            "name": "google-oauth",
            "label": "Google",
            "group": "main",
            "hash": "#/google-oauth",
            "module": "/static/ext/google-oauth/page.js",
            "api_prefix": "/api/google-oauth",
        }

    async def test_admin_extension_page_uses_admin_hash(
        self, dashboard_server, mock_web, monkeypatch
    ) -> None:
        """管理グループの拡張ページは `#/admin/{name}` に載る。"""
        _patch_dashboard_contributions(
            monkeypatch,
            dashboard_server,
            pages=[_page_entry("habits", "Habits", "admin")],
        )

        await dashboard_server.handle_api_dashboard_nav(_make_request())

        page = self._payload(mock_web)["pages"][-1]
        assert page["group"] == "admin"
        assert page["hash"] == "#/admin/habits"

    async def test_builtin_catalog_is_not_mutated(
        self, dashboard_server, mock_web, monkeypatch
    ) -> None:
        """返した dict を書き換えてもモジュール定数は壊れない。"""
        _patch_dashboard_contributions(monkeypatch, dashboard_server)

        await dashboard_server.handle_api_dashboard_nav(_make_request())
        self._payload(mock_web)["pages"][0]["label"] = "Tampered"

        assert dashboard_server._BUILTIN_PAGES[0]["label"] == "Home"


class TestAddExtensionRoutes:
    """拡張が申告したルート・静的ファイルの載せ方。"""

    def test_static_mounts_are_registered(
        self, dashboard_server, monkeypatch, tmp_path
    ) -> None:
        """静的ディレクトリは末尾スラッシュを落とした接頭辞で載せる。"""
        _patch_dashboard_contributions(
            monkeypatch, dashboard_server, mounts=[_static_mount("habits", tmp_path)]
        )
        app = MagicMock()

        dashboard_server._add_extension_routes(app)

        app.router.add_static.assert_called_once_with("/static/ext/habits", tmp_path)

    def test_missing_static_dir_is_skipped(
        self, dashboard_server, monkeypatch, tmp_path, caplog
    ) -> None:
        """存在しないディレクトリは登録せず WARNING だけ出す（起動は止めない）。"""
        _patch_dashboard_contributions(
            monkeypatch,
            dashboard_server,
            mounts=[_static_mount("habits", tmp_path / "nope")],
        )
        app = MagicMock()

        with caplog.at_level("WARNING"):
            dashboard_server._add_extension_routes(app)

        app.router.add_static.assert_not_called()
        assert "habits" in caplog.text

    def test_session_and_public_routes_are_added(
        self, dashboard_server, monkeypatch
    ) -> None:
        """セッションルートも公開ルートも同じルーターへ載せる。"""
        api = _route("get", "/api/habits/items")
        public = _route("GET", "/oauth/habits/callback")
        _patch_dashboard_contributions(
            monkeypatch, dashboard_server, routes=[api], public_routes=[public]
        )
        app = MagicMock()

        dashboard_server._add_extension_routes(app)

        assert app.router.add_route.call_args_list == [
            (("GET", "/api/habits/items", api.handler),),
            (("GET", "/oauth/habits/callback", public.handler),),
        ]

    def test_static_is_registered_before_routes(
        self, dashboard_server, monkeypatch, tmp_path
    ) -> None:
        """静的配信を先に登録する（後述の `/static` 一括配信に食われないため）。"""
        _patch_dashboard_contributions(
            monkeypatch,
            dashboard_server,
            mounts=[_static_mount("habits", tmp_path)],
            routes=[_route("GET", "/api/habits")],
        )
        app = MagicMock()

        dashboard_server._add_extension_routes(app)

        called = [call[0] for call in app.router.mock_calls]
        assert called.index("add_static") < called.index("add_route")


class TestStartDashboardServer:
    """起動時のルート登録。"""

    @pytest.fixture
    def started_app(self, dashboard_server, mock_web):
        """`start_dashboard_server()` を走らせ、組み立てられた app を返すファクトリ。"""
        async def _start():
            mock_web.AppRunner.return_value.setup = AsyncMock()
            mock_web.TCPSite.return_value.start = AsyncMock()
            await dashboard_server.start_dashboard_server()
            return mock_web.Application.return_value

        return _start

    async def test_listens_on_the_configured_host_and_port(
        self, dashboard_server, mock_web, mock_cfg, monkeypatch, started_app
    ) -> None:
        """listen 先は `dashboard.host` / `dashboard.port` から取る。

        既定は全インターフェースだが、同一ホストからしか使わない運用では
        `lilla.yaml` で `127.0.0.1` に絞れる（管理画面を晒さないため）。
        """
        _patch_dashboard_contributions(monkeypatch, dashboard_server)
        mock_cfg.dashboard.host = "127.0.0.1"
        mock_cfg.dashboard.port = 9999

        await started_app()

        runner = mock_web.AppRunner.return_value
        assert mock_web.TCPSite.call_args.args == (runner, "127.0.0.1", 9999)

    async def test_nav_endpoint_is_registered(
        self, dashboard_server, monkeypatch, started_app
    ) -> None:
        """ナビのカタログはセッション認証の内側（除外リストの外）に載る。"""
        _patch_dashboard_contributions(monkeypatch, dashboard_server)

        app = await started_app()

        paths = [call.args[0] for call in app.router.add_get.call_args_list]
        assert "/api/dashboard/nav" in paths
        assert not dashboard_server._is_auth_exempt("/api/dashboard/nav")

    async def test_extension_static_is_registered_before_the_global_static(
        self, dashboard_server, monkeypatch, tmp_path, started_app
    ) -> None:
        """aiohttp は登録順に最初にマッチしたリソースを使うので、拡張分が先。

        `/static` の一括配信を先に登録すると `/static/ext/...` がそちらに
        吸われ、拡張のファイルへ到達できなくなる。
        """
        _patch_dashboard_contributions(
            monkeypatch, dashboard_server, mounts=[_static_mount("habits", tmp_path)]
        )

        app = await started_app()

        prefixes = [call.args[0] for call in app.router.add_static.call_args_list]
        assert prefixes.index("/static/ext/habits") < prefixes.index("/static")
