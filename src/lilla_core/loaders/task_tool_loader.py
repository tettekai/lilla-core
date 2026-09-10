import glob
import logging
from pathlib import Path

import yaml

from lilla_core.core.config import get_config
from lilla_core.loaders.script_loader import load_script_class  # 共通ローダー使用（クラス版）

logger = logging.getLogger(__name__)

_config = get_config()
TOOL_ROOT = _config.paths.tool_root
CONFIG_ROOT = _config.env.config_root

tool_class_map = {}  # type名 -> クラス（キャッシュ）


def _load_tool_configs(config_root: Path) -> list[tuple[str, dict]]:
    """config_root/tools/task_*.yaml を読み込み、(ファイルパス, dict) のリストを返す。"""
    tool_config_root = config_root / "tools"
    results = []
    for config_path in glob.glob(str(tool_config_root / "task_*.yaml")):
        with open(config_path, "r", encoding="utf-8") as f:
            results.append((config_path, yaml.safe_load(f)))
    return results


def _resolve_tool_class(
    tool_type: str, tool_root: Path, class_map: dict
) -> type | None:
    """tool_type に対応するクラスを返す。キャッシュ済みなら再利用、なければロード。"""
    if tool_type not in class_map:
        matches = list(tool_root.rglob(f"{tool_type}.py"))
        if not matches:
            return None
        py_file = matches[0]
        cls = load_script_class(py_file)
        if not cls:
            return None
        class_map[tool_type] = cls
    return class_map.get(tool_type)


def _build_tool_entry(instance, tool_type: str, config: dict) -> dict:
    """インスタンスからツールメタデータ dict を生成する（純粋関数）。"""
    return {
        "instance": instance,
        "description": getattr(instance, "description", ""),
        "category": getattr(instance, "category", "unknown"),
        "trigger": _get_trigger_from_filename(tool_type),
        "scheduled": bool(getattr(instance, "schedule", None)),
    }


def load_all_tools(
    tool_root: Path | None = None,
    config_root: Path | None = None,
    class_map: dict | None = None,
) -> dict[str, dict]:
    """全ツールを読み込み、ツール名をキーとする dict を返す。"""
    if tool_root is None:
        tool_root = TOOL_ROOT
    if config_root is None:
        config_root = CONFIG_ROOT
    if class_map is None:
        class_map = tool_class_map

    tools = {}
    for config_path, config in _load_tool_configs(config_root):
        tool_type = config.get("type")
        if not tool_type:
            logger.warning("Config file %s has no type. Skipping", config_path)
            continue

        cls = _resolve_tool_class(tool_type, tool_root, class_map)
        if not cls:
            continue

        config["_yaml_path"] = config_path
        name = Path(config_path).stem
        instance = cls(config, name)
        tools[name] = _build_tool_entry(instance, tool_type, config)
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
