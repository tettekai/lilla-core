"""Discord コマンドのパッケージ。

コマンドは 1 コマンド 1 ファイルで `src/commands/` 配下に置き、
`@register_command("コマンド名")` でレジストリへ登録する。ここでは
`pkgutil.iter_modules` でパッケージ内の全モジュールを動的に import するため、
コマンドを追加するときにこのファイルへ追記する必要はない。
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from pathlib import Path

from lilla_core.commands.registry import known_command_names

logger = logging.getLogger(__name__)


def load_all_commands() -> list[str]:
    """`src/commands/` 配下の全モジュールを import してコマンドを登録する。

    起動時に一度だけ呼び出す。何度呼んでも import 済みモジュールは再実行されない
    （＝登録は冪等）。

    Returns:
        登録済みのコマンド名の一覧。
    """
    package_dir = Path(__file__).parent
    for module_info in pkgutil.iter_modules([str(package_dir)]):
        importlib.import_module(f"{__name__}.{module_info.name}")

    names = known_command_names()
    logger.info("Commands loaded: %s", ", ".join(sorted(names)))
    return names
