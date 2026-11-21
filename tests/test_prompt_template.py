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
    find_comments,
    load_template_settings,
    public_field_names,
    render_extension_prompt,
    render_extension_prompt_parts,
    render_or_raw,
    render_prompt,
    render_template,
    settings_for_template,
    strip_tag,
    preview_extension_prompt,
    preview_skill_prompt,
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
    async def test_injects_current_hour(self) -> None:
        """共通の変数 `current_hour` を渡す。"""
        assert await render_template("いまは {{ current_hour }}", now=_NOW) == "いまは 2026-11-14 20:xx"

    async def test_passes_caller_variables(self) -> None:
        """呼び出し元の変数も渡せる。"""
        assert await render_template("{{ a }}/{{ current_hour }}", {"a": "x"}, now=_NOW) == (
            "x/2026-11-14 20:xx"
        )

    async def test_rejects_caller_variable_named_like_common(self) -> None:
        """共通の変数と同じ名前は呼び出し元の誤りとして ValueError。"""
        with pytest.raises(ValueError, match="current_hour"):
            await render_template("x", {"current_hour": "y"}, now=_NOW)

    async def test_legacy_now_is_undefined(self) -> None:
        """廃止した `{{now}}` は未定義の名前として失敗する。"""
        with pytest.raises(PromptTemplateError):
            await render_template("{{now}}", now=_NOW)


class _FakeNotes:
    """テンプレートの `notes()` が読む保存先 `notes` の代わり（`read` だけを持つ）。"""

    def __init__(self, files: dict[str, str], error: Exception | None = None) -> None:
        """`files`（パス → 本文）と、読み取りで投げる例外を受け取る。"""
        self.files = files
        self.error = error
        self.reads: list[str] = []

    async def read(self, path: str) -> str:
        """本文を返す（無ければ `FileStorageNotFoundError`）。"""
        from lilla_core.core.file_storage import FileStorageNotFoundError

        self.reads.append(path)
        if self.error is not None:
            raise self.error
        if path not in self.files:
            raise FileStorageNotFoundError(f"not found: {path}")
        return self.files[path]


@pytest.fixture
def notes_storage(monkeypatch):
    """`get_file_storage("notes")` が返す保存先を差し替える（`set(storage)` で入れ替え）。"""
    holder: dict[str, object] = {"notes": _FakeNotes({"memory/core.md": "覚えたこと\n"})}
    monkeypatch.setattr(
        "lilla_core.core.extension.get_file_storage", lambda name: holder.get(name)
    )
    return holder


class TestNotesFunction:
    """どのテンプレートでも使える関数 `notes(path)`。"""

    async def test_reads_note(self, notes_storage) -> None:
        """保存先 `notes` のファイルの本文を展開する。"""
        assert await render_template('[{{ notes("memory/core.md") }}]') == "[覚えたこと\n]"

    async def test_missing_note_is_empty(self, notes_storage) -> None:
        """無いファイルは空文字（まだ作られていないノートを書いておける）。"""
        assert await render_template('[{{ notes("memory/none.md") }}]') == "[]"
        assert await render_template('{% if notes("memory/none.md") %}x{% endif %}') == ""

    async def test_available_in_extension_prompts(self, notes_storage) -> None:
        """拡張のプロンプト（`settings` と一緒）でも使える。"""
        text = '{% if settings.enabled %}{{ notes("memory/core.md") }}{% endif %}'
        assert await render_prompt(text, {"enabled": True}) == "覚えたこと\n"

    async def test_note_braces_are_not_expanded_again(self, notes_storage) -> None:
        """ノートの中の波括弧はもう一度展開しない。"""
        notes_storage["notes"] = _FakeNotes({"a.md": "{{ current_hour }}"})
        assert await render_template('{{ notes("a.md") }}') == "{{ current_hour }}"

    async def test_long_note_is_truncated_with_warning(self, notes_storage, caplog) -> None:
        """`MAX_NOTE_CHARS` を超える本文は切り詰めて WARNING を出す。"""
        from lilla_core.services import prompt_template

        notes_storage["notes"] = _FakeNotes({"a.md": "x" * (prompt_template.MAX_NOTE_CHARS + 5)})
        with caplog.at_level("WARNING"):
            result = await render_template('{{ notes("a.md") }}')
        assert len(result) == prompt_template.MAX_NOTE_CHARS
        assert "a.md" in caplog.text

    async def test_read_error_fails_rendering(self, notes_storage) -> None:
        """無いファイル以外の失敗（パスの規則違反など）は展開の失敗にする。"""
        from lilla_core.core.file_storage import FileStoragePathError

        notes_storage["notes"] = _FakeNotes({}, error=FileStoragePathError("bad path"))
        with pytest.raises(PromptTemplateError, match="bad path"):
            await render_template('{{ notes("../x") }}')

    async def test_without_notes_storage_fails_rendering(self, notes_storage) -> None:
        """保存先 `notes` が無ければ展開の失敗にする。"""
        notes_storage.clear()
        with pytest.raises(PromptTemplateError, match="not registered"):
            await render_template('{{ notes("a.md") }}')

    async def test_non_string_path_fails_rendering(self, notes_storage) -> None:
        """文字列でないパスは展開の失敗にする。"""
        with pytest.raises(PromptTemplateError):
            await render_template("{{ notes(1) }}")

    async def test_failure_falls_back_to_raw_text(self, notes_storage, caplog) -> None:
        """`render_or_raw()` では失敗しても展開前の本文を使う。"""
        notes_storage.clear()
        text = '{{ notes("a.md") }}'
        with caplog.at_level("WARNING"):
            assert await render_or_raw(text, "prompt.system") == text

    async def test_not_called_when_unused(self, notes_storage) -> None:
        """使わないテンプレートでは読みに行かない。"""
        storage = notes_storage["notes"]
        await render_template("plain")
        assert storage.reads == []

    async def test_name_is_reserved(self) -> None:
        """`notes` は共通の名前で、呼び出し元の変数には使えない。"""
        with pytest.raises(ValueError, match="notes"):
            await render_template("x", {"notes": "y"})


