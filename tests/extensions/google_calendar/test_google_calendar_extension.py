"""`lilla_core.extensions.google_calendar` の `google-calendar` 拡張のテスト。

カレンダー一覧の `extensions.google_calendar` を申告するのはこの拡張だけで、
OAuth2 の項目は `google-oauth` 拡張が持つことをここで固定する（節名はコアが
`name` から導く）。`google-oauth` への依存の申告（`requires` / `required_env_fields`）と、
ツールの探索ルートの申告も同様。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lilla_core.core.config import core_env_field_names, extension_section_name
from lilla_core.extensions import google_calendar, google_oauth

_PACKAGE_DIR = Path(google_calendar.__file__).resolve().parent


@pytest.fixture
def calendar_config_root(tmp_path: Path) -> Path:
    """OAuth とカレンダーの節だけを持つ最小構成の `lilla.yaml` を書いたディレクトリ。

    この拡張のテストが、他拡張の節を含む共通 YAML に依存しないようにする。
    """
    from lilla_core.testing import write_minimal_lilla_yaml

    write_minimal_lilla_yaml(
        tmp_path,
        extra={
            "extensions": {
                "google_oauth": {"client_id": "gc-id"},
                "google_calendar": {
                    "calendars": [{"id": "primary", "friendly_name": "メイン"}]
                },
            }
        },
    )
    return tmp_path


class TestGoogleCalendarExtensionDeclaration:
    """`google-calendar` 拡張の申告内容。"""

    def test_name(self) -> None:
        """拡張名は `google-calendar`。"""
        assert google_calendar.extension.name == "google-calendar"

    def test_declares_the_google_calendar_section(self) -> None:
        """申告するモデルは `GoogleCalendarConfig` で、キーは `extensions.google_calendar`。"""
        assert google_calendar.extension.config_model() is google_calendar.GoogleCalendarConfig
        assert extension_section_name(google_calendar.extension.name) == "google_calendar"

    def test_section_holds_calendars_only(self) -> None:
        """`extensions.google_calendar` はカレンダー一覧だけを持つ（OAuth2 や TZ の項目は持たない）。"""
        assert set(google_calendar.GoogleCalendarConfig.model_fields) == {"calendars"}

    def test_calendar_entry_fields(self) -> None:
        """各要素は `id` と `friendly_name` の組。"""
        assert set(google_calendar.CalendarEntryConfig.model_fields) == {
            "id",
            "friendly_name",
        }

    def test_section_is_optional(self) -> None:
        """必須フィールドを持たないため `extensions.google_calendar` 節そのものを省略できる。"""
        assert not any(
            f.is_required()
            for f in google_calendar.GoogleCalendarConfig.model_fields.values()
        )

    def test_declares_no_env_fields(self) -> None:
        """秘匿情報は `google-oauth` 拡張の担当で、こちらは何も申告しない。"""
        assert google_calendar.extension.env_fields() == {}

    def test_no_collision_with_core_env_names(self) -> None:
        """コア確定の `EnvConfig` フィールド名とは重ならない。"""
        assert not (set(google_calendar.extension.env_fields()) & core_env_field_names())


class TestDependencyOnGoogleOAuth:
    """`google-oauth` 拡張への依存の申告。"""

    def test_requires_google_oauth(self) -> None:
        """`requires` で並び順（依存先が自分より前）を検証させる。

        クライアントが読む `extensions.google_oauth` への依存も、コア契約上はこれで表す。
        """
        assert google_calendar.extension.requires == ("google-oauth",)

    def test_requires_client_secret_env_field(self) -> None:
        """クライアントが読む `env.google_client_secret` を要求側として申告する。"""
        assert google_calendar.extension.required_env_fields() == ["google_client_secret"]

    def test_requirements_are_provided_by_google_oauth(self) -> None:
        """要求した名前は `google-oauth` 拡張が実際に提供している。"""
        assert google_oauth.extension.config_model() is not None
        assert set(google_calendar.extension.required_env_fields()) <= set(
            google_oauth.extension.env_fields()
        )

    def test_sections_do_not_overlap_with_google_oauth(self) -> None:
        """`google_oauth` と `google_calendar` は別々の拡張の名前から導かれる。"""
        assert extension_section_name(google_calendar.extension.name) != extension_section_name(
            google_oauth.extension.name
        )


class TestComposition:
    """コアへの登録と設定の合成。"""

    def test_registers_after_google_oauth(self, calendar_config_root: Path) -> None:
        """`google-oauth` の後に並べれば登録でき、カレンダー一覧が型付きで読める。"""
        from lilla_core.core.config import get_section
        from lilla_core.testing import use_extensions

        with use_extensions(
            google_oauth.extension, google_calendar.extension, config_root=calendar_config_root
        ) as cfg:
            calendars = get_section(
                "google-calendar", google_calendar.GoogleCalendarConfig, cfg
            ).calendars

        assert [(c.id, c.friendly_name) for c in calendars] == [("primary", "メイン")]

    def test_fails_without_google_oauth(self, tmp_path: Path) -> None:
        """`google-oauth` を載せずに登録すると fail-fast する。"""
        from lilla_core.testing import use_extensions, write_minimal_lilla_yaml

        write_minimal_lilla_yaml(tmp_path)
        with pytest.raises(ValueError, match="google-oauth"):
            with use_extensions(google_calendar.extension, config_root=tmp_path):
                pass

    def test_fails_when_ordered_before_google_oauth(self, calendar_config_root: Path) -> None:
        """`google-oauth` より前に並べると順序違反で fail-fast する（コアは並べ替えない）。"""
        from lilla_core.testing import use_extensions

        with pytest.raises(ValueError, match="google-oauth"):
            with use_extensions(
                google_calendar.extension,
                google_oauth.extension,
                config_root=calendar_config_root,
            ):
                pass

    def test_loads_from_the_import_paths(
        self, calendar_config_root: Path, monkeypatch, tmp_path_factory
    ) -> None:
        """`LILLA_EXTENSIONS` に 2 つの import パスを並べれば `load_extensions()` で読める。"""
        from lilla_core.core import extension as ext_mod
        from lilla_core.testing import use_extensions

        monkeypatch.setenv(
            ext_mod.EXTENSIONS_ENV_VAR,
            "lilla_core.extensions.google_oauth,lilla_core.extensions.google_calendar",
        )
        from lilla_core.testing import write_minimal_lilla_yaml

        core_only_root = tmp_path_factory.mktemp("core_only")
        write_minimal_lilla_yaml(core_only_root)
        # 登録内容と設定を元へ戻すため、拡張 0 個の `use_extensions()` の中でロードする
        # （入口の合成は拡張の節を持たない YAML で行い、ロードの直前に向け直す）。
        with use_extensions(config_root=core_only_root):
            monkeypatch.setenv("CONFIG_ROOT", str(calendar_config_root))
            loaded = ext_mod.load_extensions()

        assert [e.name for e in loaded] == ["google-oauth", "google-calendar"]


class TestToolRoots:
    """ツールの探索ルートの申告。"""

    def test_points_at_the_bundled_tools_dir(self) -> None:
        """パッケージ同梱の `tools/` を指す。"""
        roots = google_calendar.extension.tool_roots()

        assert roots == [_PACKAGE_DIR / "tools"]
        assert roots[0].is_dir()

    def test_bundles_both_calendar_tools(self) -> None:
        """取得・作成の両ツールがその配下にある（YAML の `type` で解決できる）。"""
        root = google_calendar.extension.tool_roots()[0]

        assert (root / "llm_calendar_get.py").is_file()
        assert (root / "llm_calendar_create.py").is_file()

    def test_bundles_no_tool_config(self) -> None:
        """ツール YAML は利用者の `${CONFIG_ROOT}/tools` にあるものを使う（同梱しない）。"""
        assert google_calendar.extension.tool_config_roots() == []

    def test_tools_load_from_user_yaml(self, calendar_config_root: Path) -> None:
        """登録すると、利用者の YAML（`type: llm_calendar_get` など）からツールが読める。

        ツール本体はパッケージ内にあり、コアのツール探索（`tool_roots()`）と
        ロード可能ディレクトリの検査を通ってロードされる。
        """
        from lilla_core.loaders import llm_tool_loader
        from lilla_core.testing import use_extensions

        tools_dir = calendar_config_root / "tools"
        tools_dir.mkdir()
        (tools_dir / "llm_calendar_get.yaml").write_text("type: llm_calendar_get\n")
        (tools_dir / "llm_calendar_create.yaml").write_text("type: llm_calendar_create\n")

        with use_extensions(
            google_oauth.extension, google_calendar.extension, config_root=calendar_config_root
        ):
            tools = llm_tool_loader.load_llm_tools(config_root=calendar_config_root)
            create_schema = tools["llm_calendar_create"]["schema"]

        assert set(tools) == {"llm_calendar_get", "llm_calendar_create"}
        # `build_schema` が登録済みのカレンダー一覧を calendar パラメータへ注入している。
        calendar_prop = create_schema["function"]["parameters"]["properties"]["calendar"]
        assert calendar_prop["enum"] == ["メイン"]
