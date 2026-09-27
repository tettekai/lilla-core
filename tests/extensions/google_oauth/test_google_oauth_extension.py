"""`lilla_core.extensions.google_oauth` の `google-oauth` 拡張が申告する内容のテスト。

OAuth2 の設定（`extensions.google_oauth` / `env.google_client_secret`）はこの拡張だけが
申告し、カレンダー一覧は `google-calendar` 拡張の `extensions.google_calendar` が持つ。
節名はコアが `name` から導くため、ここでは申告するモデルと導かれるキーを固定する。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

from lilla_core.core.config import core_env_field_names, extension_section_name
from lilla_core.extensions import google_oauth


@pytest.fixture
def oauth_only_config_root(tmp_path: Path) -> Path:
    """`extensions.google_oauth` だけを持つ最小構成の `lilla.yaml` を書いたディレクトリ。

    この拡張単体のテストが、他拡張の節を含む共通 YAML に依存しないようにする。
    """
    from lilla_core.testing import write_minimal_lilla_yaml

    write_minimal_lilla_yaml(
        tmp_path,
        extra={"extensions": {"google_oauth": {"client_id": "gc-id"}}},
    )
    return tmp_path


class TestGoogleOAuthExtensionDeclaration:
    """`google-oauth` 拡張の申告内容。"""

    def test_name(self) -> None:
        """拡張名は `google-oauth`。"""
        assert google_oauth.extension.name == "google-oauth"

    def test_declares_the_google_oauth_section(self) -> None:
        """申告するモデルは `GoogleConfig` で、キーは `extensions.google_oauth`。"""
        assert google_oauth.extension.config_model() is google_oauth.GoogleConfig
        assert extension_section_name(google_oauth.extension.name) == "google_oauth"

    def test_google_section_is_oauth_only(self) -> None:
        """`extensions.google_oauth` は OAuth2 の項目のみを持つ（カレンダー一覧は持たない）。"""
        assert set(google_oauth.GoogleConfig.model_fields) == {"client_id", "redirect_uri"}

    def test_google_section_is_optional(self) -> None:
        """必須フィールドを持たないため `extensions.google_oauth` 節そのものを省略できる。"""
        assert not any(
            f.is_required() for f in google_oauth.GoogleConfig.model_fields.values()
        )

    def test_declares_client_secret_env_field(self) -> None:
        """秘匿情報は `GOOGLE_CLIENT_SECRET` の 1 つだけ。"""
        assert google_oauth.extension.env_fields() == {
            "google_client_secret": "GOOGLE_CLIENT_SECRET"
        }

    def test_no_collision_with_core_env_names(self) -> None:
        """コア確定の `EnvConfig` フィールド名とは重ならない。"""
        assert not (set(google_oauth.extension.env_fields()) & core_env_field_names())

    def test_has_no_dependencies(self) -> None:
        """OAuth は他の拡張に依存しない（Calendar 抜きでも単独で載せられる）。"""
        assert google_oauth.extension.requires == ()


class TestGoogleOAuthComposition:
    """コアへの登録と設定の合成。"""

    def test_registers_alone(self, oauth_only_config_root: Path, monkeypatch) -> None:
        """Calendar を載せなくても、OAuth だけで登録・合成できる。"""
        from lilla_core.core.config import get_section
        from lilla_core.testing import use_extensions

        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "gc-secret")
        with use_extensions(google_oauth.extension, config_root=oauth_only_config_root) as cfg:
            section = get_section("google-oauth", google_oauth.GoogleConfig, cfg)

            assert section.client_id == "gc-id"
            assert section.redirect_uri == "http://localhost:8765/oauth/google-oauth/callback"
            assert cfg.env.google_client_secret == "gc-secret"

    def test_section_can_be_omitted(self, tmp_path: Path) -> None:
        """`extensions.google_oauth` 節が無くても既定値で合成できる。"""
        from lilla_core.core.config import get_section
        from lilla_core.testing import use_extensions, write_minimal_lilla_yaml

        write_minimal_lilla_yaml(tmp_path)
        with use_extensions(google_oauth.extension, config_root=tmp_path) as cfg:
            assert get_section("google-oauth", google_oauth.GoogleConfig, cfg).client_id is None

    def test_loads_from_the_import_path(
        self, oauth_only_config_root: Path, monkeypatch, tmp_path_factory
    ) -> None:
        """`LILLA_EXTENSIONS` に import パスを書けば `load_extensions()` で読める。"""
        from lilla_core.core import extension as ext_mod
        from lilla_core.testing import use_extensions

        from lilla_core.testing import write_minimal_lilla_yaml

        core_only_root = tmp_path_factory.mktemp("core_only")
        write_minimal_lilla_yaml(core_only_root)
        monkeypatch.setenv(ext_mod.EXTENSIONS_ENV_VAR, "lilla_core.extensions.google_oauth")
        # 登録内容と設定を元へ戻すため、拡張 0 個の `use_extensions()` の中でロードする
        # （入口の合成は拡張の節を持たない YAML で行い、ロードの直前に向け直す）。
        with use_extensions(config_root=core_only_root):
            monkeypatch.setenv("CONFIG_ROOT", str(oauth_only_config_root))
            loaded = ext_mod.load_extensions()

        assert [e.name for e in loaded] == ["google-oauth"]


class TestGoogleOAuthLocales:
    """認可完了時にブラウザへ返す文言のカタログ。"""

    def test_locale_dir_exists(self) -> None:
        """`locale_dirs()` はパッケージ同梱の `locales/` を指す。"""
        (locale_dir,) = google_oauth.extension.locale_dirs()

        assert locale_dir == Path(google_oauth.__file__).resolve().parent / "locales"
        assert (locale_dir / "ja.yaml").is_file()
        assert (locale_dir / "en.yaml").is_file()

    def test_catalogs_have_the_same_keys(self) -> None:
        """ja / en のカタログはトップレベルが拡張名だけで、同じキーを持つ。"""
        (locale_dir,) = google_oauth.extension.locale_dirs()
        ja = yaml.safe_load((locale_dir / "ja.yaml").read_text(encoding="utf-8"))
        en = yaml.safe_load((locale_dir / "en.yaml").read_text(encoding="utf-8"))

        assert list(ja) == ["google-oauth"]
        assert ja["google-oauth"].keys() == en["google-oauth"].keys()
        assert ja["google-oauth"]["callback"].keys() == en["google-oauth"]["callback"].keys()

    def test_message_resolves_after_registration(self, tmp_path: Path) -> None:
        """登録すると `t()` から拡張の文言が引ける。"""
        from lilla_core.testing import use_extensions, write_minimal_lilla_yaml
        from lilla_core.ui.messages import t

        write_minimal_lilla_yaml(tmp_path)
        with use_extensions(google_oauth.extension, config_root=tmp_path):
            text = t("google-oauth.callback.completed")

        assert text != "google-oauth.callback.completed"


class TestGoogleOAuthPublicRoute:
    """認可コードフローの戻り先の申告（ダッシュボードの認証の外側に載る公開ルート）。"""

    def test_declares_the_oauth_callback_route(self) -> None:
        """`GET /oauth/google-oauth/callback` を 1 本だけ申告する。"""
        from lilla_core.extensions.google_oauth.callback import handle_google_callback

        routes = google_oauth.extension.dashboard_public_routes()

        assert len(routes) == 1
        assert routes[0].method == "GET"
        assert routes[0].path == "/oauth/google-oauth/callback"
        assert routes[0].handler is handle_google_callback

    def test_path_is_derived_from_the_extension_name(self) -> None:
        """パスは `/oauth/{name}` 配下（接頭辞はコアの定数を参照せずリテラル）。"""
        path = google_oauth.extension.dashboard_public_routes()[0].path

        assert path.startswith(f"/oauth/{google_oauth.extension.name}/")

    def test_does_not_import_the_core_prefix_constant(self) -> None:
        """守る契約は `/oauth/{name}` であってコアの定数ではない。

        `DASHBOARD_PUBLIC_PREFIX` を import すると、公開の対象パスが増えて定数の
        意味が変わったときに、この拡張の経路が黙って追随してしまう。docstring での
        言及は許すため、import 文だけを AST で見る。
        """
        tree = ast.parse(Path(google_oauth.__file__).read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }

        assert "DASHBOARD_PUBLIC_PREFIX" not in imported

    def test_core_accepts_the_declaration(self, oauth_only_config_root: Path) -> None:
        """コアのロード時検証（`/oauth/{name}` 配下か）を通り、集約から読める。"""
        from lilla_core.core.extension import get_dashboard_public_routes
        from lilla_core.testing import use_extensions

        with use_extensions(google_oauth.extension, config_root=oauth_only_config_root):
            routes = get_dashboard_public_routes()

        assert [r.path for r in routes] == ["/oauth/google-oauth/callback"]

    def test_declares_no_dashboard_page(self) -> None:
        """タブは出さない（公開ルートだけ）。"""
        assert google_oauth.extension.dashboard_page() is None
        assert google_oauth.extension.dashboard_static_dir() is None
        assert google_oauth.extension.dashboard_routes() == []

    def test_default_redirect_uri_points_at_the_declared_route(self) -> None:
        """既定の `redirect_uri` は申告したパスを指す（設定を写し忘れない）。"""
        path = google_oauth.extension.dashboard_public_routes()[0].path

        assert google_oauth.GoogleConfig().redirect_uri.endswith(path)
