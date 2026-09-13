"""ツール探索ディレクトリの解決と、ツールファイルの検索を共通化するモジュール。

LLM ツール（`llm_tool_loader`）と task ツール（`task_tool_loader`）は同じ
ルート集合を探索する。ルートは `paths.tool_root` を先頭に、拡張の
`tool_roots()` をロード順で足したもの。

同名のツールファイルが複数のルートにあると、どちらが使われるかが暗黙になる
ため fail-fast する（先頭マッチで静かに採らない）。1 つのルートの中に同名
ファイルが複数ある場合は従来どおり先頭マッチを使う。

追加ルートは **ファイル探索だけ** に使い、`sys.path` へは挿入しない。
拡張のツールが Python パッケージとして import される必要があるなら、
インストール済みパッケージにすること。

YAML の `type` が `.` を含む場合は import パスとみなし、ファイル探索ではなく
`importlib.import_module` で解決する（`import_tool_module`）。インストール済みの
パッケージ（PyPI 配布の拡張など）がツールを同梱するための経路で、モジュールは通常の
import と同じく `sys.modules` に登録され、相対 import も使える。import に失敗した場合は
ファイルが見つからないときと同じく WARNING を出してそのツールだけスキップする。
import できるパッケージは `LILLA_EXTENSIONS` のモジュールと同じ信頼レベルとみなし、
ディレクトリの検査は行わない。

ツールの `.py` をロードしてよいディレクトリは `resolve_tool_dirs()` が返す
「探索ルート + `config_root/tools`（`type: self` の置き場所）」で、これ以外の設定は
持たない。`find_tool_file` は YAML の `type` を `rglob` のパターンとして使うため、
`..` を含む `type` や、ルート内のシンボリックリンクが外を指す場合は解決後のパスが
ルートの外へ出うる。ローダーは読み込む直前に `is_within_tool_dirs()` で解決後の
パスを検査し、外へ出ていればロードしない。これは信頼境界ではなく不変条件の検査で、
`CONFIG_ROOT` と各探索ルートへ書き込める者はコードを実行できる（`SECURITY.md`）。
"""
from __future__ import annotations

import importlib
import logging
from pathlib import Path
from types import ModuleType

from lilla_core.core.config import get_config
from lilla_core.core.extension import get_tool_roots

logger = logging.getLogger(__name__)


def resolve_tool_roots() -> list[Path]:
    """探索対象のツールルートを順序付きで返す。

    `paths.tool_root` が先頭で、そのあとに拡張の `tool_roots()` がロード順で
    続く。同じディレクトリを指す重複は取り除く（解決後のパスで比較する）。

    Returns:
        重複を除いたツールルートのリスト。
    """
    roots: list[Path] = [get_config().paths.tool_root, *get_tool_roots()]
    unique: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        resolved = Path(root).resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(Path(root))
    return unique


def resolve_tool_dirs(
    tool_roots: list[Path] | None = None, config_root: Path | None = None
) -> list[Path]:
    """ツールの `.py` をロードしてよいディレクトリを解決済みのパスで返す。

    探索ルート（`tool_roots`。省略時は `resolve_tool_roots()`）に、`type: self` の
    `.py` が置かれる `config_root/tools`（省略時は設定の `env.config_root`）を足す。

    Args:
        tool_roots: 探索に使ったツールルート。`None` なら設定と拡張から解決する。
        config_root: 設定ルート。`None` なら設定から取る。

    Returns:
        `resolve()` 済みのディレクトリのリスト（順序は探索ルート → `config_root/tools`）。
    """
    if tool_roots is None:
        tool_roots = resolve_tool_roots()
    if config_root is None:
        config_root = get_config().env.config_root
    return [Path(root).resolve() for root in tool_roots] + [
        (Path(config_root) / "tools").resolve()
    ]


def is_within_tool_dirs(path: Path, tool_dirs: list[Path]) -> bool:
    """`path` の解決後の位置が `tool_dirs` のいずれかの配下にあるかを返す。

    `path` と各ディレクトリの両方を `resolve()` してから比較するため、`..` や
    シンボリックリンクで探索ルートの外へ出たパスは `False` になる。

    Args:
        path: 検査するファイルパス。
        tool_dirs: ロードを許すディレクトリ。

    Returns:
        いずれかの配下にあれば `True`。
    """
    resolved = Path(path).resolve()
    return any(resolved.is_relative_to(Path(d).resolve()) for d in tool_dirs)


def is_import_path(tool_type: str) -> bool:
    """YAML の `type` が import パス（ドット区切りのモジュール名）かどうかを返す。

    `.` を 1 つでも含めば import パスとみなす。ファイル名 stem による探索は
    `{type}.py` を探すため、stem に `.` が含まれることは実用上ない。

    Args:
        tool_type: YAML の `type` 値。

    Returns:
        import パスなら `True`。
    """
    return "." in tool_type


def import_tool_module(tool_type: str) -> ModuleType | None:
    """import パス形式の `type` を `importlib.import_module` で解決して返す。

    モジュールが無い、または import 中に例外が出た場合は、ファイルが見つからない
    ときと同じ扱いで WARNING を出して `None` を返す（そのツールだけスキップし、
    起動は止めない）。

    Args:
        tool_type: ドット区切りの import パス（例: `"lilla_google_calendar.tools.calendar_get"`）。

    Returns:
        import したモジュール。失敗時は `None`。
    """
    try:
        return importlib.import_module(tool_type)
    except Exception as e:
        logger.warning("Failed to import tool module '%s': %s", tool_type, e, exc_info=True)
        return None


def find_tool_file(tool_type: str, tool_roots: list[Path]) -> Path | None:
    """各ルートを再帰的に検索し、`{tool_type}.py` にマッチするファイルを返す。

    Args:
        tool_type: YAML の `type` 値（例: `"llm_web_search"`）。
        tool_roots: 探索するルートのリスト（先頭が優先）。

    Returns:
        マッチしたファイルパス。見つからない場合は `None`。

    Raises:
        ValueError: 複数のルートに同名のツールファイルがある場合。
    """
    matches: list[Path] = []
    for root in tool_roots:
        if not root.is_dir():
            continue
        for match in root.rglob(f"{tool_type}.py"):
            matches.append(match)
            break

    if len(matches) > 1:
        raise ValueError(
            f"Tool file '{tool_type}.py' found in multiple tool roots: "
            f"{[str(m) for m in matches]}"
        )
    return matches[0] if matches else None
