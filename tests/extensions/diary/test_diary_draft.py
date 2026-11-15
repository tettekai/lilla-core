"""`diary` 拡張の日記ドラフト（サービス・ツール・設定）のテスト。"""
from __future__ import annotations

import importlib.util
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_root = Path(__file__).parents[3]
_DIARY_DIR = _root / "src" / "lilla_core" / "extensions" / "diary"

TEMPLATE = """---
date: <<date>>
mood: <<moodscore>>
---
# <<date>>

## 今日の出来事
<<events>>

## ひとこと
<<memo>>

---
end
"""


def _load_tool(name: str):
    path = _DIARY_DIR / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _cfg():
    return SimpleNamespace(ui=SimpleNamespace(timezone="Asia/Tokyo"))


def _settings(template=TEMPLATE, rollover="04:00"):
    """日記の設定（ダッシュボードで保存した値）を作る。"""
    from lilla_core.extensions.diary import DiarySettings
    return DiarySettings(template=template, day_rollover=rollover)


def _jst(y, mo, d, h, mi=0):
    """JST の時刻を UTC の datetime で返す。"""
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc) - timedelta(hours=9)


@pytest.fixture
def stored():
    """`load_settings()` が返す保存済みの設定。`stored["v"]` を差し替えると次の読み取りから変わる。"""
    return {"v": _settings()}


@pytest.fixture
def svc(stored):
    from lilla_core.extensions.diary import service as s

    async def _load(name, model):
        assert name == "diary"
        value = stored["v"]
        if isinstance(value, Exception):
            raise value
        return value

    load = AsyncMock(side_effect=_load)
    with patch.object(s, "load_settings", load), \
         patch("lilla_core.core.config.get_config", return_value=_cfg()):
        s._test_load = load
        yield s


@pytest.fixture
def repo():
    r = MagicMock()
    r.get = AsyncMock(return_value=None)
    r.create_if_absent = AsyncMock(side_effect=lambda d, c: {"_id": d, "content": c})
    r.set_content = AsyncMock()
    with patch("lilla_core.extensions.diary.repository.get_diary_draft_repo", return_value=r):
        yield r


class TestDraftCreation:
    """初回 GET がテンプレから日付だけ埋めて作ること。"""

    @pytest.mark.asyncio
    async def test_first_get_fills_only_date(self, svc, repo):
        from datetime import date
        content = await svc.get_draft(date(2026, 9, 28))
        assert "date: 2026-09-28 (月)" in content
        assert "# 2026-09-28 (月)" in content
        assert "<<events>>" in content and "<<moodscore>>" in content
        assert "<<date>>" not in content
        repo.create_if_absent.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_existing_draft_is_returned_as_is(self, svc, repo):
        from datetime import date
        repo.get.return_value = {"_id": "2026-09-28", "content": "saved"}
        assert await svc.get_draft(date(2026, 9, 28)) == "saved"
        repo.create_if_absent.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_existing_draft_does_not_read_settings(self, svc, repo, stored):
        """既存のドラフトはテンプレを変えても・設定が読めなくてもそのまま返す。"""
        from datetime import date
        from lilla_core.services.extension_settings import SettingsUnavailableError
        repo.get.return_value = {"_id": "2026-09-28", "content": "saved"}
        stored["v"] = SettingsUnavailableError("db down")
        assert await svc.get_draft(date(2026, 9, 28)) == "saved"
        svc._test_load.assert_not_awaited()
        repo.set_content.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unset_template_fails_clearly(self, svc, repo, stored):
        """テンプレ未設定なら、画面での設定を促すエラーにし、何も作らない。"""
        from datetime import date
        stored["v"] = _settings(template=None)
        with pytest.raises(svc.DiaryDraftError, match="not set.*Extensions"):
            await svc.get_draft(date(2026, 9, 28))
        repo.create_if_absent.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_db_failure_is_not_treated_as_unset(self, svc, repo, stored):
        """設定を DB から読めないときは未設定扱いにせず、別のエラーにする。"""
        from datetime import date
        from lilla_core.services.extension_settings import SettingsUnavailableError
        stored["v"] = SettingsUnavailableError("db down")
        with pytest.raises(svc.DiaryDraftError) as e:
            await svc.get_draft(date(2026, 9, 28))
        assert "could not load" in str(e.value)
        assert "not set" not in str(e.value)
        repo.create_if_absent.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalid_stored_settings_fail(self, svc, repo, stored):
        """保存値が不正なら初期値で置き換えず、項目名つきで失敗する。"""
        from datetime import date
        from lilla_core.services.extension_settings import InvalidStoredSettingsError
        stored["v"] = InvalidStoredSettingsError(
            "bad", [{"field": "day_rollover", "message": "x"}]
        )
        with pytest.raises(svc.DiaryDraftError, match="day_rollover"):
            await svc.get_draft(date(2026, 9, 28))
        repo.create_if_absent.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_template_is_the_body_not_a_source_spec(self, svc, repo, stored):
        """`file:` で始まる本文も参照としては読まず、本文そのものとして使う。"""
        from datetime import date
        stored["v"] = _settings(template="file:/x/template.md\n<<date>>\n")
        content = await svc.get_draft(date(2026, 9, 28))
        assert content == "file:/x/template.md\n2026-09-28 (月)"

    @pytest.mark.asyncio
    async def test_next_draft_uses_new_template(self, svc, repo, stored):
        """保存後に始まる次の処理は新しいテンプレで作る（再起動は要らない）。"""
        from datetime import date
        await svc.get_draft(date(2026, 9, 28))
        stored["v"] = _settings(template="# <<date>>\n<<body>>\n")
        content = await svc.get_draft(date(2026, 9, 29))
        assert content == "# 2026-09-29 (火)\n<<body>>"


