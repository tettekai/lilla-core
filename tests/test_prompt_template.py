"""`lilla_core.services.prompt_template` のテスト。"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from pydantic import BaseModel, SecretBytes, SecretStr

from lilla_core.services.prompt_template import (
    PromptTemplateError,
    check_syntax,
    current_hour,
    load_template_settings,
    public_field_names,
    render_extension_prompt,
    render_or_raw,
    render_prompt,
    render_template,
    settings_for_template,
    validate_extension_prompt,
)


class Inner(BaseModel):
    """入れ子のモデル（秘匿値を含む）。"""

    label: str = "inner"
    token: SecretStr = SecretStr("inner-secret")


class SampleSettings(BaseModel):
    """テスト用の画面管理の設定。"""

    path: str = "{year}/{date}.md"
    enabled: bool = True
    storages: list[str] = ["notes"]
    api_key: SecretStr | None = None
    raw: SecretBytes = SecretBytes(b"bytes")
    keys: list[SecretStr] = []
    inner: Inner = Inner()
    mixed: list = []


@pytest.fixture
def settings_repo(monkeypatch) -> MagicMock:
    """画面管理の設定のリポジトリのモック（`doc` を返す）。"""
    repo = MagicMock()
    repo.doc = None
    repo.find_one = AsyncMock(side_effect=lambda name: repo.doc)
    monkeypatch.setattr(
        "lilla_core.repository.extension_settings_repository.get_extension_settings_repo",
        lambda: repo,
    )
    return repo


@pytest.fixture
def registered(make_extension, use_extensions):
    """設定を持つ `diary` と、持たない `plain` を登録する。"""
    use_extensions(
        make_extension("diary", settings_model=SampleSettings),
        make_extension("plain"),
    )


_TZ = ZoneInfo("Asia/Tokyo")
_NOW = datetime(2026, 11, 14, 20, 45, tzinfo=_TZ)


@pytest.fixture(autouse=True)
def local_tz():
    """`current_hour` が使うタイムゾーンを固定する（実行環境の OS に左右されないように）。"""
    with patch("lilla_core.utils.datetime_utils.local_timezone", return_value=_TZ):
        yield


class TestCurrentHour:
    def test_formats_to_hour(self) -> None:
        """`YYYY-MM-DD HH:xx` の形で、分は伏せる（曜日は付けない）。"""
        assert current_hour(_NOW) == "2026-11-14 20:xx"

    def test_converts_aware_datetime_to_local_timezone(self) -> None:
        """タイムゾーン付きの日時は `local_timezone()` へ合わせてから書式にする。"""
        utc = datetime(2026, 11, 14, 11, 45, tzinfo=ZoneInfo("UTC"))
        assert current_hour(utc) == "2026-11-14 20:xx"

    def test_defaults_to_local_now(self) -> None:
        """省略時は `local_now()` を使う。"""
        with patch("lilla_core.utils.datetime_utils.local_now", return_value=_NOW):
            assert current_hour() == "2026-11-14 20:xx"


class TestRenderTemplate:
    def test_injects_current_hour(self) -> None:
        """共通の変数 `current_hour` を渡す。"""
        assert render_template("いまは {{ current_hour }}", now=_NOW) == "いまは 2026-11-14 20:xx"

    def test_passes_caller_variables(self) -> None:
        """呼び出し元の変数も渡せる。"""
        assert render_template("{{ a }}/{{ current_hour }}", {"a": "x"}, now=_NOW) == (
            "x/2026-11-14 20:xx"
        )

    def test_rejects_caller_variable_named_like_common(self) -> None:
        """共通の変数と同じ名前は呼び出し元の誤りとして ValueError。"""
        with pytest.raises(ValueError, match="current_hour"):
            render_template("x", {"current_hour": "y"}, now=_NOW)

    def test_legacy_now_is_undefined(self) -> None:
        """廃止した `{{now}}` は未定義の名前として失敗する。"""
        with pytest.raises(PromptTemplateError):
            render_template("{{now}}", now=_NOW)


class TestRenderOrRaw:
    def test_renders(self) -> None:
        """展開できれば展開した本文を返す。"""
        assert render_or_raw("{{ current_hour }}\n", "prompt.system", now=_NOW) == (
            "2026-11-14 20:xx\n"
        )

    @pytest.mark.parametrize("text", ["{{ broken", "{{ missing }}"])
    def test_returns_raw_with_warning_on_failure(self, text, caplog) -> None:
        """構文エラー・未定義の名前は WARNING を出して本文をそのまま返す。"""
        with caplog.at_level("WARNING"):
            assert render_or_raw(text, "prompt.system", now=_NOW) == text
        assert "prompt.system" in caplog.text

    def test_empty_text_is_returned_as_is(self) -> None:
        """空の本文は展開しない。"""
        assert render_or_raw("", "prompt.system") == ""


class TestCheckSyntax:
    """構文だけの検査。"""

    def test_valid_template_passes(self) -> None:
        """正しい構文なら例外を投げない。"""
        check_syntax("{% if settings.enabled %}on{% endif %} {{ settings.path }}")

    def test_syntax_error_has_line_number(self) -> None:
        """構文エラーは行番号つきの `PromptTemplateError` になる。"""
        with pytest.raises(PromptTemplateError, match="line 2"):
            check_syntax("ok\n{% if settings.enabled %}")

    def test_undefined_name_is_not_a_syntax_error(self) -> None:
        """未定義の名前は構文の検査では見つからない（展開で見つかる）。"""
        check_syntax("{{ settings.missing }}")


class TestRenderPrompt:
    """`settings` を渡した展開。"""

    def test_renders_value_and_branch(self) -> None:
        """値の埋め込みと条件分岐が効く。"""
        text = "{% if settings.enabled %}保存先: {{ settings.path }}{% endif %}"
        assert render_prompt(text, {"enabled": True, "path": "a.md"}) == "保存先: a.md"

    def test_plain_text_is_unchanged_including_trailing_newline(self) -> None:
        """記法を含まない本文は末尾の改行も含めて変わらない。"""
        text = "## 見出し\n本文です。\n\n"
        assert render_prompt(text, {}) == text

    def test_value_braces_are_not_expanded_again(self) -> None:
        """設定値の中の波括弧はもう一度展開しない。"""
        assert render_prompt("{{ settings.path }}", {"path": "{{ x }}"}) == "{{ x }}"

    def test_no_autoescape(self) -> None:
        """HTML ではないため自動エスケープしない。"""
        assert render_prompt("{{ settings.v }}", {"v": "<a & b>"}) == "<a & b>"

    def test_undefined_name_fails(self) -> None:
        """未定義の名前は空文字にせず失敗にする。"""
        with pytest.raises(PromptTemplateError, match="UndefinedError"):
            render_prompt("{{ settings.missing }}", {"path": "a"})

    def test_raw_block_keeps_braces(self) -> None:
        """`{% raw %}` の中はそのまま出る。"""
        assert render_prompt("{% raw %}{{名前}}{% endraw %}", {}) == "{{名前}}"

    def test_sandbox_blocks_attribute_access(self) -> None:
        """サンドボックスが内部属性への到達を拒否する。"""
        with pytest.raises(PromptTemplateError):
            render_prompt("{{ settings.__class__.__mro__ }}", {})

    @pytest.mark.parametrize("name", ["items", "keys", "values", "get", "update"])
    def test_dict_method_names_resolve_to_values(self, name) -> None:
        """dict のメソッド名と同じ項目も、属性でも添字でも値を返す。"""
        text = "{{ settings.%s }}/{{ settings['%s'] }}" % (name, name)
        assert render_prompt(text, {name: "v"}) == "v/v"

    def test_nested_values_are_wrapped(self) -> None:
        """入れ子の dict・リストの中の dict も同じ規則で引ける。"""
        text = "{{ settings.inner.items }}{% for x in settings.rows %},{{ x.get }}{% endfor %}"
        settings = {"inner": {"items": "a"}, "rows": [{"get": "b"}, {"get": "c"}]}
        assert render_prompt(text, settings) == "a,b,c"

    def test_view_supports_membership_iteration_and_truthiness(self) -> None:
        """`in`・`for`・真偽判定・フィルターが使える。"""
        text = (
            "{{ 'path' in settings }} {% for k in settings %}{{ k }};{% endfor %} "
            "{% if settings.empty %}y{% else %}n{% endif %} {{ settings.tags|join(',') }}"
        )
        settings = {"path": "a", "empty": {}, "tags": ["x", "y"]}
        assert render_prompt(text, settings) == "True path;empty;tags; n x,y"

    def test_view_hides_internals(self) -> None:
        """入れ物の中身の属性にはテンプレートから届かない。"""
        with pytest.raises(PromptTemplateError, match="unsafe"):
            render_prompt("{{ settings._data }}", {})

    def test_nested_undefined_name_fails(self) -> None:
        """入れ子の無い名前も失敗にする。"""
        with pytest.raises(PromptTemplateError, match="UndefinedError"):
            render_prompt("{{ settings.inner.missing }}", {"inner": {}})

    def test_syntax_error_is_prompt_template_error(self) -> None:
        """構文エラーも `PromptTemplateError` になる。"""
        with pytest.raises(PromptTemplateError, match="syntax"):
            render_prompt("{% if %}", {})


class TestSecretStripping:
    """秘匿値の除外。"""

    def test_public_field_names_exclude_secret_annotations(self) -> None:
        """秘匿値の型を含む注釈の項目は名前ごと除く（入れ子のモデルは残す）。"""
        assert public_field_names(SampleSettings) == [
            "path", "enabled", "storages", "inner", "mixed",
        ]

    def test_secret_values_are_removed_recursively(self) -> None:
        """入れ子のモデル・注釈の無いリストの中の秘匿値も取り除く。"""
        settings = SampleSettings(
            api_key=SecretStr("top-secret"),
            mixed=[SecretStr("in-list"), "kept", {"k": SecretStr("in-dict"), "v": 1}],
        )
        data = settings_for_template(settings)
        assert data == {
            "path": "{year}/{date}.md",
            "enabled": True,
            "storages": ["notes"],
            "inner": {"label": "inner"},
            "mixed": ["kept", {"v": 1}],
        }
        assert "secret" not in repr(data)


class TestLoadTemplateSettings:
    """拡張の設定の読み込み。"""

    async def test_extension_without_settings_returns_empty_without_db(
        self, registered, settings_repo,
    ) -> None:
        """`settings_model()` を持たない拡張は DB を読まずに空を返す。"""
        assert await load_template_settings("plain") == {}
        settings_repo.find_one.assert_not_awaited()

    async def test_reads_stored_values(self, registered, settings_repo) -> None:
        """保存値を読み、秘匿値を除いた dict にする。"""
        settings_repo.doc = {"extension": "diary", "values": {"path": "x.md", "enabled": False}}
        data = await load_template_settings("diary")
        assert data["path"] == "x.md"
        assert data["enabled"] is False
        assert "api_key" not in data


class TestRenderExtensionPrompt:
    """会話への注入用の展開（失敗しても例外を投げない）。"""

    async def test_renders_with_settings(self, registered, settings_repo, caplog) -> None:
        """設定値で展開し、成功したら WARNING を出さない。"""
        settings_repo.doc = {"extension": "diary", "values": {"path": "y.md"}}
        with caplog.at_level("WARNING"):
            assert await render_extension_prompt("diary", "保存先 {{ settings.path }}") == "保存先 y.md"
        assert not [r for r in caplog.records if r.levelname == "WARNING"]

    async def test_renders_current_hour_with_settings(self, registered, settings_repo) -> None:
        """拡張のプロンプトでも共通の `current_hour` が使える。"""
        settings_repo.doc = {"extension": "diary", "values": {"path": "y.md"}}
        with patch("lilla_core.utils.datetime_utils.local_now", return_value=_NOW):
            rendered = await render_extension_prompt(
                "diary", "{{ current_hour }} {{ settings.path }}"
            )
        assert rendered == "2026-11-14 20:xx y.md"

    async def test_render_failure_returns_text_with_warning(
        self, registered, settings_repo, caplog,
    ) -> None:
        """展開に失敗したら拡張名を含む WARNING を出して元の本文を返す。"""
        text = "{{ settings.missing }}"
        with caplog.at_level("WARNING"):
            assert await render_extension_prompt("diary", text) == text
        assert any(
            r.levelname == "WARNING" and "'diary'" in r.getMessage() for r in caplog.records
        )

    async def test_settings_failure_returns_text_with_warning(
        self, registered, settings_repo, caplog,
    ) -> None:
        """設定を読めなければ WARNING を出して元の本文を返す。"""
        settings_repo.find_one.side_effect = RuntimeError("db down")
        text = "{{ settings.path }}"
        with caplog.at_level("WARNING"):
            assert await render_extension_prompt("diary", text) == text
        assert any(
            r.levelname == "WARNING" and "'diary'" in r.getMessage() for r in caplog.records
        )

    async def test_invalid_stored_settings_returns_text(self, registered, settings_repo) -> None:
        """保存値が不正なときも元の本文を返す。"""
        settings_repo.doc = {"extension": "diary", "values": {"unknown_key": 1}}
        text = "{{ settings.path }}"
        assert await render_extension_prompt("diary", text) == text


class TestValidateExtensionPrompt:
    """保存時の検査（構文と、今の設定での試しの展開）。"""

    async def test_valid_template_passes(self, registered, settings_repo) -> None:
        """今の設定で展開できれば例外を投げない。"""
        await validate_extension_prompt("diary", "{{ settings.path }}")

    async def test_undefined_name_is_rejected(self, registered, settings_repo) -> None:
        """未定義の名前は保存時に拒否する。"""
        with pytest.raises(PromptTemplateError):
            await validate_extension_prompt("diary", "{{ settings.missing }}")

    async def test_untaken_branch_is_not_checked(self, registered, settings_repo) -> None:
        """通らなかった分岐の中の未定義の名前は見つからない（既知の限界）。"""
        settings_repo.doc = {"extension": "diary", "values": {"enabled": False}}
        await validate_extension_prompt(
            "diary", "{% if settings.enabled %}{{ settings.missing }}{% endif %}"
        )

    async def test_settings_failure_checks_syntax_only(self, registered, settings_repo) -> None:
        """設定を読めなければ試しの展開を飛ばし、構文だけを確かめる。"""
        settings_repo.find_one.side_effect = RuntimeError("db down")
        await validate_extension_prompt("diary", "{{ settings.missing }}")
        with pytest.raises(PromptTemplateError, match="syntax"):
            await validate_extension_prompt("diary", "{% if %}")

    async def test_extension_without_settings_rejects_settings_reference(
        self, registered, settings_repo,
    ) -> None:
        """設定を持たない拡張で `settings.名前` を使うと拒否する。"""
        with pytest.raises(PromptTemplateError):
            await validate_extension_prompt("plain", "{{ settings.path }}")
