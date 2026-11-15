"""`lilla_core/extensions/diary/locales/` の UI 文言カタログのテスト。

コアの `Extension.locale_dirs()` 契約（トップレベルのキーが拡張名ただ 1 つ）と、
`ja.yaml` / `en.yaml` を対に保つこと、登録後に `t()` から解決できることを検証する
（ホスト側カタログの同等のテストは `tests/test_locales.py`）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from lilla_core.testing import use_extensions
from lilla_core.ui import messages

from lilla_core.extensions import diary

#: カタログの値に含まれる `{name}` 形式のプレースホルダ。
_PLACEHOLDER_RE = re.compile(r"{(\w+)}")


def _flatten(catalog: dict, prefix: str = "") -> dict[str, str]:
    """ネストしたカタログを `{"a.b": "文言"}` の平坦な辞書にする。

    Args:
        catalog: カタログの辞書。
        prefix: 再帰で積み上げるキーの接頭辞。

    Returns:
        ドット区切りのキーから文言への平坦な辞書。
    """
    flat: dict[str, str] = {}
    for key, value in catalog.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{path}."))
        else:
            flat[path] = value
    return flat


@pytest.fixture
def locale_dir() -> Path:
    """拡張が申告しているカタログディレクトリを返す。"""
    dirs = diary.extension.locale_dirs()
    assert len(dirs) == 1
    return dirs[0]


@pytest.fixture
def registered(tmp_path: Path):
    """拡張を登録して `t()` が日記のカタログを解決できる状態にする。

    `DiaryExtension` は他拡張へ依存しないため単独で登録できる。`use_extensions()`
    は設定も組み直すため、拡張の節を持たない最小構成の config_root を別に用意する。
    """
    from lilla_core.testing import write_minimal_lilla_yaml

    write_minimal_lilla_yaml(tmp_path)
    with use_extensions(diary.extension, config_root=tmp_path):
        yield


class TestDiaryLocaleCatalogs:
    """カタログファイルそのものの検証。"""

    def test_directory_exists(self, locale_dir: Path) -> None:
        """申告したディレクトリが実在する。"""
        assert locale_dir.is_dir()

    def test_ships_ja_and_en(self, locale_dir: Path) -> None:
        """`ja.yaml` と `en.yaml` の両方を同梱している。"""
        assert {path.stem for path in locale_dir.glob("*.yaml")} == {"ja", "en"}

    def test_top_level_key_is_the_extension_name(self, locale_dir: Path) -> None:
        """トップレベルのキーは拡張名（`diary`）ただ 1 つ（コアの名前空間規約）。"""
        for path in sorted(locale_dir.glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            assert list(data) == [diary.extension.name], (
                f"unexpected top-level key in {path.name}"
            )

    def test_locales_have_the_same_keys(self, locale_dir: Path) -> None:
        """`ja.yaml` と `en.yaml` は同じキー集合を持つ（片方だけに足していない）。"""
        catalogs = {
            path.stem: _flatten(yaml.safe_load(path.read_text(encoding="utf-8")))
            for path in sorted(locale_dir.glob("*.yaml"))
        }
        base = set(catalogs["ja"])
        for locale, flat in catalogs.items():
            assert set(flat) == base, f"key mismatch in {locale}.yaml"

    def test_locales_have_the_same_placeholders(self, locale_dir: Path) -> None:
        """同じキーの文言は、どのカタログでも同じプレースホルダを持つ。"""
        catalogs = {
            path.stem: _flatten(yaml.safe_load(path.read_text(encoding="utf-8")))
            for path in sorted(locale_dir.glob("*.yaml"))
        }
        base = catalogs["ja"]
        for locale, flat in catalogs.items():
            for key, text in flat.items():
                assert set(_PLACEHOLDER_RE.findall(text)) == set(
                    _PLACEHOLDER_RE.findall(base[key])
                ), f"placeholder mismatch: {locale}.yaml {key}"

    def test_all_values_are_strings(self, locale_dir: Path) -> None:
        """カタログの葉はすべて文字列（辞書やリストを置かない）。"""
        for path in sorted(locale_dir.glob("*.yaml")):
            flat = _flatten(yaml.safe_load(path.read_text(encoding="utf-8")))
            for key, value in flat.items():
                assert isinstance(value, str), f"{path.name} {key} is not a string"


class TestDiaryResolution:
    """登録後に `t()` から解決できることの検証。"""

    def test_resolves_every_key(self, registered, locale_dir: Path) -> None:
        """同梱カタログの全キーがキー名そのものではなく文言として解決される。"""
        data = yaml.safe_load((locale_dir / "ja.yaml").read_text(encoding="utf-8"))
        for key in _flatten({diary.extension.name: data[diary.extension.name]}):
            assert messages.t(key) != key, f"unresolved: {key}"

    def test_fills_placeholders(self, registered) -> None:
        """プレースホルダに渡した値が埋まる。"""
        text = messages.t("diary.faircopy.saved", date="2026-09-27", storage="notes", path="d.md")
        assert "2026-09-27" in text and "notes" in text and "d.md" in text