class TestSetDraft:
    """SET の時間帯チェックと固定文の検証。"""

    def _filled(self, svc, d="2026-09-28"):
        from datetime import date
        base = svc.build_initial_draft(svc.template_text(_settings()), date.fromisoformat(d))
        return base.replace("<<events>>", "散歩した\n\n- 昼食").replace("<<memo>>", "良い日")

    @pytest.mark.asyncio
    async def test_set_inside_window_saves(self, svc, repo):
        from datetime import date
        with patch.object(svc, "utc_now", return_value=_jst(2026, 9, 28, 22)):
            await svc.set_draft(date(2026, 9, 28), self._filled(svc))
        repo.set_content.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_set_after_midnight_still_same_diary_day(self, svc, repo):
        from datetime import date
        with patch.object(svc, "utc_now", return_value=_jst(2026, 9, 29, 3, 30)):
            await svc.set_draft(date(2026, 9, 28), self._filled(svc))
        repo.set_content.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_set_outside_window_fails_with_active_date(self, svc, repo):
        from datetime import date
        with patch.object(svc, "utc_now", return_value=_jst(2026, 9, 29, 5)):
            with pytest.raises(svc.DiaryDraftError) as e:
                await svc.set_draft(date(2026, 9, 28), self._filled(svc))
        assert "2026-09-29" in str(e.value)
        repo.set_content.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_broken_heading_is_rejected(self, svc, repo):
        from datetime import date
        broken = self._filled(svc).replace("## 今日の出来事", "## 変えた見出し")
        with patch.object(svc, "utc_now", return_value=_jst(2026, 9, 28, 22)):
            with pytest.raises(svc.DiaryDraftError):
                await svc.set_draft(date(2026, 9, 28), broken)
        repo.set_content.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_date_mismatch_is_rejected(self, svc, repo):
        from datetime import date
        wrong = self._filled(svc, "2026-09-27")
        with patch.object(svc, "utc_now", return_value=_jst(2026, 9, 28, 22)):
            with pytest.raises(svc.DiaryDraftError):
                await svc.set_draft(date(2026, 9, 28), wrong)

    @pytest.mark.asyncio
    async def test_truncated_tail_is_rejected(self, svc, repo):
        from datetime import date
        cut = self._filled(svc).replace("\nend", "")
        with patch.object(svc, "utc_now", return_value=_jst(2026, 9, 28, 22)):
            with pytest.raises(svc.DiaryDraftError):
                await svc.set_draft(date(2026, 9, 28), cut)

    @pytest.mark.asyncio
    async def test_reads_settings_once_per_set(self, svc, repo, stored):
        """1 回の保存では設定を 1 回だけ読み、時間帯もテンプレも同じ版を使う。"""
        from datetime import date

        async def _change_settings(*_):
            stored["v"] = _settings(template="# <<date>>\n", rollover="23:00")

        repo.set_content.side_effect = _change_settings
        with patch.object(svc, "utc_now", return_value=_jst(2026, 9, 28, 22)):
            await svc.set_draft(date(2026, 9, 28), self._filled(svc))
        svc._test_load.assert_awaited_once()

        # 保存をまたいだ次の処理は新しい版（23:00 区切り・新しいテンプレ）を使う
        with patch.object(svc, "utc_now", return_value=_jst(2026, 9, 28, 22)):
            with pytest.raises(svc.DiaryDraftError, match="2026-09-27"):
                await svc.set_draft(date(2026, 9, 28), self._filled(svc))

    @pytest.mark.asyncio
    async def test_custom_rollover_is_used(self, svc, repo, stored):
        """画面で保存した day_rollover で書き込み可能時間帯を判定する。"""
        from datetime import date
        stored["v"] = _settings(rollover="06:30")
        with patch.object(svc, "utc_now", return_value=_jst(2026, 9, 29, 5)):
            await svc.set_draft(date(2026, 9, 28), self._filled(svc))
        repo.set_content.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unset_template_saves_nothing(self, svc, repo, stored):
        from datetime import date
        filled = self._filled(svc)
        stored["v"] = _settings(template=None)
        with patch.object(svc, "utc_now", return_value=_jst(2026, 9, 28, 22)):
            with pytest.raises(svc.DiaryDraftError, match="not set"):
                await svc.set_draft(date(2026, 9, 28), filled)
        repo.set_content.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_db_failure_saves_nothing(self, svc, repo, stored):
        from datetime import date
        from lilla_core.services.extension_settings import SettingsUnavailableError
        filled = self._filled(svc)
        stored["v"] = SettingsUnavailableError("db down")
        with pytest.raises(svc.DiaryDraftError, match="could not load"):
            await svc.set_draft(date(2026, 9, 28), filled)
        repo.set_content.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_existing_draft_against_new_template_is_rejected(self, svc, repo, stored):
        """テンプレを変えた後は、古いテンプレの形の本文は今までどおり固定文の検証で弾く。"""
        from datetime import date
        filled = self._filled(svc)
        stored["v"] = _settings(template=TEMPLATE.replace("## ひとこと", "## メモ"))
        with patch.object(svc, "utc_now", return_value=_jst(2026, 9, 28, 22)):
            with pytest.raises(svc.DiaryDraftError, match="fixed text"):
                await svc.set_draft(date(2026, 9, 28), filled)
        repo.set_content.assert_not_awaited()

    def test_holes_may_stay_unfilled(self, svc):
        from datetime import date
        d = date(2026, 9, 28)
        t = svc.template_text(_settings())
        svc.validate_content(t, svc.build_initial_draft(t, d), d)


