"""`POST /api/admin/prompt-preview`（Jinja2 テンプレートのプレビュー API）のテスト。

本物の aiohttp アプリケーションに、プレビューのハンドラだけを載せて確かめる。
設定・スキルのリポジトリはモックに差し替え、保存しないことも確かめる。
"""
from __future__ import annotations

import importlib
import sys
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from pydantic import BaseModel, SecretStr

PATH = "/api/admin/prompt-preview"
_TZ = ZoneInfo("Asia/Tokyo")
_NOW = datetime(2026, 11, 14, 20, 45, tzinfo=_TZ)


class DiarySettings(BaseModel):
    """テスト用の画面管理の設定（秘匿値を含む）。"""

    day_rollover: str = "04:00"
    api_key: SecretStr = SecretStr("top-secret")


@pytest.fixture
def dashboard_server(monkeypatch):
    """他のテストが `web` をモックへ差し替えたモジュールを残しうるため、読み直して返す。"""
    monkeypatch.delitem(sys.modules, "lilla_core.handlers.dashboard_server", raising=False)
    return importlib.import_module("lilla_core.handlers.dashboard_server")


@pytest.fixture(autouse=True)
def fixed_now():
    """`current_hour` の基準を固定する。"""
    with patch("lilla_core.utils.datetime_utils.local_timezone", return_value=_TZ), \
            patch("lilla_core.utils.datetime_utils.local_now", return_value=_NOW):
        yield


@pytest.fixture
def settings_repo(monkeypatch) -> MagicMock:
    """画面管理の設定のリポジトリのモック（`doc` を返す）。"""
    repo = MagicMock()
    repo.doc = None
    repo.find_one = AsyncMock(side_effect=lambda name: repo.doc)
    repo.save = AsyncMock()
    monkeypatch.setattr(
        "lilla_core.repository.extension_settings_repository.get_extension_settings_repo",
        lambda: repo,
    )
    return repo


@pytest.fixture
def override_repo(monkeypatch) -> MagicMock:
    """上書きのリポジトリのモック（書き込まれないことを確かめる）。"""
    repo = MagicMock()
    repo.update_overrides = AsyncMock()
    monkeypatch.setattr(
        "lilla_core.repository.extension_override_repository.get_extension_override_repo",
        lambda: repo,
    )
    return repo


@pytest.fixture(autouse=True)
def registered(make_extension, use_extensions):
    """プロンプトと設定を持つ `diary`、何も持たない `plain` を登録する。"""
    use_extensions(
        make_extension("diary", default_prompt={"ja": "既定"}, settings_model=DiarySettings),
        make_extension("plain"),
    )


async def _client(app: web.Application) -> TestClient:
    """テストクライアントを起こす。"""
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.fixture
async def client(dashboard_server, settings_repo, override_repo):
    """プレビューのハンドラだけを載せたアプリのクライアント（認証はここでは見ない）。"""
    app = web.Application()
    app.router.add_post(PATH, dashboard_server.handle_api_prompt_preview)
    client = await _client(app)
    yield client
    await client.close()


def _skill(text: str) -> dict:
    return {"text": text, "target": {"type": "skill"}}


def _ext(text: str, name: str = "diary") -> dict:
    return {"text": text, "target": {"type": "extension", "name": name}}