class TestStripTag:
    """フィルタ `strip_tag`。"""

    @pytest.mark.parametrize(
        "text",
        ["a<memo>b", "a</memo>b", "a</Memo >b", "a< / MEMO>b", 'a<memo id="x">b', "a</memo"],
    )
    def test_removes_tag_forms(self, text) -> None:
        """開きタグ・閉じタグ・大文字・空白・属性・閉じていない形を取り除く。"""
        assert "memo" not in strip_tag(text, "memo").lower()

    def test_keeps_words_and_other_tags(self, caplog) -> None:
        """単語としての名前と、名前が続く別のタグは残し、何も取り除かなければ警告しない。"""
        text = "memo の話 <memo_extra> <memo-x>"
        with caplog.at_level("WARNING"):
            assert strip_tag(text, "memo") == text
        assert caplog.text == ""

    def test_warns_with_count(self, caplog) -> None:
        """取り除いたら件数を WARNING に出す。"""
        with caplog.at_level("WARNING"):
            assert strip_tag("<memo>x</memo>", "memo") == "x"
        assert "Removed 2 'memo' tag(s)" in caplog.text

    @pytest.mark.parametrize("name", ["", "1abc", "a b", "a>b", None])
    def test_invalid_name(self, name) -> None:
        """タグ名の形でなければ ValueError。"""
        with pytest.raises(ValueError):
            strip_tag("x", name)

    async def test_usable_in_templates(self) -> None:
        """テンプレートのフィルタとして使える。"""
        assert await render_template('{{ "a</memo>b" | strip_tag("memo") }}') == "ab"


