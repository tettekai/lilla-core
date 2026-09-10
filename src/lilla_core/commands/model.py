"""`!model [プロバイダー名]` コマンド。

会話で使用する LLM プロバイダーを、Bot の再起動を伴わずに一時的に切り替える。
状態はプロセス内メモリのみで保持するため、再起動すると `llm.default` に戻る。

切り替えが効くのは discord 会話（メンション / DM）と、拡張が対応していれば他クライアントの
会話のみ。`!runtask` や APScheduler 経由の定期タスクは各タスクの YAML 設定どおりのプロバイダーで
動作し、この上書きの影響を受けない。

返信は LLM を介さない固定文言（システムメッセージ）とする。
"""
from __future__ import annotations

import logging

from lilla_core.commands.registry import register_command
from lilla_core.core.config import get_config
from lilla_core.core.runtime_state import reset_active_llm_name, set_active_llm_name
from lilla_core.ui.messages import t

logger = logging.getLogger(__name__)


@register_command("model")
async def handle_model(message, arg: str, tools: dict, bot) -> None:
    """`!model [プロバイダー名]` の処理。

    引数ありの場合は `lilla.yaml` の `llm.providers` に定義されたプロバイダーへ
    切り替える。引数なしの場合は上書きを解除して `llm.default` に戻す。
    未定義の名前が指定された場合は状態を変更せず、利用可能な名前の一覧を返信する。

    Args:
        message: コマンドを送信した Discord メッセージ。結果の返信に使用する。
        arg: 切り替え先のプロバイダー名（無い場合は空文字列）。
        tools: ツールレジストリ（未使用）。
        bot: Discord クライアント（未使用）。
    """
    config = get_config()
    name = arg.strip()

    if not name:
        reset_active_llm_name()
        logger.info("[MODEL] Reverted to default model (%s)", config.llm.default)
        await message.reply(t("command.model.reset", model=config.llm.default))
        return

    if name not in config.llm.providers:
        available = ", ".join(sorted(config.llm.providers)) or t("command.model.none")
        await message.reply(t("command.model.not_found", name=name, available=available))
        return

    set_active_llm_name(name)
    logger.info("[MODEL] Switched model to %s", name)
    await message.reply(t("command.model.switched", model=name))
