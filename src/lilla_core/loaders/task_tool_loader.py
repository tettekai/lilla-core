import logging
from pathlib import Path

import yaml

from lilla_core.core.config import get_config
from lilla_core.loaders.script_loader import find_tool_class, load_script_class
from lilla_core.loaders.tool_paths import (
    find_tool_file,
    import_tool_module,
    is_import_path,
    is_tool_enabled,
    resolve_tool_config_files,
    resolve_tool_roots,
)

logger = logging.getLogger(__name__)

tool_class_map = {}  # type名 -> クラス（キャッシュ）


def _load_tool_configs(config_root: Path) -> list[tuple[str, dict]]:
    """task ツールの YAML を読み込み、(ファイルパス, dict) のリストを返す。

    集める YAML は `tool_paths.resolve_tool_config_files()` に従う（拡張が同梱した
    既定 YAML → `config_root/tools` の順で、同じ stem は後者が丸ごと上書き）。
    `enabled: false` の YAML は含めない。
    """
    results = []
    for config_path in resolve_tool_config_files("task_", config_root):
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        if not is_tool_enabled(config or {}):
            logger.info("Task tool disabled by config: %s", config_path)
            continue
        results.append((str(config_path), config))
    return results


def _resolve_tool_class(
    tool_type: str, tool_roots: list[Path], class_map: dict
) -> type | None:
    """tool_type に対応するクラスを返す。キャッシュ済みなら再利用、なければロード。

    `tool_type` が `.` を含む場合は import パスとみなして `importlib` で読み、
    そのモジュールからツールクラスを探す。そうでなければ従来どおりツールルートを
    ファイル名で探索する。同名のツールファイルが複数のツールルートにある場合は
    `find_tool_file` が例外を投げる（どちらが使われるかを暗黙にしないため）。
    ファイル探索の場合、ロードは探索に使った `tool_roots` の配下に閉じる
    （解決後のパスが外へ出ていればロードしない）。
    """
    if tool_type not in class_map:
        if is_import_path(tool_type):
            module = import_tool_module(tool_type)
            cls = find_tool_class(module) if module is not None else None
        else:
            py_file = find_tool_file(tool_type, tool_roots)
            if py_file is None:
                return None
            cls = load_script_class(py_file, tool_dirs=tool_roots)
        if not cls:
            return None
        class_map[tool_type] = cls
    return class_map.get(tool_type)


def _build_tool_entry(instance, name: str, config: dict) -> dict:
    """インスタンスからツールメタデータ dict を生成する（純粋関数）。

    トリガー種別は YAML のファイル名 stem（＝ツール名）から判定する。`type` から
    導くと、import パス指定（`type: some.pkg.daily_summary`）のツールがどの
    プレフィックスにも当たらず `other` になってしまうため。

    Args:
        instance: 生成済みのツールインスタンス。
        name: YAML のファイル名 stem（ツール名）。
        config: ツールの YAML 設定（現状は未使用）。

    Returns:
        ツールメタデータ dict。
    """
    return {
        "instance": instance,
        "description": getattr(instance, "description", ""),
        "category": getattr(instance, "category", "unknown"),
        "trigger": _get_trigger_from_filename(name),
        "scheduled": bool(getattr(instance, "schedule", None)),
    }


def load_all_tools(
    tool_roots: list[Path] | None = None,
    config_root: Path | None = None,
    class_map: dict | None = None,
) -> dict[str, dict]:
    """全ツールを読み込み、ツール名をキーとする dict を返す。

    `tool_roots` の既定は `paths.tool_root` に拡張の `tool_roots()` を足したもの。
    """
    if tool_roots is None:
        tool_roots = resolve_tool_roots()
    if config_root is None:
        config_root = get_config().env.config_root
    if class_map is None:
        class_map = tool_class_map

    tools = {}
    for config_path, config in _load_tool_configs(config_root):
        tool_type = config.get("type")
        if not tool_type:
            logger.warning("Config file %s has no type. Skipping", config_path)
            continue

        cls = _resolve_tool_class(tool_type, tool_roots, class_map)
        if not cls:
            continue

        config["_yaml_path"] = config_path
        name = Path(config_path).stem
        instance = cls(config, name)
        tools[name] = _build_tool_entry(instance, name, config)
        logger.info("Tool loaded: %s (%s)", name, tool_type)

    return tools


def _get_trigger_from_filename(filename: str) -> str:
    """ファイル名プレフィックスからトリガー種別を返す。"""
    if filename.startswith("task_"):
        return "task"
    if filename.startswith("llm_"):
        return "llm"
    if filename.startswith("system_"):
        return "system"
    return "other"