class TestFaircopy:
    """清書の穴埋め。"""

    def test_fills_holes_except_moodscore(self, svc):
        r = svc.make_faircopy("mood: <<moodscore>>\n<<events>>\n<< memo >>")
        assert r.content == "mood: <<moodscore>>\n-\n-"
        assert r.unresolved == ["moodscore"]

    @pytest.mark.asyncio
    async def test_does_not_read_settings(self, svc, repo):
        from datetime import date
        repo.get.return_value = {"_id": "2026-09-28", "content": "a <<memo>>"}
        assert (await svc.get_faircopy(date(2026, 9, 28))).content == "a -"
        svc._test_load.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_draft_fails_without_saving(self, svc, repo):
        from datetime import date
        with pytest.raises(svc.DiaryDraftError):
            await svc.get_faircopy(date(2026, 9, 28))
        repo.create_if_absent.assert_not_awaited()
        repo.set_content.assert_not_awaited()


class TestDoubleBraces:
    """`{{...}}` は穴ではなくただの文字列で、読み替えない。"""

    def test_template_keeps_double_braces(self):
        text = TEMPLATE + "\n{{memo}}\n"
        assert _settings(template=text).template == text

    @pytest.mark.asyncio
    async def test_get_draft_returns_content_as_is(self, svc, repo):
        from datetime import date
        repo.get.return_value = {"_id": "2026-09-28", "content": "a {{memo}} <<moodscore>>"}
        assert await svc.get_draft(date(2026, 9, 28)) == "a {{memo}} <<moodscore>>"

    @pytest.mark.asyncio
    async def test_faircopy_does_not_treat_double_braces_as_holes(self, svc, repo):
        from datetime import date
        repo.get.return_value = {"_id": "2026-09-28", "content": "a {{memo}} <<moodscore>>"}
        r = await svc.get_faircopy(date(2026, 9, 28))
        assert r.content == "a {{memo}} <<moodscore>>"
        assert r.unresolved == ["moodscore"]


