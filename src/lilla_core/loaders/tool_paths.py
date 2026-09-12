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
"""
from __future__ import annotations

from pathlib import Path

from lilla_core.core.config import get_config
from lilla_core.core.extension import get_tool_roots


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