class TestRenderOrRaw:
    async def test_renders(self) -> None:
        """展開できれば展開した本文を返す。"""
        assert await render_or_raw("{{ current_hour }}\n", "prompt.system", now=_NOW) == (
            "2026-11-14 20:xx\n"
        )

    @pytest.mark.parametrize("text", ["{{ broken", "{{ missing }}"])
    async def test_returns_raw_with_warning_on_failure(self, text, caplog) -> None:
        """構文エラー・未定義の名前は WARNING を出して本文をそのまま返す。"""
        with caplog.at_level("WARNING"):
            assert await render_or_raw(text, "prompt.system", now=_NOW) == text
        assert "prompt.system" in caplog.text

    async def test_empty_text_is_returned_as_is(self) -> None:
        """空の本文は展開しない。"""
        assert await render_or_raw("", "prompt.system") == ""


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

    async def test_renders_value_and_branch(self) -> None:
        """値の埋め込みと条件分岐が効く。"""
        text = "{% if settings.enabled %}保存先: {{ settings.path }}{% endif %}"
        assert await render_prompt(text, {"enabled": True, "path": "a.md"}) == "保存先: a.md"

    async def test_plain_text_is_unchanged_including_trailing_newline(self) -> None:
        """記法を含まない本文は末尾の改行も含めて変わらない。"""
        text = "## 見出し\n本文です。\n\n"
        assert await render_prompt(text, {}) == text

    async def test_value_braces_are_not_expanded_again(self) -> None:
        """設定値の中の波括弧はもう一度展開しない。"""
        assert await render_prompt("{{ settings.path }}", {"path": "{{ x }}"}) == "{{ x }}"

    async def test_no_autoescape(self) -> None:
        """HTML ではないため自動エスケープしない。"""
        assert await render_prompt("{{ settings.v }}", {"v": "<a & b>"}) == "<a & b>"

    async def test_undefined_name_fails(self) -> None:
        """未定義の名前は空文字にせず失敗にする。"""
        with pytest.raises(PromptTemplateError, match="UndefinedError"):
            await render_prompt("{{ settings.missing }}", {"path": "a"})

    async def test_raw_block_keeps_braces(self) -> None:
        """`{% raw %}` の中はそのまま出る。"""
        assert await render_prompt("{% raw %}{{名前}}{% endraw %}", {}) == "{{名前}}"

    async def test_sandbox_blocks_attribute_access(self) -> None:
        """サンドボックスが内部属性への到達を拒否する。"""
        with pytest.raises(PromptTemplateError):
            await render_prompt("{{ settings.__class__.__mro__ }}", {})

    @pytest.mark.parametrize("name", ["items", "keys", "values", "get", "update"])
    async def test_dict_method_names_resolve_to_values(self, name) -> None:
        """dict のメソッド名と同じ項目も、属性でも添字でも値を返す。"""
        text = "{{ settings.%s }}/{{ settings['%s'] }}" % (name, name)
        assert await render_prompt(text, {name: "v"}) == "v/v"

    async def test_nested_values_are_wrapped(self) -> None:
        """入れ子の dict・リストの中の dict も同じ規則で引ける。"""
        text = "{{ settings.inner.items }}{% for x in settings.rows %},{{ x.get }}{% endfor %}"
        settings = {"inner": {"items": "a"}, "rows": [{"get": "b"}, {"get": "c"}]}
        assert await render_prompt(text, settings) == "a,b,c"

    async def test_view_supports_membership_iteration_and_truthiness(self) -> None:
        """`in`・`for`・真偽判定・フィルターが使える。"""
        text = (
            "{{ 'path' in settings }} {% for k in settings %}{{ k }};{% endfor %} "
            "{% if settings.empty %}y{% else %}n{% endif %} {{ settings.tags|join(',') }}"
        )
        settings = {"path": "a", "empty": {}, "tags": ["x", "y"]}
        assert await render_prompt(text, settings) == "True path;empty;tags; n x,y"

    async def test_view_hides_internals(self) -> None:
        """入れ物の中身の属性にはテンプレートから届かない。"""
        with pytest.raises(PromptTemplateError, match="unsafe"):
            await render_prompt("{{ settings._data }}", {})

    async def test_nested_undefined_name_fails(self) -> None:
        """入れ子の無い名前も失敗にする。"""
        with pytest.raises(PromptTemplateError, match="UndefinedError"):
            await render_prompt("{{ settings.inner.missing }}", {"inner": {}})

    async def test_syntax_error_is_prompt_template_error(self) -> None:
        """構文エラーも `PromptTemplateError` になる。"""
        with pytest.raises(PromptTemplateError, match="syntax"):
            await render_prompt("{% if %}", {})


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


