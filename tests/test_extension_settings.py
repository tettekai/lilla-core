"""画面管理の拡張設定（`Extension.settings_model()`）のテスト。

ロード時の検証（初期値・担当の二重宣言）、`lilla.yaml` に残った画面管理の項目の拒否、
`services/extension_settings.py` の読み取り・検証・保存・画面向けの組み立てを確かめる。
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel, Field, ValidationError, field_validator

from lilla_core.services import extension_settings as svc
from lilla_core.services.extension_settings import (
    InvalidStoredSettingsError,
    SettingsUnavailableError,
    SettingsValidationError,
)
from lilla_core.testing import use_extensions as compose_with, write_minimal_lilla_yaml


class DiaryYamlConfigBase(BaseModel):
    """入れ子のモデル（汎用フォームの対象外）。"""

    note: str = ""


def _storage(read_only: bool = False):
    """何もしない `FileStorage` を返す（`read_only` だけを指定できる）。"""
    from lilla_core.core.file_storage import FileStorage, FileWriteResult

    class _Storage(FileStorage):
        @property
        def read_only(self) -> bool:
            return read_only

        async def read(self, path):
            return ""

        async def write(self, path, content, mode="overwrite"):
            return FileWriteResult(path=path, created=True)

        async def list(self, path=""):
            return []

        async def exists(self, path):
            return False

    return _Storage()


class DiarySettings(BaseModel):
    """テスト用の画面管理の設定（未設定を取れる複数行の文字列と、時刻の文字列）。"""

    template: str | None = Field(
        default=None, title="Template", json_schema_extra={"multiline": True}
    )
    day_rollover: str = Field(default="04:00", pattern=r"^\d{2}:\d{2}$")

    @field_validator("template")
    @classmethod
    def _not_blank(cls, value: str | None) -> str | None:
        """空・空白だけの本文は拒否する（未設定は `None`）。"""
        if value is not None and not value.strip():
            raise ValueError("template must not be blank")
        return value


class JournalSettings(BaseModel):
    """チェックボックス群（静的・動的な選択肢）と、汎用フォームで扱えない項目を持つ設定。"""

    storages: list[str] = Field(
        default=["notes"],
        json_schema_extra={"widget": "checkbox-group", "choices_from": "writable_file_storages"},
    )
    levels: list[int] = Field(
        default=[1],
        json_schema_extra={"widget": "checkbox-group", "choices": [1, {"value": 2, "title": "Two"}]},
    )
    tags: list[str] = []
    nested: DiaryYamlConfigBase = DiaryYamlConfigBase()


class DiaryYamlConfig(BaseModel):
    """YAML 担当の項目だけを持つ設定。"""

    note: str = ""


@pytest.fixture
def repo(monkeypatch) -> MagicMock:
    """設定のリポジトリのモック。`doc` が保存済みのドキュメント。"""
    repo = MagicMock()
    repo.doc = None

    async def _find(name):
        return repo.doc

    async def _save(name, values):
        repo.doc = {
            "extension": name,
            "values": values,
            "updated_at": datetime(2026, 11, 7, tzinfo=timezone.utc),
        }

    repo.find_one = AsyncMock(side_effect=_find)
    repo.save = AsyncMock(side_effect=_save)
    monkeypatch.setattr(
        "lilla_core.repository.extension_settings_repository.get_extension_settings_repo",
        lambda: repo,
    )
    return repo


@pytest.fixture
def diary(make_extension, use_extensions):
    """`diary`（画面管理の設定あり）と `plain`（無し）を登録する。"""
    use_extensions(
        make_extension("diary", settings_model=DiarySettings, config_model=DiaryYamlConfig),
        make_extension("plain"),
    )


class TestDeclaration:
    """ロード時の検証。"""

    def test_registered_models(self, diary) -> None:
        """宣言した拡張だけが登録される。"""
        from lilla_core.core.extension import get_settings_models

        assert get_settings_models() == {"diary": DiarySettings}

    def test_rejects_non_model(self, make_extension, use_extensions) -> None:
        """`BaseModel` サブクラス以外は落とす。"""
        with pytest.raises(ValueError, match="BaseModel subclass"):
            use_extensions(make_extension("diary", settings_model=dict))

    def test_rejects_field_without_default(self, make_extension, use_extensions) -> None:
        """初期値の無い項目はロード時に落とす。"""

        class Missing(BaseModel):
            template: str

        with pytest.raises(ValueError, match="must all have defaults.*template"):
            use_extensions(make_extension("diary", settings_model=Missing))

    def test_rejects_invalid_default(self, make_extension, use_extensions) -> None:
        """検証を通らない初期値（宣言ミス）はロード時に落とす。"""

        class BadDefault(BaseModel):
            day_rollover: str = Field(default="4am", pattern=r"^\d{2}:\d{2}$")

        with pytest.raises(ValueError, match="invalid default values"):
            use_extensions(make_extension("diary", settings_model=BadDefault))

    def test_rejects_double_declaration(self, make_extension, use_extensions) -> None:
        """YAML（config_model）と画面（settings_model）の両方で同じ項目を宣言したら落とす。"""

        class Both(BaseModel):
            template: str | None = None

        with pytest.raises(ValueError, match="declares template in both"):
            use_extensions(
                make_extension("diary", settings_model=DiarySettings, config_model=Both)
            )


class TestWidgetDeclaration:
    """`json_schema_extra` の `widget` 宣言のロード時の検証。"""

    @pytest.mark.parametrize(
        ("annotation", "default", "extra", "match"),
        [
            (list[str], [], {"widget": "tags"}, "unknown widget 'tags'"),
            (str, "", {"widget": "checkbox-group", "choices": ["a"]}, "not a list of scalar"),
            (list[str], [], {"widget": "checkbox-group"}, "exactly one of"),
            (
                list[str],
                [],
                {"widget": "checkbox-group", "choices": ["a"], "choices_from": "writable_file_storages"},
                "exactly one of",
            ),
            (list[str], [], {"widget": "checkbox-group", "choices_from": "nope"}, "unknown choices_from"),
            (list[str], [], {"widget": "checkbox-group", "choices": "a"}, "invalid choices"),
            (list[str], [], {"widget": "checkbox-group", "choices": [{"title": "x"}]}, "invalid choices"),
            (list[int], [], {"widget": "checkbox-group", "choices": ["1", 2]}, "does not match"),
            (list[str], [], {"widget": "checkbox-group", "choices": [1]}, "does not match"),
        ],
    )
    def test_rejects_invalid_widget(
        self, make_extension, use_extensions, annotation, default, extra, match
    ) -> None:
        """未知の部品・配列でない・選択肢の宣言漏れや重複・未知のプロバイダはロード時に落とす。"""
        from pydantic import create_model

        model = create_model(
            "Bad", value=(annotation, Field(default=default, json_schema_extra=extra))
        )
        with pytest.raises(ValueError, match=match):
            use_extensions(make_extension("diary", settings_model=model))

    def test_accepts_valid_widgets(self, make_extension, use_extensions) -> None:
        """静的・動的な選択肢のチェックボックス群は登録できる。"""
        from lilla_core.core.extension import get_settings_models

        use_extensions(make_extension("journal", settings_model=JournalSettings))

        assert get_settings_models() == {"journal": JournalSettings}


class TestYamlRejection:
    """`lilla.yaml` に残った画面管理の項目。"""

    def test_dashboard_item_in_yaml_names_the_migration_target(self, make_extension, tmp_path) -> None:
        """画面管理の項目を YAML に書くと、移行先（ダッシュボード）を示して起動時に落ちる。"""
        write_minimal_lilla_yaml(
            tmp_path,
            extra={"extensions": {"diary": {"note": "ok", "template": "file:diary.md"}}},
        )
        ext = make_extension("diary", settings_model=DiarySettings, config_model=DiaryYamlConfig)

        with pytest.raises(ValidationError, match=r"dashboard.*extensions\.diary\.template"):
            with compose_with(ext, config_root=tmp_path):
                pass

    def test_without_config_model_still_names_the_migration_target(self, make_extension, tmp_path) -> None:
        """YAML の節を持たない拡張でも、未知キーではなく移行先を示して落ちる。"""
        write_minimal_lilla_yaml(
            tmp_path, extra={"extensions": {"diary": {"day_rollover": "04:00"}}}
        )
        ext = make_extension("diary", settings_model=DiarySettings)

        with pytest.raises(ValidationError, match=r"extensions\.diary\.day_rollover"):
            with compose_with(ext, config_root=tmp_path):
                pass

    def test_yaml_items_still_work(self, make_extension, tmp_path) -> None:
        """YAML 担当の項目はそのまま読める（画面管理の項目が無ければ回帰しない）。"""
        write_minimal_lilla_yaml(tmp_path, extra={"extensions": {"diary": {"note": "hello"}}})
        ext = make_extension("diary", settings_model=DiarySettings, config_model=DiaryYamlConfig)

        with compose_with(ext, config_root=tmp_path) as cfg:
            assert cfg.extensions.diary.note == "hello"


class TestLoadSettings:
    """型付きの動的読み取り。"""

    async def test_unsaved_returns_declared_defaults(self, diary, repo) -> None:
        """保存していなければ、宣言された初期値（未設定を含む）を返す。"""
        settings = await svc.load_settings("diary", DiarySettings)

        assert isinstance(settings, DiarySettings)
        assert settings.template is None
        assert settings.day_rollover == "04:00"

    async def test_db_failure_is_distinct_from_unsaved(self, diary, repo) -> None:
        """DB から読めなければ、初期値ではなく `SettingsUnavailableError`。"""
        repo.find_one.side_effect = RuntimeError("db down")

        with pytest.raises(SettingsUnavailableError):
            await svc.load_settings("diary")

    @pytest.mark.parametrize(
        "values",
        [
            {"template": "   "},
            {"day_rollover": "4am"},
            {"template": "x", "legacy": 1},
            "not a dict",
        ],
    )
    async def test_invalid_stored_values_are_an_error(self, diary, repo, values) -> None:
        """保存値が不正なら初期値で置き換えず、エラーにする（保存値も書き換えない）。"""
        repo.doc = {"extension": "diary", "values": values}

        with pytest.raises(InvalidStoredSettingsError) as excinfo:
            await svc.load_settings("diary")

        assert excinfo.value.errors
        assert repo.doc["values"] == values
        repo.save.assert_not_awaited()

    async def test_snapshot_and_next_process(self, diary, repo) -> None:
        """保存後の次の読み取りは新しい値、先に読んだスナップショットは古い値のまま。"""
        in_progress = await svc.load_settings("diary", DiarySettings)

        await svc.save_settings("diary", {"template": "# {{date}}", "day_rollover": "05:00"})
        next_process = await svc.load_settings("diary", DiarySettings)

        assert in_progress.template is None
        assert in_progress.day_rollover == "04:00"
        assert next_process.template == "# {{date}}"
        assert next_process.day_rollover == "05:00"

    async def test_undeclared_or_wrong_model(self, diary, repo) -> None:
        """宣言していない拡張・宣言と違うモデルは `ValueError`。"""
        with pytest.raises(ValueError, match="does not declare"):
            await svc.load_settings("plain")
        with pytest.raises(ValueError, match="not DiaryYamlConfig"):
            await svc.load_settings("diary", DiaryYamlConfig)
        repo.find_one.assert_not_awaited()


class TestSaveSettings:
    """検証と保存。"""

    async def test_saves_validated_whole_set(self, diary, repo) -> None:
        """一式を検証して JSON の値で 1 回だけ保存する。省略した項目は初期値になる。"""
        await svc.save_settings("diary", {"template": "# body\n"})

        repo.save.assert_awaited_once_with("diary", {"template": "# body\n", "day_rollover": "04:00"})

    async def test_empty_list_is_kept(self, make_extension, use_extensions, repo) -> None:
        """全部オフ（`[]`）は初期値に戻さず、読み直しても `[]` のまま。"""
        use_extensions(make_extension("journal", settings_model=JournalSettings))

        await svc.save_settings("journal", {"storages": [], "levels": []})
        loaded = await svc.load_settings("journal", JournalSettings)

        assert repo.save.await_args.args[1]["storages"] == []
        assert loaded.storages == []
        assert loaded.levels == []

    async def test_value_outside_dynamic_choices_is_saved(
        self, make_extension, use_extensions, repo
    ) -> None:
        """今の動的な候補に無い値でも、モデルの検証を通れば拒否しない。"""
        use_extensions(make_extension("journal", settings_model=JournalSettings))

        await svc.save_settings("journal", {"storages": ["gone"]})

        assert repo.save.await_args.args[1]["storages"] == ["gone"]

    async def test_explicit_unset_is_allowed(self, diary, repo) -> None:
        """未設定（`None`）は空の本文と区別して保存できる。"""
        await svc.save_settings("diary", {"template": None})

        assert repo.save.await_args.args[1]["template"] is None

    @pytest.mark.parametrize(
        "values, field",
        [
            ({"template": ""}, "template"),
            ({"template": 3}, "template"),
            ({"day_rollover": "25"}, "day_rollover"),
            ({"unknown": "x"}, "unknown"),
            (["template"], ""),
        ],
    )
    async def test_invalid_request_saves_nothing(self, diary, repo, values, field) -> None:
        """不正な一式は項目ごとの理由つきで拒否し、何も保存しない。"""
        with pytest.raises(SettingsValidationError) as excinfo:
            await svc.save_settings("diary", values)

        assert field in [error["field"] for error in excinfo.value.errors]
        repo.save.assert_not_awaited()

    async def test_save_failure_propagates(self, diary, repo) -> None:
        """DB への保存の失敗はそのまま伝える（成功扱いにしない）。"""
        repo.save.side_effect = RuntimeError("db down")

        with pytest.raises(RuntimeError):
            await svc.save_settings("diary", {"template": "x"})


class TestDetail:
    """画面向けの組み立て。"""

    def test_describe_fields(self) -> None:
        """項目の型・未設定を取れるか・複数行・初期値・パターンを返す。"""
        fields = {field["name"]: field for field in svc.describe_fields(DiarySettings)}

        assert fields["template"]["type"] == "string"
        assert fields["template"]["nullable"] is True
        assert fields["template"]["multiline"] is True
        assert fields["template"]["title"] == "Template"
        assert fields["template"]["default"] is None
        assert fields["day_rollover"]["nullable"] is False
        assert fields["day_rollover"]["multiline"] is False
        assert fields["day_rollover"]["default"] == "04:00"
        assert fields["day_rollover"]["pattern"] == r"^\d{2}:\d{2}$"

    def test_describe_fields_has_no_widget_by_default(self) -> None:
        """`widget` の無い項目は `widget` / `items_type` / `choices` が `None` で、扱える。"""
        fields = {field["name"]: field for field in svc.describe_fields(DiarySettings)}

        assert fields["template"]["widget"] is None
        assert fields["template"]["items_type"] is None
        assert fields["template"]["choices"] is None
        assert fields["template"]["supported"] is True

    def test_describe_checkbox_group(self) -> None:
        """チェックボックス群は部品・要素の型・静的な選択肢を返し、動的な選択肢は解決しない。"""
        fields = {field["name"]: field for field in svc.describe_fields(JournalSettings)}

        storages = fields["storages"]
        assert storages["type"] == "array"
        assert storages["widget"] == "checkbox-group"
        assert storages["items_type"] == "string"
        assert storages["choices"] is None
        assert storages["choices_from"] == "writable_file_storages"
        assert storages["default"] == ["notes"]
        assert storages["supported"] is True
        levels = fields["levels"]
        assert levels["items_type"] == "integer"
        assert levels["choices"] == [{"value": 1, "title": "1"}, {"value": 2, "title": "Two"}]

    def test_type_alone_does_not_pick_a_widget(self) -> None:
        """`widget` の無い配列・入れ子のモデルは部品を推測せず、未対応にする。"""
        fields = {field["name"]: field for field in svc.describe_fields(JournalSettings)}

        assert fields["tags"]["type"] == "array"
        assert fields["tags"]["widget"] is None
        assert fields["tags"]["supported"] is False
        assert fields["nested"]["type"] == "object"
        assert fields["nested"]["supported"] is False

    async def test_detail_resolves_writable_file_storages(
        self, make_extension, use_extensions, repo
    ) -> None:
        """`writable_file_storages` は書き込める保存先だけを登録順に選択肢にする。"""
        use_extensions(
            make_extension(
                "journal",
                settings_model=JournalSettings,
                file_storages={
                    "zeta": _storage(),
                    "archive": _storage(read_only=True),
                    "alpha": _storage(),
                },
            )
        )

        detail = await svc.build_settings_detail("journal")

        fields = {field["name"]: field for field in detail["fields"]}
        assert fields["storages"]["choices"] == [
            {"value": "zeta", "title": "zeta"},
            {"value": "alpha", "title": "alpha"},
        ]
        assert fields["levels"]["choices"][1] == {"value": 2, "title": "Two"}

    async def test_detail_survives_provider_failure(
        self, make_extension, use_extensions, repo, monkeypatch
    ) -> None:
        """選択肢のプロバイダが失敗しても、空の候補で詳細を返す。"""
        use_extensions(make_extension("journal", settings_model=JournalSettings))

        def _boom():
            raise RuntimeError("boom")

        monkeypatch.setitem(svc.CHOICE_PROVIDERS, "writable_file_storages", _boom)

        detail = await svc.build_settings_detail("journal")

        fields = {field["name"]: field for field in detail["fields"]}
        assert fields["storages"]["choices"] == []

    async def test_unsaved(self, diary, repo) -> None:
        """未保存なら初期値を値として返し、`stored` は偽。"""
        detail = await svc.build_settings_detail("diary")

        assert detail["stored"] is False
        assert detail["values"] == {"template": None, "day_rollover": "04:00"}
        assert detail["error"] is None

    async def test_stored(self, diary, repo) -> None:
        """保存済みならその値と更新日時を返す。"""
        await svc.save_settings("diary", {"template": "x"})

        detail = await svc.build_settings_detail("diary")

        assert detail["stored"] is True
        assert detail["values"]["template"] == "x"
        assert detail["updated_at"].startswith("2026-11-07")

    async def test_unavailable(self, diary, repo) -> None:
        """読めなければ値を出さず `unavailable`。"""
        repo.find_one.side_effect = RuntimeError("db down")

        detail = await svc.build_settings_detail("diary")

        assert detail["values"] is None
        assert detail["error"]["kind"] == "unavailable"

    async def test_invalid_shows_raw_values(self, diary, repo) -> None:
        """不正な保存値は、直せるよう生の値と理由を返す。"""
        repo.doc = {"extension": "diary", "values": {"template": " ", "day_rollover": "04:00"}}

        detail = await svc.build_settings_detail("diary")

        assert detail["values"] == {"template": " ", "day_rollover": "04:00"}
        assert detail["error"]["kind"] == "invalid"
        assert detail["error"]["errors"][0]["field"] == "template"

    async def test_extension_without_settings(self, diary, repo) -> None:
        """設定を宣言していない拡張は `None`。"""
        assert await svc.build_settings_detail("plain") is None
