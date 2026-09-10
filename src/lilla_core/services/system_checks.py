"""生存確認（liveness）と自己診断（self-test）で共有するチェック関数群。

目的の異なる 2 つの入口から同じチェックを再利用するためのモジュール。

- 生存確認（liveness）:
  拡張側の HTTP ヘルスチェックエンドポイントなどから利用される想定で、
  プロセスが応答しているかどうかだけを見る。Docker / systemd などが
  「再起動すべきか」を判断する材料になるため、再起動で直らない問題は混ぜない
  （＝ MongoDB 疎通のみを見る）
- 自己診断（`!selftest` = `commands/selftest.py`）:
  起動後やパッケージ構成の変更後に手動で走らせ、リラの機能が壊れていないかを
  人間が確認するための検査。失敗してもコンテナを落とす必要はない

`health` を名乗らない中立な名前にしているのは、拡張側が持つ他の
ヘルスチェック関連モジュールと紛らわしくなるのを避けるため。

各チェック関数は内部で例外を捕捉し、失敗時も例外を投げずに `ok=False` の
`CheckResult` を返す（呼び出し側がチェックごとに try/except を書かずに済み、
1 件の失敗が他のチェックの実行を妨げない）。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from lilla_core.ui.messages import t

logger = logging.getLogger(__name__)

#: MongoDB 疎通確認のタイムアウト秒数。
#: 同一 Docker network 内の MongoDB を前提とするため正常時はミリ秒単位で応答する。
#: 3 秒は十分な余裕を持たせた上限であり、呼び出し側（`handle_root` / `!selftest`）が
#: 意識しなくてもこれ以上は待たされない。
MONGO_TIMEOUT_SECONDS = 3

#: LLM 疎通確認のうち「LLM 往復」部分のタイムアウト秒数。
#: システムプロンプトの組み立て・トークン計測はこのタイムアウトの対象外。
LLM_TIMEOUT_SECONDS = 30

#: LLM 疎通確認で送る user 発言。コスト・レイテンシを抑えるため最小限にする。
LLM_PING_MESSAGE = "ping"

#: トークン数概算に使う tiktoken のエンコーディング名
#: （`tools/schedule/task_daily_health_check.py` と同じ方式）。
_TIKTOKEN_ENCODING = "cl100k_base"


@dataclass
class CheckResult:
    """個別チェックの結果。

    Attributes:
        name: チェック名（例: "mongodb"）。
        ok: チェックが成功したかどうか。
        detail: 人間向けの補足情報。失敗時は短いエラー内容を入れる。
        elapsed_ms: チェックに要した時間（ミリ秒）。
    """

    name: str
    ok: bool
    detail: str
    elapsed_ms: float


def _elapsed_ms(start: float) -> float:
    """`time.perf_counter()` の開始値からの経過時間をミリ秒（小数第 1 位）で返す。

    Args:
        start: 計測開始時点の `time.perf_counter()` の値。

    Returns:
        経過時間（ミリ秒）。
    """
    return round((time.perf_counter() - start) * 1000, 1)


async def check_process_alive() -> CheckResult:
    """プロセスが応答していることを示すダミーのチェック結果を返す。

    ここまで到達できている時点で Bot のプロセスは生きているため、常に成功する。
    `!selftest` 専用のチェックで、`GET /` からは呼ばない
    （HTTP ハンドラに到達している時点で生存は自明なため）。

    Returns:
        常に `ok=True` の CheckResult。
    """
    start = time.perf_counter()
    return CheckResult(
        name="process_alive",
        ok=True,
        detail=t("selftest.check.process_alive"),
        elapsed_ms=_elapsed_ms(start),
    )


async def check_mongodb() -> CheckResult:
    """MongoDB への疎通を確認する（`admin.command("ping")`）。

    `MONGO_TIMEOUT_SECONDS` 秒のタイムアウトを内部に持つため、呼び出し側が
    意識しなくてもこれ以上は待たされない。

    Returns:
        疎通できたかどうかを表す CheckResult。失敗・タイムアウトでも例外は投げない。
    """
    from lilla_core.core.config import get_config
    from lilla_core.repository.motor_client import create_motor_client

    start = time.perf_counter()
    try:
        config = get_config()
        client = create_motor_client(config.env.mongodb_uri)
        await asyncio.wait_for(
            client.admin.command("ping"), timeout=MONGO_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        return CheckResult(
            name="mongodb",
            ok=False,
            detail=t("selftest.check.mongodb_timeout", seconds=MONGO_TIMEOUT_SECONDS),
            elapsed_ms=_elapsed_ms(start),
        )
    except Exception as e:
        return CheckResult(
            name="mongodb",
            ok=False,
            detail=f"{type(e).__name__}: {e}",
            elapsed_ms=_elapsed_ms(start),
        )

    return CheckResult(
        name="mongodb",
        ok=True,
        detail=t("selftest.check.mongodb_ok"),
        elapsed_ms=_elapsed_ms(start),
    )


async def check_command_registry() -> CheckResult:
    """コマンドレジストリに 1 件以上のコマンドが登録されているかを確認する。

    Returns:
        登録件数を detail に持つ CheckResult。0 件なら `ok=False`。
    """
    from lilla_core.commands.registry import known_command_names

    start = time.perf_counter()
    try:
        names = known_command_names()
    except Exception as e:
        return CheckResult(
            name="command_registry",
            ok=False,
            detail=f"{type(e).__name__}: {e}",
            elapsed_ms=_elapsed_ms(start),
        )

    count = len(names)
    return CheckResult(
        name="command_registry",
        ok=count > 0,
        detail=t("selftest.check.command_registry_count", count=count),
        elapsed_ms=_elapsed_ms(start),
    )


async def check_task_tools(tools: dict) -> CheckResult:
    """task ツールが 1 件以上読み込まれているかを確認する。

    Args:
        tools: task ツールのレジストリ（`bot.py` が読み込んだもの）。

    Returns:
        件数を detail に持つ CheckResult。0 件・None なら `ok=False`。
    """
    start = time.perf_counter()
    try:
        count = len(tools or {})
    except Exception as e:
        return CheckResult(
            name="task_tools",
            ok=False,
            detail=f"{type(e).__name__}: {e}",
            elapsed_ms=_elapsed_ms(start),
        )

    return CheckResult(
        name="task_tools",
        ok=count > 0,
        detail=t("selftest.check.task_tools_count", count=count),
        elapsed_ms=_elapsed_ms(start),
    )


async def check_llm() -> CheckResult:
    """本番と同じ経路でシステムプロンプトを組み立て、実際に使われている
    LLM プロバイダーへ短い疎通確認を送る（`!selftest full` 専用）。

    タイミングの構成:

    1. システムプロンプトの組み立てとトークン計測を行う（タイムアウト対象外）。
       ここで例外が出た場合はその時点で `ok=False` を返す
       （＝プロンプト組み立てロジック自体の regression を検知できる）
    2. その後の LLM 往復のみを `LLM_TIMEOUT_SECONDS` 秒でタイムアウトさせる。
       タイムアウトしても計測済みの `system_prompt_tokens` は detail に残す

    プロバイダーは `core.runtime_state.get_active_llm_name()` を使い、特定の
    プロバイダーには固定しない（`!model` で切り替え中でも「今実際に使われている
    プロバイダー」をそのまま検証する）。detail に書く名前は `None` が表示されて
    分かりにくくならないよう `llm.default` で解決した後の名前を使う。

    detail にはシステムプロンプト本文を含めない。`build_system_prompt` は
    ユーザーメモやツールキャッシュなど私的な内容を含み得るため、`!selftest` の
    添付ファイルに全文が残らないようにする（中身の確認はサーバーログで行う）。

    `chat_to_llm` はそもそも会話履歴に保存しない関数であり、このチェックでも
    その前提のまま呼び出す（MongoDB への書き込みは発生しない）。

    Returns:
        疎通できたかどうかを表す CheckResult。失敗・タイムアウトでも例外は投げない。
    """
    import tiktoken

    from lilla_core.api.llm_client import chat_to_llm
    from lilla_core.core.config import get_config
    from lilla_core.core.runtime_state import get_active_llm_name
    from lilla_core.services.memory_manager import get_memory_manager

    start = time.perf_counter()
    llm_name = get_active_llm_name()

    try:
        config = get_config()
        resolved_name = llm_name or config.llm.default
    except Exception as e:
        return CheckResult(
            name="llm",
            ok=False,
            detail=t("selftest.check.llm_config_error", error=f"{type(e).__name__}: {e}"),
            elapsed_ms=_elapsed_ms(start),
        )

    # 1. システムプロンプトの組み立てとトークン計測（タイムアウト対象外）
    try:
        memory_manager = get_memory_manager()
        system_prompt = await memory_manager.build_system_prompt(
            extra_prompt=config.conversation_prompt, client_type="discord"
        )
        enc = tiktoken.get_encoding(_TIKTOKEN_ENCODING)
        token_count = len(enc.encode(system_prompt))
    except Exception as e:
        return CheckResult(
            name="llm",
            ok=False,
            detail=t(
                "selftest.check.llm_prompt_error",
                info=f"provider={resolved_name}",
                error=f"{type(e).__name__}: {e}",
            ),
            elapsed_ms=_elapsed_ms(start),
        )

    # system_prompt 本文は detail に載せない（私的な内容を含み得るため）
    prompt_info = f"provider={resolved_name} system_prompt_tokens={token_count}"

    # 2. LLM 往復のみタイムアウトの対象にする
    try:
        await asyncio.wait_for(
            chat_to_llm(
                LLM_PING_MESSAGE,
                system_prompt=system_prompt,
                llm_name=llm_name,
            ),
            timeout=LLM_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        return CheckResult(
            name="llm",
            ok=False,
            detail=t("selftest.check.llm_timeout", info=prompt_info, seconds=LLM_TIMEOUT_SECONDS),
            elapsed_ms=_elapsed_ms(start),
        )
    except Exception as e:
        return CheckResult(
            name="llm",
            ok=False,
            detail=t(
                "selftest.check.llm_request_error",
                info=prompt_info,
                error=f"{type(e).__name__}: {e}",
            ),
            elapsed_ms=_elapsed_ms(start),
        )

    return CheckResult(
        name="llm",
        ok=True,
        detail=t("selftest.check.llm_ok", info=prompt_info),
        elapsed_ms=_elapsed_ms(start),
    )
