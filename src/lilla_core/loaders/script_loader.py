import importlib.util
import logging
from collections.abc import Callable
from pathlib import Path

from lilla_core.core.config import get_config

logger = logging.getLogger(__name__)


def _get_allowed_paths() -> list[Path]:
    """ホワイトリストパスのリストを設定から都度読む。

    import 時に固定すると、拡張が `set_config()` で差し替えた設定が
    反映されないため、呼び出しのたびに読む。
    """
    return get_config().paths.allowed_tool_paths_list


def load_script_function(script_path: Path, function_name: str) -> Callable | None:
    """
    指定パスの.pyから関数を安全にロードする。
    ホワイトリストチェック付き。
    """
    resolved_path = script_path.resolve()

    # ホワイトリストチェック
    if not any(resolved_path.is_relative_to(allowed) for allowed in _get_allowed_paths()):
        logger.error("Path is not allowed: %s", resolved_path)
        return None

    if not resolved_path.exists() or resolved_path.suffix != ".py":
        logger.warning("File not found or invalid: %s", resolved_path)
        return None

    module_name = resolved_path.stem
    spec = importlib.util.spec_from_file_location(module_name, str(resolved_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    func = getattr(module, function_name, None)
    if not callable(func):
        logger.warning("Function %s not found: %s", function_name, resolved_path)
        return None

    return func


def load_script_class(
    script_path: Path, class_name: str | None = None
) -> type | None:
    """
    指定パスの.pyからクラスを安全にロードする。
    class_name 指定時はそのクラス、None時は最初のツールっぽいクラスを探す。
    ホワイトリストチェック付き。
    """
    resolved_path = script_path.resolve()

    # ホワイトリストチェック
    if not any(resolved_path.is_relative_to(allowed) for allowed in _get_allowed_paths()):
        logger.error("Path is not allowed: %s", resolved_path)
        return None

    if not resolved_path.exists() or resolved_path.suffix != ".py":
        logger.warning("File not found or invalid: %s", resolved_path)
        return None

    module_name = resolved_path.stem
    spec = importlib.util.spec_from_file_location(module_name, str(resolved_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if class_name:
        cls = getattr(module, class_name, None)
        if not (
            isinstance(cls, type)
            and hasattr(cls, "execute")
            and callable(getattr(cls, "execute"))
        ):
            logger.warning("Class %s not found or invalid: %s", class_name, resolved_path)
            return None
        return cls

    # class_name None時は最初のツールクラスを探す
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if (
            isinstance(attr, type)
            and hasattr(attr, "execute")
            and callable(getattr(attr, "execute"))
        ):
            return attr

    logger.warning("Tool class not found: %s", resolved_path)
    return None
