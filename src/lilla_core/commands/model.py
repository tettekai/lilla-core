"""`!model [プロバイダー名 | reset]` コマンド。

会話で使用する LLM プロバイダーを、Bot の再起動を伴わずに一時的に切り替える。
状態はプロセス内メモリのみで保持するため、再起動すると `llm.default` に戻る。
引数なしは状態を変えずに一覧と現在のモデルを返し、上書きの解除は `!model reset` で行う。

切り替えが効くのは discord 会話（メンション / DM）と、拡張が対応していれば他クライアントの
会話のみ。`!runtask` や APScheduler 経由の定期タスクは各タスクの YAML 設定どおりのプロバイダーで
動作し、この上書きの影響を受けない。

返信は LLM を介さない固定文言（システムメッセージ）とする。
"""
from __future__ import annotations

import logging

from lilla_core.commands.registry import register_command
from lilla_core.core.config import get_config
from lilla_core.core.runtime_state import (
    get_active_llm_name,
    reset_active_llm_name,
    set_active_llm_name,
)
from lilla_core.ui.messages import t

logger = logging.getLogger(__name__)

# 上書きの解除に使う引数。`core/config.py` の `RESERVED_LLM_PROVIDER_NAMES` に含まれるため、
# 同名のプロバイダーが無いことは起動時に保証される。
RESET_KEYWORD = "reset"


def _available_names(config) -> str:
    """定義済みプロバイダー名を名前順に並べた文字列を返す（無ければ「なし」の文言）。

    Args:
        config: `get_config()` の設定。`llm.providers` を読む。

    Returns:
        カンマ区切りのプロバイダー名。
    """
    return ", ".join(sorted(config.llm.providers)) or t("command.model.none")


@register_command("model")
async def handle_model(message, arg: str, tools: dict, bot) -> None:
    """`!model [プロバイダー名 | reset]` の処理。

    引数なしの場合は状態を変えず、`lilla.yaml` の `llm.providers` に定義された
    プロバイダーの一覧と、今有効なモデル名（上書きが無ければ `llm.default`）を返信する。
    `reset` の場合は上書きを解除して `llm.default` に戻す（`reset` はプロバイダー名に
    使えない予約語で、起動時に検証済み）。それ以外は定義済みのプロバイダーへ切り替え、
    未定義の名前が指定された場合は状態を変更せず、利用可能な名前の一覧を返信する。

    Args:
        message: コマンドを送信した Discord メッセージ。結果の返信に使用する。
        arg: 切り替え先のプロバイダー名、または `reset`（無い場合は空文字列）。
        tools: ツールレジストリ（未使用）。
        bot: Discord クライアント（未使用）。
    """
    config = get_config()
    name = arg.strip()

    if not name:
        current = get_active_llm_name() or config.llm.default
        await message.reply(
            t("command.model.status", current=current, available=_available_names(config))
        )
        return

    if name == RESET_KEYWORD:
        reset_active_llm_name()
        logger.info("[MODEL] Reverted to default model (%s)", config.llm.default)
        await message.reply(t("command.model.reset", model=config.llm.default))
        return

    if name not in config.llm.providers:
        await message.reply(
            t("command.model.not_found", name=name, available=_available_names(config))
        )
        return

    set_active_llm_name(name)
    logger.info("[MODEL] Switched model to %s", name)
    await message.reply(t("command.model.switched", model=name))
