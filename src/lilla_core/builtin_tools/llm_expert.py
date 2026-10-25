"""専門家エージェント委譲ツール。

YAML 設定（`config/tools/llm_*.yaml`、`type: llm_expert`）から複数の
Expert インスタンスを生成し、それぞれが独自のシステムプロンプトと
ツールセットを持つサブエージェントとして振る舞う。

YAML 側で以下を指定する:
- `prompt`: システムプロンプトの source spec（`dir:` / `file:` プレフィックス、`${config_root}` 展開対応）
- `llm_provider`: 使用する LLM プロバイダー名（省略可）
- `available_tools`: Expert が利用可能な LLM ツール名（YAML stem）のリスト。
  `$` + セット名（`$main` など）と書くと `tools.sets` の同名セットに展開される。展開と存在確認は
  コアの `resolve_available_tools` が行い、Expert 側では解釈しない
- `description`: メインの LLM に提示するツール説明（ローダで上書きされる）
- `api`: 使用する API（`chat_completions`（デフォルト） | `responses`）
- `grok_tools`: Responses API 経由で使う組み込みツール定義のリスト
  （`api: responses` のときのみ有効）

`api` と `available_tools` / `grok_tools` の対応:
- `api: chat_completions`（デフォルト） + `available_tools`: 既存の function calling ループ
- `api: chat_completions` + `grok_tools`: 非対応（エラー）
- `api: responses` + `available_tools`: 非対応（エラー、将来対応）
- `api: responses` + `grok_tools`（有無問わず）: Responses API 単発呼び出し
"""
from __future__ import annotations

import logging

from lilla_core.core.config import get_config
from lilla_core.api.llm_client import chat_to_llm_responses
from lilla_core.loaders.llm_tool_loader import (
    build_tools_param,
    resolve_available_tools,
)
from lilla_core.services.tool_loop import run_tool_loop
from lilla_core.tool_support.tool_result import tool_error, tool_success
from lilla_core.ui.messages import t
from lilla_core.utils.resource_loader import load_text_resources

logger = logging.getLogger(__name__)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "llm_expert",
        "description": (
            "専門家エージェントに処理を委譲する汎用ツール。"
            "YAML設定でdescriptionが上書きされる想定。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": (
                        "専門家への依頼内容。"
                        "回答に必要な背景情報（直近のやりとりの要約など）も"
                        "あわせて記述すること。"
                    ),
                },
            },
            "required": ["prompt"],
        },
    },
}


async def execute(input: dict, context: dict) -> dict:
    """専門家エージェントに処理を委譲する。

    context には YAML 設定（`prompt`, `llm_provider`, `available_tools`）と
    実行時コンテキスト（`llm_tools`, `client_type` など）がマージされて渡される。

    Parameters
    ----------
    input : dict
        `{"prompt": "依頼内容"}` の形式。
    context : dict
        ツール実行コンテキスト。

    Returns
    -------
    dict
        標準のツール戻り値。Expert 内で `needs_auth` が発生した場合は
        即座にループを中断して伝播させる。ループ上限到達時はエラーにせず
        `success: True` で、ツールなしで書かせたここまでのまとめを返す
        （`llm.on_tool_call_limit: message` のとき、またはまとめを得られなかったときは
        中断メッセージ）。
    """
    tool_name = "llm_expert"

    logger.info("Executing LLM Expert with input: %s", input)

    prompt_dir_raw = context.get("prompt")
    if not prompt_dir_raw:
        return tool_error(
            tool_name, None, "Expert の prompt ディレクトリが設定されていません"
        )

    system_prompt = load_text_resources(prompt_dir_raw)

    llm_tools = context.get("llm_tools", {})
    available_tools = context.get("available_tools", [])
    client_type = context.get("client_type", "discord")
    llm_name = context.get("llm_provider")
    api = context.get("api", "chat_completions")
    grok_tools = context.get("grok_tools")

    messages: list[dict] = [{"role": "user", "content": input["prompt"]}]

    if api == "responses":
        if available_tools:
            raise ValueError(
                f"{tool_name}: The combination of api=responses + available_tools is not supported. "
                "The function_call round-trip loop is not implemented; only grok_tools alone can be used for now."
            )
        result = await chat_to_llm_responses(
            messages,
            system_prompt=system_prompt,
            tools=grok_tools,
            llm_name=llm_name,
        )
        return tool_success(tool_name, None, result["content"] or "")

    if api != "chat_completions":
        raise ValueError(f"{tool_name}: Unknown api specified: {api!r}")

    if grok_tools:
        raise ValueError(
            f"{tool_name}: grok_tools cannot be used with api=chat_completions. "
            "Specify api: responses to use grok_tools."
        )

    # 存在しない stem・未知のトークンはコアが ValueError で落とす（握りつぶさない）
    allowed_names = resolve_available_tools(available_tools, llm_tools)
    tools_param = build_tools_param(
        llm_tools,
        client_type=client_type,
        allowed_names=allowed_names,
    )

    # ループは会話と共通の部品を使う。プロバイダーは YAML の指定に固定し、resolver の
    # 再判定も `final_reply_provider` による書き直しも行わない（どちらも会話の経路だけ）。
    # 上限に達したときのまとめは共通部品が会話と同じ規則で書かせるが、書き手は専門家の
    # プロバイダーのまま（専門家の応答はユーザーへの最終応答ではないため、
    # `final_reply_provider` は見ない）
    loop_result = await run_tool_loop(
        messages,
        system_prompt=system_prompt,
        tools=tools_param,
        llm_tools=llm_tools,
        context=context,
        max_iterations=get_config().llm.max_tool_call_iterations,
        llm_name=llm_name,
        stop_on_needs_auth=True,
        summarize_with_final_reply_provider=False,
    )

    if loop_result.stopped_on_needs_auth:
        logger.info(
            "Aborting because needs_auth was detected inside Expert: %s",
            loop_result.needs_auth_list,
        )
        return {
            "success": False,
            "tool_name": tool_name,
            "memory_entry": None,
            "needs_auth": True,
            "needs_auth_list": loop_result.needs_auth_list,
            "data": None,
            "error": "認証が必要です",
        }

    # 上限に達しても、ツールループがツールなしのまとめを書かせていればそれを返す
    # （`llm.on_tool_call_limit: message`、またはまとめを得られなければ固定文言）
    if loop_result.limit_reached:
        logger.warning("Expert tool call limit reached")
        if loop_result.content is None:
            return tool_success(tool_name, None, t("tool.llm_expert.tool_call_limit"))

    return tool_success(tool_name, None, loop_result.content or "")