class TestRenderExtensionPromptParts:
    """既定と追加分を独立したテンプレートとして展開する。"""

    async def test_each_part_is_independent(self, registered, settings_repo, caplog) -> None:
        """失敗した部分だけ展開前の本文になり、ほかの部分は展開した結果のまま。"""
        settings_repo.doc = {"extension": "diary", "values": {"path": "y.md"}}
        with caplog.at_level("WARNING"):
            result = await render_extension_prompt_parts(
                "diary", ["{% set who = 'a' %}{{ who }} {{ settings.path }}", "{{ who }}"]
            )
        assert result == ["a y.md", "{{ who }}"]
        assert any(
            r.levelname == "WARNING" and "'diary'" in r.getMessage() for r in caplog.records
        )

    async def test_settings_failure_returns_all_raw(self, registered, settings_repo) -> None:
        """設定を読めなければどの部分も展開前の本文を返し、設定は 1 回だけ読む。"""
        settings_repo.find_one.side_effect = RuntimeError("db down")
        texts = ["{{ settings.path }}", "{{ current_hour }}"]
        assert await render_extension_prompt_parts("diary", texts) == texts
        assert settings_repo.find_one.await_count == 1

    async def test_empty_list(self, registered, settings_repo) -> None:
        """部分が無ければ設定を読まない。"""
        assert await render_extension_prompt_parts("diary", []) == []
        settings_repo.find_one.assert_not_awaited()


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


class TestFindComments:
    """コメント（`{# … #}`）の検出。"""

    def test_finds_comments_with_line_numbers(self) -> None:
        """出てくる順に、始まりの行と中身を返す（空白制御つきも含む）。"""
        comments = find_comments("a\n{# x\ny #} b {#- z -#}c")
        assert [(c.line, c.text) for c in comments] == [(2, " x\ny "), (3, " z ")]

    def test_raw_block_is_not_a_comment(self) -> None:
        """`{% raw %}` の中はコメントにしない（Jinja2 と同じ）。"""
        assert find_comments("{% raw %}{# no #}{% endraw %}") == []

    def test_syntax_error_is_template_error(self) -> None:
        """閉じていないコメントは `PromptTemplateError`（行番号つき）。"""
        with pytest.raises(PromptTemplateError, match="line 1"):
            find_comments("{# open")


class TestPreviewSkillPrompt:
    """スキルの本文のプレビュー。"""

    async def test_renders_with_current_hour(self) -> None:
        """`current_hour` で展開し、コメントも返す。"""
        with patch("lilla_core.utils.datetime_utils.local_now", return_value=_NOW):
            preview = await preview_skill_prompt("{{ current_hour }}{# memo #}")
        assert preview.rendered == "2026-11-14 20:xx"
        assert [c.text for c in preview.comments] == [" memo "]
        assert preview.settings_unavailable is False

    async def test_settings_is_undefined(self) -> None:
        """スキルには `settings` を渡さない。"""
        with pytest.raises(PromptTemplateError):
            await preview_skill_prompt("{{ settings.path }}")


class TestPreviewExtensionPrompt:
    """拡張のプロンプトのプレビュー（保存時の検査と同じ処理）。"""

    async def test_renders_with_settings(self, registered, settings_repo) -> None:
        """今の設定で展開した本文を返す。"""
        preview = await preview_extension_prompt("diary", "保存先 {{ settings.path }}")
        assert preview.rendered == "保存先 {year}/{date}.md"
        assert preview.settings_unavailable is False

    async def test_secrets_are_not_exposed(self, registered, settings_repo) -> None:
        """秘匿値は変数に入らないため、参照すると失敗し、全体を出しても含まれない。"""
        settings_repo.doc = {"extension": "diary", "values": {"api_key": "top-secret"}}
        with pytest.raises(PromptTemplateError):
            await preview_extension_prompt("diary", "{{ settings.api_key }}")
        preview = await preview_extension_prompt("diary", "{{ settings }}")
        assert "top-secret" not in preview.rendered
        assert "inner-secret" not in preview.rendered

    async def test_settings_failure_checks_syntax_only(self, registered, settings_repo) -> None:
        """設定を読めなければ展開を飛ばし、その旨とコメントを返す。"""
        settings_repo.find_one.side_effect = RuntimeError("db down")
        preview = await preview_extension_prompt("diary", "{{ settings.missing }}{# c #}")
        assert preview.rendered is None
        assert preview.settings_unavailable is True
        assert [c.text for c in preview.comments] == [" c "]
        with pytest.raises(PromptTemplateError, match="syntax"):
            await preview_extension_prompt("diary", "{% if %}")

    async def test_matches_validation(self, registered, settings_repo) -> None:
        """保存時の検査で落ちる本文はプレビューでも落ちる。"""
        for text in ["{{ settings.missing }}", "{% if %}", "{{ settings.__class__ }}"]:
            with pytest.raises(PromptTemplateError):
                await preview_extension_prompt("diary", text)
            with pytest.raises(PromptTemplateError):
                await validate_extension_prompt("diary", text)
