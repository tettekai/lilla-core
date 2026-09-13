"""外部 Python 関数・クラスを、ツール探索ディレクトリの配下に限ってロードするモジュール。

ロードしてよいディレクトリは `loaders/tool_paths.py` の `resolve_tool_dirs()` が
設定から導く（探索ルートと `config_root/tools`）。呼び出し側が探索に使った
ルートを `tool_dirs` で渡せば、そのルートに閉じて検査する。
"""
from __future__ import annotations

import importlib.util
import logging
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

logger = logging.getLogger(__name__)


def _default_tool_dirs() -> list[Path]:
    """`tool_dirs` が省略されたときに使う、ツールを置いてよいディレクトリを設定から都度導く。

    import 時に固定すると、拡張のロード後に差し替えられた設定が反映されないため、
    呼び出しのたびに解決する。
    """
    from lilla_core.loaders.tool_paths import resolve_tool_dirs

    return resolve_tool_dirs()


def _load_module(script_path: Path, tool_dirs: list[Path] | None) -> ModuleType | None:
    """`.py` ファイルをモジュールとして読み込んで返す。

    解決後のパスが `tool_dirs`（省略時は `_default_tool_dirs()`）のいずれかの配下に
    無い場合（`..` を含む `type` やシンボリックリンク経由で探索ルートの外へ出た場合）は
    ERROR ログを出して `None` を返す。

    Args:
        script_path: ロードする `.py` ファイルのパス。
        tool_dirs: ロードを許すディレクトリ。`None` なら設定から導く。

    Returns:
        読み込んだモジュール。配下に無い・存在しない・`.py` でない場合は `None`。
    """
    from lilla_core.loaders.tool_paths import is_within_tool_dirs

    resolved_path = script_path.resolve()
    dirs = tool_dirs if tool_dirs is not None else _default_tool_dirs()
    if not is_within_tool_dirs(resolved_path, dirs):
        logger.error(
            "Tool file resolves outside the tool directories: %s (dirs=%s)",
            resolved_path,
            [str(d) for d in dirs],
        )
        return None

    if not resolved_path.exists() or resolved_path.suffix != ".py":
        logger.warning("File not found or invalid: %s", resolved_path)
        return None

    module_name = resolved_path.stem
    spec = importlib.util.spec_from_file_location(module_name, str(resolved_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_script_function(
    script_path: Path, function_name: str, tool_dirs: list[Path] | None = None
) -> Callable | None:
    """指定パスの `.py` から関数をロードする。

    Args:
        script_path: ロードする `.py` ファイルのパス。
        function_name: 取り出す関数名。
        tool_dirs: ロードを許すディレクトリ。`None` なら設定から導く。

    Returns:
        関数。配下に無い・ファイル不正・関数が無い場合は `None`。
    """
    module = _load_module(script_path, tool_dirs)
    if module is None:
        return None

    func = getattr(module, function_name, None)
    if not callable(func):
        logger.warning("Function %s not found: %s", function_name, script_path.resolve())
        return None

    return func


def load_script_class(
    script_path: Path, class_name: str | None = None, tool_dirs: list[Path] | None = None
) -> type | None:
    """指定パスの `.py` からクラスをロードする。

    `class_name` 指定時はそのクラス、`None` 時は最初のツールらしいクラス
    （`execute` メソッドを持つ型）を探す。

    Args:
        script_path: ロードする `.py` ファイルのパス。
        class_name: 取り出すクラス名。`None` なら自動検出する。
        tool_dirs: ロードを許すディレクトリ。`None` なら設定から導く。

    Returns:
        クラス。配下に無い・ファイル不正・クラスが無い場合は `None`。
    """
    module = _load_module(script_path, tool_dirs)
    if module is None:
        return None
    return find_tool_class(module, class_name, str(script_path.resolve()))


def _is_tool_class(obj: object) -> bool:
    """`execute` メソッドを持つ型（ツールクラス）かどうかを返す。"""
    return isinstance(obj, type) and callable(getattr(obj, "execute", None))


def find_tool_class(
    module: ModuleType, class_name: str | None = None, origin: str | None = None
) -> type | None:
    """読み込み済みモジュールからツールクラスを取り出す。

    ファイルから読んだモジュール（`load_script_class`）と、import パスで読んだ
    モジュール（`task_tool_loader`）の両方で共有する。

    Args:
        module: 探索対象のモジュール。
        class_name: 取り出すクラス名。`None` なら `execute` を持つ最初の型を返す。
        origin: ログに出す出所（ファイルパスや import パス）。省略時はモジュール名。

    Returns:
        ツールクラス。見つからない・不正な場合は `None`。
    """
    where = origin or getattr(module, "__name__", repr(module))
    if class_name:
        cls = getattr(module, class_name, None)
        if not _is_tool_class(cls):
            logger.warning("Class %s not found or invalid: %s", class_name, where)
            return None
        return cls

    # class_name None時は最初のツールクラスを探す
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if _is_tool_class(attr):
            return attr

    logger.warning("Tool class not found: %s", where)
    return None
