"""`lilla_core.extensions.files` の `files` 拡張のテスト。"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from lilla_core.core.config import extension_section_name
from lilla_core.extensions import files

_PACKAGE_DIR = Path(files.__file__).resolve().parent
_TOOL_STEMS = {"llm_file_list", "llm_file_read", "llm_file_write", "llm_file_search"}


class TestFilesExtensionDeclaration:
    """`files` 拡張の申告内容。"""

    def test_name(self) -> None:
        """拡張名は `files` で、他の拡張に依存しない。"""
        assert files.extension.name == "files"
        assert files.extension.requires == ()

    def test_declares_the_files_section(self) -> None:
        """申告するモデルは `FilesConfig` で、キーは `extensions.files`。"""
        assert files.extension.config_model() is files.FilesConfig
        assert extension_section_name(files.extension.name) == "files"

    def test_section_is_optional(self) -> None:
        """必須フィールドを持たないため節そのものを省略できる。"""
        assert not any(f.is_required() for f in files.FilesConfig.model_fields.values())

    def test_tool_roots(self) -> None:
        """同梱の `tools/` を探索ルートへ足し、4 つのツールがある。"""
        (root,) = files.extension.tool_roots()
        assert root == _PACKAGE_DIR / "tools"
        assert {p.stem for p in root.glob("llm_*.py")} == _TOOL_STEMS

    def test_locale_dirs(self) -> None:
        """文言カタログは日英の両方がある。"""
        (locale_dir,) = files.extension.locale_dirs()
        assert {p.name for p in locale_dir.glob("*.yaml")} == {"ja.yaml", "en.yaml"}


class TestFilesConfig:
    """`extensions.files` のモデルの検証。"""

    def test_defaults(self) -> None:
        """既定はホワイトリストが空（すべて拒否）・上限 100,000 文字。"""
        cfg = files.FilesConfig()

        assert cfg.read_only_dirs == []
        assert cfg.writable_dirs == []
        assert cfg.max_read_chars == 100_000

    def test_plain_string_uses_basename(self, tmp_path: Path) -> None:
        """文字列だけで書けばパスの末尾の要素が名前になる（末尾の区切りは無視）。"""
        cfg = files.FilesConfig(read_only_dirs=[f" {tmp_path / 'notes'}/ "])

        assert cfg.read_only_dirs[0].label == "notes"

    def test_explicit_name(self, tmp_path: Path) -> None:
        """`{path, name}` で名前を明示できる。"""
        cfg = files.FilesConfig(writable_dirs=[{"path": str(tmp_path), "name": "memo"}])

        assert cfg.writable_dirs[0].label == "memo"

    @pytest.mark.parametrize("path", ["", "  ", "relative/dir", "smb://server/share"])
    def test_rejects_invalid_paths(self, path: str) -> None:
        """空・相対パス・URL 形式は設定エラーにする。"""
        with pytest.raises(ValidationError):
            files.FilesConfig(read_only_dirs=[path])

    def test_rejects_duplicate_names(self, tmp_path: Path) -> None:
        """名前が重なったら設定エラーにする（`name` で区別させる）。"""
        with pytest.raises(ValidationError, match="duplicate"):
            files.FilesConfig(
                read_only_dirs=[str(tmp_path / "a" / "notes")],
                writable_dirs=[str(tmp_path / "b" / "notes")],
            )

    @pytest.mark.parametrize("name", ["", "a/b", "a\\b", ".."])
    def test_rejects_invalid_names(self, tmp_path: Path, name: str) -> None:
        """名前に区切りを含めたり、空・`..` にしたりはできない。"""
        with pytest.raises(ValidationError):
            files.FilesConfig(read_only_dirs=[{"path": str(tmp_path), "name": name}])

    def test_rejects_root_without_basename(self) -> None:
        """`/` のように末尾の要素が無いパスは `name` を求める。"""
        with pytest.raises(ValidationError, match="name"):
            files.FilesConfig(read_only_dirs=["/"])

    def test_rejects_same_dir_in_both(self, tmp_path: Path) -> None:
        """同じディレクトリを両方に書いたら設定エラーにする。"""
        with pytest.raises(ValidationError, match="both"):
            files.FilesConfig(
                read_only_dirs=[{"path": str(tmp_path), "name": "a"}],
                writable_dirs=[{"path": f"{tmp_path}/", "name": "b"}],
            )

    def test_rejects_unknown_keys(self) -> None:
        """綴り間違いの項目名は黙って無視しない。"""
        with pytest.raises(ValidationError):
            files.FilesConfig(writeable_dirs=["/tmp"])

    def test_rejects_non_positive_max_read_chars(self) -> None:
        """`max_read_chars` は正の整数。"""
        with pytest.raises(ValidationError):
            files.FilesConfig(max_read_chars=0)


class TestComposition:
    """コアの設定合成とツールのロード。"""

    @pytest.fixture
    def config_root(self, tmp_path: Path) -> Path:
        """`extensions.files` だけを持つ最小構成の `lilla.yaml` を書いたディレクトリ。"""
        from lilla_core.testing import write_minimal_lilla_yaml

        (tmp_path / "data").mkdir()
        write_minimal_lilla_yaml(
            tmp_path,
            extra={
                "extensions": {
                    "files": {
                        "read_only_dirs": [str(tmp_path / "data")],
                        "writable_dirs": [{"path": str(tmp_path / "out"), "name": "out"}],
                        "max_read_chars": 500,
                    }
                }
            },
        )
        return tmp_path

    def test_section_is_composed(self, config_root: Path) -> None:
        """`get_section()` で型付きのセクションを取り出せる。"""
        from lilla_core.core.config import get_section
        from lilla_core.testing import use_extensions

        with use_extensions(files.extension, config_root=config_root) as cfg:
            section = get_section("files", files.FilesConfig, cfg)

        assert [d.label for d in section.read_only_dirs] == ["data"]
        assert [d.label for d in section.writable_dirs] == ["out"]
        assert section.max_read_chars == 500

    def test_tools_are_loaded(self, config_root: Path) -> None:
        """利用者の YAML に `type: <ファイル名>` と書けば 4 つのツールがロードされる。"""
        from lilla_core.loaders import llm_tool_loader
        from lilla_core.testing import use_extensions

        tools_dir = config_root / "tools"
        tools_dir.mkdir()
        for stem in _TOOL_STEMS:
            (tools_dir / f"{stem}.yaml").write_text(f"type: {stem}\n")

        with use_extensions(files.extension, config_root=config_root):
            tools = llm_tool_loader.load_llm_tools(config_root=config_root)

        assert set(tools) == _TOOL_STEMS

    def test_locale_catalog_is_valid(self, config_root: Path) -> None:
        """文言カタログが登録時の検証を通り、日英で引ける。"""
        from lilla_core.core.config import get_config
        from lilla_core.testing import use_extensions
        from lilla_core.ui import messages

        with use_extensions(files.extension, config_root=config_root):
            catalogs = {
                locale: messages._load_catalog(locale)
                for locale in ("ja", "en")
            }
            assert get_config().extensions.files.max_read_chars == 500

        for catalog in catalogs.values():
            assert "{tool}" in catalog["files"]["tool"]["unexpected_error"]
