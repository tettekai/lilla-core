"""Discord bot のエントリポイント。

起動時の配線（設定・コマンド・ツール・コア確定のリポジトリの初期化）と、
Discord イベントの各 handler への委譲のみを行う。

メッセージ処理は `handlers/message_handler.py`、ボタン押下の処理は
`handlers/interaction_handler.py`、承認フローは `handlers/approval_flow.py`
にあり、このモジュールには業務ロジックを置かない。
"""
import asyncio
import importlib
import logging
import os
import sys

import aiohttp
import discord

# 起動時拡張（config の `set_config()` や extension_points への
# 登録など）を動的に import する。環境変数 `LILLA_EXTENSIONS_MODULE` が
# 指定されていればその名前のモジュールを import する（副作用としての登録が
# 走る）。未指定なら何もしない（拡張を持たないコア単体起動を許容する）。
# 指定されているのに import できない場合はここで例外が伝播し、起動が失敗
# する（fail-fast）。extensions の import は、拡張の登録
# （`set_config` / `register_startup_repo` / `register_message_hook` /
# `register_startup_task`）を有効にするため、必ずコアの他の初期化処理より
# 前に完了させる。
_extensions_module = os.environ.get("LILLA_EXTENSIONS_MODULE")
if _extensions_module:
    importlib.import_module(_extensions_module)

from lilla_core.bot_client import bot
from lilla_core.core.config import get_config
from lilla_core.core.extension_points import (
    get_extra_startup_repos,
    get_message_hook,
    get_startup_tasks,
)
from lilla_core.core.logging_setup import setup_logging
from lilla_core.commands import load_all_commands
from lilla_core.loaders.task_tool_loader import load_all_tools
from lilla_core.loaders.llm_tool_loader import get_llm_tools
from lilla_core.repository.conversation_repository import get_conversation_repo
from lilla_core.repository.user_memo_repository import get_user_memo_repo
from lilla_core.repository.tool_cache_repository import get_tool_cache_repo
from lilla_core.repository.button_actions_repository import get_button_actions_repo
from lilla_core.repository.pending_tool_calls_repository import get_pending_tool_calls_repo
from lilla_core.repository.admin_credential_repository import get_admin_credential_repo
from lilla_core.repository.admin_session_repository import get_admin_session_repo
from lilla_core.handlers import message_handler, interaction_handler, task_handler

setup_logging()

logger = logging.getLogger(__name__)

# 以下の import は、各モジュールが import 時に出すログを取りこぼさないよう
# setup_logging() の後に行う（先頭の import ブロックへまとめないこと）。

_config = get_config()

# on_ready で初期化するコア確定リポジトリのファクトリ一覧（明示リスト）。
_CORE_STARTUP_REPOS = [
    get_conversation_repo,
    get_user_memo_repo,
    get_tool_cache_repo,
    get_button_actions_repo,
    get_pending_tool_calls_repo,
    get_admin_credential_repo,
    get_admin_session_repo,
]

bot.http.proxy = _config.proxy.resolve_url()
if _config.env.http_proxy_user and _config.env.http_proxy_pass:
    bot.http.proxy_auth = aiohttp.BasicAuth(_config.env.http_proxy_user, _config.env.http_proxy_pass)

# コマンド・ツールをロード（起動時に一度だけ）
load_all_commands()
tools = load_all_tools()
llm_tools = get_llm_tools()


@bot.event
async def on_ready():
    """Discord 接続完了時に、各リポジトリの初期化と定期タスクスケジューラの起動を行う。

    追加リポジトリは呼び出し時点の `get_extra_startup_repos()` から取得する
    （モジュールレベルで固定しない＝テストで登録を差し替えやすくする）。
    """
    logger.info("Logged in as %s (Lilla ready)", bot.user)
    for factory in _CORE_STARTUP_REPOS + get_extra_startup_repos():
        repo = factory()
        try:
            await repo.init_collection()
        except Exception as e:
            logger.error(
                "Failed to initialize repository: %s: %s",
                repo.__class__.__name__, e, exc_info=True,
            )
    task_handler.start_scheduler(tools, bot, llm_tools)


@bot.event
async def on_message(message):
    """メッセージ受信イベントを message_handler へ委譲する。

    メッセージフックは呼び出し時点の `get_message_hook()` から取得する
    （未登録時はデフォルトの「常に False」フックが返る）。
    """
    await message_handler.handle_message(message, bot, tools, llm_tools, get_message_hook())


@bot.event
async def on_interaction(interaction: discord.Interaction):
    """インタラクション（ボタン押下）イベントを interaction_handler へ委譲する。"""
    await interaction_handler.handle_interaction(interaction, bot, tools, llm_tools)


# 起動
async def main():
    """拡張が登録した追加起動処理（HTTP/ダッシュボードサーバー等）を
    実行し、Discord へ接続する。

    起動タスクは呼び出し時点の `get_startup_tasks()` から取得する
    （モジュールレベルで固定しない）。

    `bot.start()` が `PrivilegedIntentsRequired`（Developer Portal で
    MESSAGE CONTENT INTENT が未有効化）を送出した場合は、原因と対処法を
    ERROR ログへ出力してからプロセスを終了する（スタックトレースは
    `exc_info=True` で残す）。それ以外の例外は従来通りそのまま伝播させる。
    """
    for task in get_startup_tasks():
        await task(tools, llm_tools, bot)
    try:
        await bot.start(_config.env.discord_token)
    except discord.errors.PrivilegedIntentsRequired:
        logger.error(
            "MESSAGE CONTENT INTENT is not enabled in the Discord Developer Portal. "
            "Turn it on under Privileged Gateway Intents on the Bot page. "
            "See the README setup section.",
            exc_info=True,
        )
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
