"""Discord コマンドのパッケージ。

コマンドは 1 コマンド 1 ファイルで `src/commands/` 配下に置き、
`@register_command("コマンド名")` でレジストリへ登録する。ここでは
`pkgutil.iter_modules` でパッケージ内の全モジュールを動的に import するため、
コマンドを追加するときにこのファイルへ追記する必要はない。

拡張が `Extension.command_packages()` で返したパッケージも同じ規約で走査する。
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from pathlib import Path

from lilla_core.commands.registry import known_command_names
from lilla_core.core.extension import get_command_packages

logger = logging.getLogger(__name__)


def _load_package_commands(package_name: str) -> None:
    """指定パッケージ配下の全モジュールを import してコマンドを登録する。

    Args:
        package_name: 走査するパッケージの import パス。

    Raises:
        ImportError: パッケージ自体、またはその配下のモジュールを import
            できない場合（起動時に気付けるよう握りつぶさない）。
    """
    package = importlib.import_module(package_name)
    for path in package.__path__:
        for module_info in pkgutil.iter_modules([str(path)]):
            importlib.import_module(f"{package_name}.{module_info.name}")


def load_all_commands() -> list[str]:
    """コア確定のコマンドと、拡張が指定したパッケージのコマンドを登録する。

    起動時に一度だけ呼び出す。何度呼んでも import 済みモジュールは再実行されない
    （＝登録は冪等）。コマンド名が衝突した場合は `register_command` が例外を
    投げるため、ここでは握りつぶさずそのまま伝播させる。

    Returns:
        登録済みのコマンド名の一覧。
    """
    package_dir = Path(__file__).parent
    for module_info in pkgutil.iter_modules([str(package_dir)]):
        importlib.import_module(f"{__name__}.{module_info.name}")

    for package_name in get_command_packages():
        _load_package_commands(package_name)

    names = known_command_names()
    logger.info("Commands loaded: %s", ", ".join(sorted(names)))
    return names