class TestParseDate:
    """date 引数の検証。"""

    @pytest.mark.parametrize("v", [None, "", "today", "2026-9-28", "2026-02-30", 20260928])
    def test_invalid(self, svc, v):
        with pytest.raises(svc.DiaryDraftError):
            svc.parse_date(v)


class TestTools:
    """ツールの入出力と公開範囲。"""

    @pytest.mark.asyncio
    async def test_get_requires_date(self, svc, repo):
        mod = _load_tool("llm_diary_draft_get")
        res = await mod.execute({}, {})
        assert res["success"] is False

    @pytest.mark.asyncio
    async def test_get_returns_content(self, svc, repo):
        mod = _load_tool("llm_diary_draft_get")
        res = await mod.execute({"date": "2026-09-28"}, {})
        assert res["success"] is True
        assert res["data"]["date"] == "2026-09-28"

    @pytest.mark.asyncio
    async def test_set_error_is_reported(self, svc, repo):
        mod = _load_tool("llm_diary_draft_set")
        with patch.object(svc, "utc_now", return_value=_jst(2026, 9, 29, 5)):
            res = await mod.execute({"date": "2026-09-28", "content": "x"}, {})
        assert res["success"] is False
        assert "2026-09-29" in res["error"]

    @pytest.mark.asyncio
    async def test_faircopy_tool(self, svc, repo):
        repo.get.return_value = {"_id": "2026-09-28", "content": "a <<memo>> <<moodscore>>"}
        mod = _load_tool("llm_diary_draft_faircopy_get")
        res = await mod.execute({"date": "2026-09-28"}, {})
        assert res["data"]["content"] == "a - <<moodscore>>"
        assert res["data"]["unresolved_holes"] == ["moodscore"]

    def test_faircopy_yaml_is_task_only(self):
        configs = _DIARY_DIR / "tool_configs"
        text = (configs / "llm_diary_draft_faircopy_get.yaml").read_text()
        assert "supported_client_type: task" in text
        for n in ("llm_diary_draft_get", "llm_diary_draft_set"):
            assert "supported_client_type" not in (configs / f"{n}.yaml").read_text()


class TestDiarySchemas:
    """LLM に見せる説明文。"""

    _NAMES = ("llm_diary_draft_get", "llm_diary_draft_set", "llm_diary_draft_faircopy_get")

    @pytest.mark.parametrize("name", _NAMES)
    def test_schema_name_matches_file_name(self, name):
        assert _load_tool(name).SCHEMA["function"]["name"] == name

    @pytest.mark.parametrize("name", _NAMES)
    def test_descriptions_refer_to_default_tool_names(self, name):
        desc = _load_tool(name).SCHEMA["function"]["description"]
        assert re.search(r"(?<!llm_)diary_draft_(get|set)", desc) is None

    @pytest.mark.parametrize("name", _NAMES)
    def test_descriptions_are_spec_only(self, name):
        """会話向けの使い方（いつ呼ぶか・推測しない など）は default_prompt() 側にある。"""
        desc = _load_tool(name).SCHEMA["function"]["description"]
        assert "推測しない" not in desc
        assert "普段の会話" not in desc
        assert "必ず呼んで" not in desc

    @pytest.mark.parametrize("name", _NAMES)
    def test_date_description_refers_to_day_rollover(self, name):
        params = _load_tool(name).SCHEMA["function"]["parameters"]["properties"]
        assert "day_rollover" in params["date"]["description"]
        assert "書き込み可能時間帯の開始時刻" not in params["date"]["description"]


