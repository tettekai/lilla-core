"""`lilla_core.extensions.google_drive` の `google-drive` 拡張のテスト。

ホワイトリストなどの `extensions.google_drive` を申告するのはこの拡張だけで、
OAuth2 の項目は `google-oauth` 拡張が持つことをここで固定する（節名はコアが
`name` から導く）。`google-oauth` への依存の申告（`requires` / `required_env_fields`）と、
ツールの探索ルートの申告も同様。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from lilla_core.core.config import core_env_field_names, extension_section_name
from lilla_core.extensions import google_drive, google_oauth

_PACKAGE_DIR = Path(google_drive.__file__).resolve().parent
_TOOL_STEMS = {
    "llm_google_drive_search_text",
    "llm_google_drive_get_text",
    "llm_google_drive_write_text",
    "llm_google_sheets_get_range",
}


@pytest.fixture
def drive_config_root(tmp_path: Path) -> Path:
    """OAuth と Drive の節だけを持つ最小構成の `lilla.yaml` を書いたディレクトリ。"""
    from lilla_core.testing import write_minimal_lilla_yaml

    write_minimal_lilla_yaml(
        tmp_path,
        extra={
            "extensions": {
                "google_oauth": {"client_id": "gc-id"},
                "google_drive": {
                    "read_only_folders": ["folder-ro"],
                    "writable_folders": ["folder-rw"],
                    "write_scope": "full",
                },
            }
        },
    )
    return tmp_path


class TestGoogleDriveExtensionDeclaration:
    """`google-drive` 拡張の申告内容。"""

    def test_name(self) -> None:
        """拡張名は `google-drive`。"""
        assert google_drive.extension.name == "google-drive"

    def test_declares_the_google_drive_section(self) -> None:
        """申告するモデルは `GoogleDriveConfig` で、キーは `extensions.google_drive`。"""
        assert google_drive.extension.config_model() is google_drive.GoogleDriveConfig
        assert extension_section_name(google_drive.extension.name) == "google_drive"

    def test_section_fields(self) -> None:
        """ホワイトリスト 2 つ・書き込みスコープ・読み取り上限だけを持つ（OAuth2 の項目は持たない）。"""
        assert set(google_drive.GoogleDriveConfig.model_fields) == {
            "read_only_folders",
            "writable_folders",
            "write_scope",
            "max_read_chars",
        }

    def test_section_is_optional(self) -> None:
        """必須フィールドを持たないため `extensions.google_drive` 節そのものを省略できる。"""
        assert not any(
            f.is_required() for f in google_drive.GoogleDriveConfig.model_fields.values()
        )

    def test_declares_no_env_fields(self) -> None:
        """秘匿情報は `google-oauth` 拡張の担当で、こちらは何も申告しない。"""
        assert google_drive.extension.env_fields() == {}
        assert not (set(google_drive.extension.env_fields()) & core_env_field_names())


class TestGoogleDriveConfig:
    """`extensions.google_drive` のモデルの検証。"""

    def test_defaults(self) -> None:
        """既定はホワイトリストが空（すべて拒否）・`write_scope: file`・上限 100,000 文字。"""
        cfg = google_drive.GoogleDriveConfig()

        assert cfg.read_only_folders == []
        assert cfg.writable_folders == []
        assert cfg.write_scope == "file"
        assert cfg.max_read_chars == 100_000

    def test_strips_folder_ids(self) -> None:
        """フォルダ ID の前後の空白は取り除く。"""
        cfg = google_drive.GoogleDriveConfig(read_only_folders=[" abc "])

        assert cfg.read_only_folders == ["abc"]

    @pytest.mark.parametrize("field", ["read_only_folders", "writable_folders"])
    def test_rejects_blank_folder_ids(self, field: str) -> None:
        """空文字・空白だけの ID は設定エラーにする。"""
        with pytest.raises(ValidationError):
            google_drive.GoogleDriveConfig(**{field: ["ok", "  "]})

    def test_rejects_id_in_both_lists(self) -> None:
        """同じ ID が読み取り専用と書き込み可能の両方にあれば設定エラーにする。"""
        with pytest.raises(ValidationError, match="both"):
            google_drive.GoogleDriveConfig(read_only_folders=["x"], writable_folders=["x"])

    def test_rejects_unknown_write_scope(self) -> None:
        """`write_scope` は `file` / `full` だけ。"""
        with pytest.raises(ValidationError):
            google_drive.GoogleDriveConfig(write_scope="drive")

    def test_rejects_non_positive_max_read_chars(self) -> None:
        """`max_read_chars` は正の整数。"""
        with pytest.raises(ValidationError):
            google_drive.GoogleDriveConfig(max_read_chars=0)

    def test_rejects_unknown_keys(self) -> None:
        """綴り間違いの項目名は黙って無視せず設定エラーにする。"""
        with pytest.raises(ValidationError):
            google_drive.GoogleDriveConfig(writeable_folders=["x"])


class TestDependencyOnGoogleOAuth:
    """`google-oauth` 拡張への依存の申告。"""

    def test_requires_google_oauth(self) -> None:
        """`requires` で並び順（依存先が自分より前）を検証させる。"""
        assert google_drive.extension.requires == ("google-oauth",)

    def test_requires_client_secret_env_field(self) -> None:
        """クライアントが読む `env.google_client_secret` を要求側として申告し、`google-oauth` が提供している。"""
        assert google_drive.extension.required_env_fields() == ["google_client_secret"]
        assert set(google_drive.extension.required_env_fields()) <= set(
            google_oauth.extension.env_fields()
        )


class TestComposition:
    """コアへの登録と設定の合成。"""

    def test_registers_after_google_oauth(self, drive_config_root: Path) -> None:
        """`google-oauth` の後に並べれば登録でき、設定が型付きで読める。"""
        from lilla_core.core.config import get_section
        from lilla_core.testing import use_extensions

        with use_extensions(
            google_oauth.extension, google_drive.extension, config_root=drive_config_root
        ) as cfg:
            section = get_section("google-drive", google_drive.GoogleDriveConfig, cfg)

        assert section.read_only_folders == ["folder-ro"]
        assert section.writable_folders == ["folder-rw"]
        assert section.write_scope == "full"

    def test_fails_without_google_oauth(self, tmp_path: Path) -> None:
        """`google-oauth` を載せずに登録すると fail-fast する。"""
        from lilla_core.testing import use_extensions, write_minimal_lilla_yaml

        write_minimal_lilla_yaml(tmp_path)
        with pytest.raises(ValueError, match="google-oauth"):
            with use_extensions(google_drive.extension, config_root=tmp_path):
                pass

    def test_fails_when_ordered_before_google_oauth(self, drive_config_root: Path) -> None:
        """`google-oauth` より前に並べると順序違反で fail-fast する（コアは並べ替えない）。"""
        from lilla_core.testing import use_extensions

        with pytest.raises(ValueError, match="google-oauth"):
            with use_extensions(
                google_drive.extension, google_oauth.extension, config_root=drive_config_root
            ):
                pass


class TestToolRoots:
    """ツールの探索ルートの申告。"""

    def test_points_at_the_bundled_tools_dir(self) -> None:
        """パッケージ同梱の `tools/` を指し、4 つのツールがその配下にある。"""
        roots = google_drive.extension.tool_roots()

        assert roots == [_PACKAGE_DIR / "tools"]
        assert {p.stem for p in roots[0].glob("llm_*.py")} == _TOOL_STEMS

    def test_bundles_no_tool_config(self) -> None:
        """ツール YAML は利用者の `${CONFIG_ROOT}/tools` にあるものを使う（同梱しない）。"""
        assert google_drive.extension.tool_config_roots() == []

    def test_tools_load_from_user_yaml(self, drive_config_root: Path) -> None:
        """登録すると、利用者の YAML（`type: llm_google_drive_get_text` など）からツールが読める。"""
        from lilla_core.loaders import llm_tool_loader
        from lilla_core.testing import use_extensions

        tools_dir = drive_config_root / "tools"
        tools_dir.mkdir()
        for stem in _TOOL_STEMS:
            (tools_dir / f"{stem}.yaml").write_text(f"type: {stem}\n")

        with use_extensions(
            google_oauth.extension, google_drive.extension, config_root=drive_config_root
        ):
            tools = llm_tool_loader.load_llm_tools(config_root=drive_config_root)

        assert set(tools) == _TOOL_STEMS