class TestSkillPreview:
    async def test_renders_with_current_hour(self, client) -> None:
        """`current_hour` で展開し、消えるコメントを行番号つきで返す。"""
        res = await client.post(PATH, json=_skill("いま {{ current_hour }}\n{# メモ #}"))

        assert res.status == 200
        data = await res.json()
        assert data["rendered"] == "いま 2026-11-14 20:xx\n"
        assert data["comments"] == [{"line": 2, "text": " メモ "}]
        assert data["settings_unavailable"] is False

    async def test_strips_like_save(self, client, dashboard_server) -> None:
        """保存時と同じく前後の空白を除いてから展開する。"""
        res = await client.post(PATH, json=_skill("  本文\n\n"))

        assert (await res.json())["rendered"] == "本文"

    @pytest.mark.parametrize("text, match", [
        ("{% if %}", "line 1"),
        ("a\n{{ x", "line 2"),
        ("{{ missing }}", "missing"),
        ("{{ settings.day_rollover }}", "settings"),
    ])
    async def test_errors_are_400(self, client, dashboard_server, text, match) -> None:
        """構文エラー（行番号つき）・未定義の名前は 400 で、保存時の検査と同じく失敗する。"""
        res = await client.post(PATH, json=_skill(text))

        assert res.status == 400
        assert match in (await res.json())["error"]
        assert dashboard_server._check_skill_prompt(text) is not None


class TestExtensionPreview:
    async def test_renders_with_settings(self, client, override_repo, settings_repo) -> None:
        """今の設定で展開し、何も保存しない。"""
        settings_repo.doc = {"extension": "diary", "values": {"day_rollover": "05:00"}}
        res = await client.post(PATH, json=_ext("締め {{ settings.day_rollover }}"))

        assert res.status == 200
        assert (await res.json())["rendered"] == "締め 05:00"
        override_repo.update_overrides.assert_not_awaited()
        settings_repo.save.assert_not_awaited()

    async def test_secret_is_not_exposed(self, client) -> None:
        """秘匿値は参照できず、設定全体を出しても含まれない。"""
        res = await client.post(PATH, json=_ext("{{ settings.api_key }}"))
        assert res.status == 400

        res = await client.post(PATH, json=_ext("{{ settings }}"))
        assert res.status == 200
        assert "top-secret" not in (await res.json())["rendered"]

    async def test_undefined_name_is_400(self, client) -> None:
        """未定義の名前は 400。"""
        res = await client.post(PATH, json=_ext("{{ settings.missing }}"))

        assert res.status == 400
        assert "missing" in (await res.json())["error"]

    async def test_settings_unavailable_checks_syntax_only(self, client, settings_repo) -> None:
        """設定を読めなければ展開を飛ばし、その旨を返す（構文エラーは 400 のまま）。"""
        settings_repo.find_one.side_effect = RuntimeError("db down")

        res = await client.post(PATH, json=_ext("{{ settings.missing }}"))
        assert res.status == 200
        data = await res.json()
        assert data["rendered"] is None
        assert data["settings_unavailable"] is True

        res = await client.post(PATH, json=_ext("{% if %}"))
        assert res.status == 400

    @pytest.mark.parametrize("name", ["missing", "plain"])
    async def test_unknown_or_promptless_extension_is_404(self, client, name) -> None:
        """未ロードの拡張・プロンプトを持たない拡張は 404。"""
        res = await client.post(PATH, json=_ext("x", name=name))

        assert res.status == 404


class TestRequestValidation:
    @pytest.mark.parametrize("body", [
        [],
        {"target": {"type": "skill"}},
        {"text": 1, "target": {"type": "skill"}},
        {"text": "x"},
        {"text": "x", "target": {"type": "other"}},
    ])
    async def test_invalid_body_is_400(self, client, body) -> None:
        """ボディの形が不正なら 400。"""
        res = await client.post(PATH, json=body)

        assert res.status == 400


class TestAuthentication:
    async def test_unauthenticated_request_is_rejected(
        self, dashboard_server, settings_repo, monkeypatch,
    ) -> None:
        """セッションが無ければ 401 で、展開しない。"""
        monkeypatch.setattr(dashboard_server, "_get_valid_session", AsyncMock(return_value=None))
        app = web.Application(middlewares=[dashboard_server.auth_middleware])
        app.router.add_post(PATH, dashboard_server.handle_api_prompt_preview)
        client = await _client(app)
        try:
            res = await client.post(PATH, json=_ext("{{ settings.day_rollover }}"))
            assert res.status == 401
        finally:
            await client.close()
        settings_repo.find_one.assert_not_awaited()