class TestDayRollover:
    """day_rollover による書き込み可能時間帯の境界。"""

    @pytest.mark.parametrize("h,mi,ok,ng", [
        (3, 59, "2026-09-28", "2026-09-29"),
        (4, 0, "2026-09-29", "2026-09-28"),
    ])
    def test_default_boundary(self, svc, h, mi, ok, ng):
        from datetime import date
        rollover = svc.day_rollover(_settings())
        with patch.object(svc, "utc_now", return_value=_jst(2026, 9, 29, h, mi)):
            svc.ensure_writable(date.fromisoformat(ok), rollover)
            with pytest.raises(svc.DiaryDraftError) as e:
                svc.ensure_writable(date.fromisoformat(ng), rollover)
        assert f"the date you can write now is {ok}" in str(e.value)

    def test_default_window_matches_previous_behavior(self, svc):
        from datetime import date
        start, end = svc.writable_window(date(2026, 9, 28), svc.day_rollover(_settings()))
        assert start.strftime("%Y-%m-%d %H:%M") == "2026-09-28 04:00"
        assert end.strftime("%Y-%m-%d %H:%M") == "2026-09-29 04:00"

    def test_custom_rollover(self, svc):
        from datetime import date
        rollover = svc.day_rollover(_settings(rollover="06:30"))
        start, end = svc.writable_window(date(2026, 9, 28), rollover)
        assert (start.hour, start.minute) == (6, 30)
        assert end.date() == date(2026, 9, 29)
        assert svc.current_writable_date(rollover, _jst(2026, 9, 29, 6, 29)) == date(2026, 9, 28)
        assert svc.current_writable_date(rollover, _jst(2026, 9, 29, 6, 30)) == date(2026, 9, 29)


class TestDiarySettings:
    """ダッシュボードで編集する日記の設定（`DiarySettings`）。"""

    def test_defaults(self):
        from lilla_core.extensions.diary import DiarySettings
        s = DiarySettings()
        assert s.template is None
        assert s.day_rollover == "04:00"

    def test_rollover_is_normalized(self):
        from lilla_core.extensions.diary import DiarySettings
        assert DiarySettings(day_rollover="6:5").day_rollover == "06:05"
        assert DiarySettings(day_rollover="04:00").day_rollover == "04:00"

    @pytest.mark.parametrize("v", ["25:00", "4", "ab:cd", "", 240, None, True])
    def test_invalid_rollover(self, v):
        from lilla_core.extensions.diary import DiarySettings
        with pytest.raises(ValueError):
            DiarySettings(day_rollover=v)

    @pytest.mark.parametrize("v", ["", "   ", "\n\t"])
    def test_blank_template_is_rejected(self, v):
        """空・空白だけの本文は拒否する（未設定にするなら None）。"""
        from lilla_core.extensions.diary import DiarySettings
        with pytest.raises(ValueError, match="blank"):
            DiarySettings(template=v)

    @pytest.mark.parametrize("v", ["# <<>>\n", "<< two words >>\n", "# <<date\n", "x >>\n"])
    def test_unreadable_holes_are_rejected(self, v):
        from lilla_core.extensions.diary import DiarySettings
        with pytest.raises(ValueError, match="template"):
            DiarySettings(template=v)

    def test_template_is_kept_as_is(self):
        """本文は書き換えない（改行・末尾の空白・`file:` の文字列もそのまま）。"""
        from lilla_core.extensions.diary import DiarySettings
        body = "file:/x.md\r\n# << date >>  \n<<memo>>\n\n"
        assert DiarySettings(template=body).template == body

    def test_is_frozen(self):
        """処理中に読んだスナップショットは書き換えられない。"""
        from lilla_core.extensions.diary import DiarySettings
        s = DiarySettings()
        with pytest.raises(Exception):
            s.day_rollover = "05:00"


class TestDiaryConfig:
    """`extensions.diary` の YAML モデル（画面管理へ移した項目は持たない）。"""

    def test_has_no_fields(self):
        from lilla_core.extensions.diary import DiaryConfig
        assert DiaryConfig.model_fields == {}

    def test_rejects_unknown_keys(self):
        from lilla_core.extensions.diary import DiaryConfig
        with pytest.raises(ValueError):
            DiaryConfig.model_validate({"templete": "file:/x"})
