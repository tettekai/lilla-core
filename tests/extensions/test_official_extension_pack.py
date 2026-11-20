"""公式拡張パック（`lilla_core.extensions`）とコア本体の境界のテスト。"""
from __future__ import annotations

import ast
from pathlib import Path

import lilla_core

_PACKAGE_ROOT = Path(lilla_core.__file__).resolve().parent
_PACK_ROOT = _PACKAGE_ROOT / "extensions"


def _imported_modules(path: Path) -> set[str]:
    """ファイルが import しているモジュール名（`from X import` の X と `import X`）を返す。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names


class TestCoreDoesNotDependOnExtensionPack:
    """コア本体は公式拡張パックを import しない（`LILLA_EXTENSIONS` 未指定なら載らない）。"""

    def test_no_core_module_imports_the_extension_pack(self) -> None:
        """`lilla_core/extensions/` の外のモジュールは `lilla_core.extensions` を参照しない。"""
        offenders = [
            str(path.relative_to(_PACKAGE_ROOT))
            for path in _PACKAGE_ROOT.rglob("*.py")
            if _PACK_ROOT not in path.parents
            and any(
                name == "lilla_core.extensions" or name.startswith("lilla_core.extensions.")
                for name in _imported_modules(path)
            )
        ]

        assert offenders == []


class TestDisplayNames:
    """公式拡張パックと標準機能の拡張は、設定画面に出す表示名を持つ。"""

    def test_each_extension_declares_a_display_name(self) -> None:
        """各拡張の `display_name()` が決めた表示名を返す。"""
        import importlib

        expected = {
            "lilla_core.extensions.notes": "ノート",
            "lilla_core.extensions.diary": "日記",
            "lilla_core.extensions.google_oauth": "Google 認証",
            "lilla_core.extensions.google_calendar": "Google カレンダー",
            "lilla_core.extensions.google_drive": "Google ドライブ",
            "lilla_core.extensions.files": "ファイル",
        }
        for module_path, display_name in expected.items():
            ext = importlib.import_module(module_path).extension
            assert ext.display_name() == display_name, module_path
