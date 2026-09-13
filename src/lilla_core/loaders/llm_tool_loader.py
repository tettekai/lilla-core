"""LLM ツールローダー。

config/tools/llm_*.yaml を読み込み、対応する tools/**/llm_*.py を動的にロードする。
LLM に渡す tools パラメータの構築と、tool_call の実行を担う。
"""
from __future__ import annotations

import glob
import importlib.util
import json
import logging
import sys
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from lilla_core.core.config import get_config
from lilla_core.core.extension import get_tool_context_providers
from lilla_core.loaders.tool_paths import (
    find_tool_file,
    import_tool_module,
    is_import_path,
    is_within_tool_dirs,
    resolve_tool_dirs,
    resolve_tool_roots,
)

logger = logging.getLogger(__name__)

_TOOL_CALL_DEPTH_KEY = "_tool_call_depth"
_TOOL_CALL_NOTIFIER_KEY = "_tool_call_notifier"
MAX_TOOL_CALL_DEPTH = 5  # 将来的に core.config 側で設定可能にしてもよい（今回は固定値でOK）

# コア自身が実行時にツールコンテキストへ注入する共通キー（フレームワーク側の枠）。
# 拡張が注入するキーはここに列挙せず、`get_tool_context_providers()` が返す
# 登録内容から `_validate_no_runtime_key_collision()` が都度導出する。
# なお _tool_call_depth と call_tool は各階層で必ず作り直されるため含めない。
_CORE_RUNTIME_CONTEXT_KEYS = frozenset({
    "client_type",
    "llm_tools",
    "client_state",
    "discord_channel_id",
    _TOOL_CALL_NOTIFIER_KEY,
})

def _ensure_tool_root_on_sys_path() -> None:
    """`tools.shared` などツール間共通パッケージを import できるようにする。

    `paths.tool_root` の親だけを `sys.path` へ入れる。拡張の `tool_roots()` は
    ファイル探索専用で、`sys.path` へは足さない。
    """
    parent = str(get_config().paths.tool_root.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)


def _load_llm_module(script_path: Path, tool_dirs: list[Path]):
    """Python モジュールをロードして返す。

    解決後のパスが `tool_dirs`（探索ルートと `config_root/tools`）のいずれかの
    配下に無い場合（`..` を含む `type` やシンボリックリンク経由で外へ出た場合）は
    ERROR ログを出してロードしない。

    Parameters
    ----------
    script_path : Path
        ロードする .py ファイルのパス
    tool_dirs : list[Path]
        ロードを許すディレクトリ（`resolve_tool_dirs()` の戻り値）

    Returns
    -------
    module | None
        ロード済みモジュール。配下に無い、またはファイル不正の場合は None。
    """
    resolved_path = script_path.resolve()

    if not is_within_tool_dirs(resolved_path, tool_dirs):
        logger.error(
            "Tool file resolves outside the tool directories: %s (dirs=%s)",
            resolved_path,
            [str(d) for d in tool_dirs],
        )
        return None

    if not resolved_path.exists() or resolved_path.suffix != ".py":
        logger.warning("File not found or invalid: %s", resolved_path)
        return None

    module_name = resolved_path.stem
    spec = importlib.util.spec_from_file_location(module_name, str(resolved_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resolve_self_tool_file(config_path: Path) -> Path | None:
    """type: self 用に、YAMLと同じディレクトリ・同じstemの.pyを返す。

    Parameters
    ----------
    config_path : Path
        YAML設定ファイルのパス

    Returns
    -------
    Path | None
        同名の.pyが存在すればそのパス、存在しなければ None。
    """
    py_file = config_path.with_suffix(".py")
    return py_file if py_file.exists() else None


def load_llm_tools(
    tool_roots: list[Path] | None = None,
    config_root: Path | None = None,
) -> dict[str, dict]:
    """config/tools/llm_*.yaml を読み込み、対応するツールをロードして返す。

    各 YAML の type をもとに tools/**/llm_*.py を探してロードする。
    YAML に description がある場合は SCHEMA["description"] を上書きする。

    YAML の type が ``self`` の場合は tool_root 配下の検索を行わず、
    YAML と同じディレクトリ・同名の .py をそのままツール本体としてロードする。
    type が ``.`` を含む場合は import パスとみなし、``importlib`` で解決する
    （インストール済みパッケージが同梱するツール向け。ファイル探索も
    ディレクトリの検査も行わない）。

    Parameters
    ----------
    tool_roots : list[Path], optional
        ツール探索ルートのリスト（デフォルト: `paths.tool_root` + 拡張の `tool_roots()`）
    config_root : Path, optional
        設定ルートディレクトリ（デフォルト: 設定値）

    Returns
    -------
    dict
        { "yaml_stem": { "schema": {...}, "execute": <coroutine func> }, ... }
    """
    _ensure_tool_root_on_sys_path()
    if tool_roots is None:
        tool_roots = resolve_tool_roots()
    if config_root is None:
        config_root = get_config().env.config_root

    tool_config_root = config_root / "tools"
    tool_dirs = resolve_tool_dirs(tool_roots, config_root)
    llm_tools: dict[str, dict] = {}

    for config_path in glob.glob(str(tool_config_root / "llm_*.yaml")):
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

        tool_type = config.get("type")
        if not tool_type:
            logger.warning("Config file %s has no type. Skipping", config_path)
            continue

        if tool_type == "self":
            py_file = _resolve_self_tool_file(Path(config_path))
            if py_file is None:
                logger.warning(
                    "type: self but no matching .py file found: %s",
                    Path(config_path).with_suffix(".py"),
                )
                continue
            module = _load_llm_module(py_file, tool_dirs)
        elif is_import_path(tool_type):
            module = import_tool_module(tool_type)
        else:
            py_file = find_tool_file(tool_type, tool_roots)
            if py_file is None:
                logger.warning(
                    "Tool file not found: %s.py (tool_roots=%s)", tool_type, tool_roots
                )
                continue
            module = _load_llm_module(py_file, tool_dirs)
        if module is None:
            continue

        # build_schema が存在すればそれを呼び出して YAML config から動的スキーマを取得する
        if hasattr(module, "build_schema"):
            schema = module.build_schema(config)
        else:
            schema = getattr(module, "SCHEMA", None)
        execute: Callable | None = getattr(module, "execute", None)

        if schema is None or execute is None:
            logger.warning("SCHEMA or execute not found: %s", py_file)
            continue

        name = Path(config_path).stem

        # function.name を YAML のファイル名（stem）で上書き、description があれば上書き
        # ネスト形式（{"type":"function","function":{...}}）は function["description"] を上書き
        # フラット形式（{"name":"...","description":"..."}）は schema["description"] を上書き
        schema = dict(schema)
        function = dict(schema.get("function", {}))
        function["name"] = name
        if config.get("description"):
            if "function" in schema:
                function["description"] = config["description"]
            else:
                schema["description"] = config["description"]
        schema["function"] = function

        llm_tools[name] = {
            "schema": schema,
            "execute": execute,
            "supported_client_type": config.get("supported_client_type", "all"),
            "tool_config": config,
        }
        logger.info("LLM tool loaded: %s (%s)", name, tool_type)

    _validate_no_runtime_key_collision(llm_tools)

    return llm_tools


@lru_cache(maxsize=1)
def get_llm_tools() -> dict[str, dict]:
    """LLM ツール辞書をキャッシュ付きで返す（プロセス内で 1 回だけロードする）。

    初回呼び出し時のみ :func:`load_llm_tools` を実行し、YAML の走査とモジュールの
    動的 import が走る。2 回目以降は同一の dict をそのまま返すため、`!runtask` や
    拡張側の手動実行エンドポイント等、起動時以外の経路からも
    バケツリレーなしで LLM ツール一覧を取得できる。

    Returns
    -------
    dict
        load_llm_tools() と同じ形式のツール辞書（呼び出しごとに同一オブジェクト）

    Notes
    -----
    テストなどでキャッシュを破棄したい場合は ``get_llm_tools.cache_clear()`` を使う。
    """
    return load_llm_tools()


def _validate_no_runtime_key_collision(llm_tools: dict[str, dict]) -> None:
    """各ツールの tool_config が実行時共通キーと衝突していないか検証する。

    実行時にツールコンテキストへ注入される共通キーは、build_tool_context()
    （conversation_service）や run_conversation() の呼び出し元で動的に注入
    されるため、YAML の tool_config が同名キーを持つと、ネストしたツール
    呼び出し時にツール自身の設定値が静かに上書きされてしまう。
    起動時に検知して fail-fast させる。

    検証対象のキーはモジュールレベルの定数として固定せず、呼び出しのたびに
    「コア自身のフレームワークキー」と「拡張の `tool_context_providers()` が
    返すキー」を合成して求める。拡張のロードタイミングとこのモジュールの
    import 順序に依存させないため。

    Parameters
    ----------
    llm_tools : dict
        load_llm_tools() が構築したツール辞書

    Raises
    ------
    ValueError
        いずれかのツールの tool_config が実行時共通キーと衝突している場合
    """
    runtime_context_keys = _CORE_RUNTIME_CONTEXT_KEYS | set(
        get_tool_context_providers().keys()
    )
    for name, entry in llm_tools.items():
        tool_config = entry.get("tool_config", {})
        collisions = set(tool_config.keys()) & runtime_context_keys
        if collisions:
            raise ValueError(
                f"Tool '{name}' tool_config collides with runtime common keys: "
                f"{sorted(collisions)}. Please rename the key(s) on the YAML side."
            )


def _is_tool_allowed(entry: dict, client_type: str) -> bool:
    """ツールエントリが指定クライアント種別で使用可能かを返す。

    Parameters
    ----------
    entry : dict
        load_llm_tools() の各エントリ
    client_type : str
        クライアント種別（例: "discord", "task", 拡張が増やす種別）
    """
    supported = entry.get("supported_client_type", "all")
    if supported == "all":
        return True
    return supported == client_type


def build_tools_param(
    llm_tools: dict[str, dict],
    client_type: str = "discord",
    allowed_names: list[str] | None = None,
) -> list:
    """llm_tools から LLM に渡す tools パラメータリストを構築する。

    allowed_names が指定されている場合、まず llm_tools のキー（YAML stem）が
    allowed_names に含まれるツールのみに絞り込み、続けて client_type に
    基づくフィルタを適用する。

    Parameters
    ----------
    llm_tools : dict
        load_llm_tools() の戻り値
    client_type : str
        クライアント種別（例: "discord", "task", 拡張が増やす種別）
    allowed_names : list[str] | None
        許可するツール名（YAML stem）のリスト。None の場合は絞り込みなし。
        空リストの場合は空リストが返る。

    Returns
    -------
    list
        各ツールの schema を格納したリスト
    """
    return [
        entry["schema"]
        for name, entry in llm_tools.items()
        if (allowed_names is None or name in allowed_names)
        and _is_tool_allowed(entry, client_type)
    ]


async def _save_tool_cache(
    tool_name: str,
    tool_input: dict,
    tool_config: dict,
    result: Any,
) -> None:
    """ツール設定の cache.mode に応じて、実行結果を MongoDB に保存する。

    mode は以下の3種類:

    - ``disable`` (または未設定): キャッシュしない。
    - ``enable``: 現行通り、ツール実行時の引数全体を args_key としてキャッシュする。
      success=True かつ data が truthy のときのみ保存する。
    - ``auto``: ツール戻り値の ``cache_writes``（リスト）の各エントリをそのまま保存する。
      各エントリは ``{"key": str, "data": str, "expiration_minutes": int}`` を持つ。

    失敗時（success=False）はいずれの mode でもスキップする。

    Parameters
    ----------
    tool_name : str
        ツールの YAML stem（例: "llm_personal_info"）
    tool_input : dict
        ツールに渡された引数
    tool_config : dict
        ツールの YAML 設定
    result : Any
        ツール実行結果。dict であり success=True のときのみ保存処理を行う。
    """
    cache_config = (tool_config or {}).get("cache") or {}
    mode = cache_config.get("mode", "disable")
    if mode == "disable":
        return
    if not isinstance(result, dict):
        return
    if not result.get("success"):
        return

    if mode == "enable":
        data = result.get("data")
        if not data:
            return
        args_key = json.dumps(tool_input, sort_keys=True, ensure_ascii=False)
        expiration_minutes = cache_config.get("expiration_minutes", 30)
        try:
            from lilla_core.repository.tool_cache_repository import get_tool_cache_repo
            await get_tool_cache_repo().set(
                tool_name, args_key, str(data), expiration_minutes
            )
        except Exception as e:
            logger.warning("Tool cache save failed (%s): %s", tool_name, e)
        return

    if mode == "auto":
        cache_writes = result.get("cache_writes")
        if not isinstance(cache_writes, list):
            return
        from lilla_core.repository.tool_cache_repository import get_tool_cache_repo
        repo = get_tool_cache_repo()
        for entry in cache_writes:
            try:
                key = entry["key"]
                data = entry["data"]
                expiration_minutes = entry.get("expiration_minutes", 30)
                await repo.set(tool_name, str(key), str(data), expiration_minutes)
            except Exception as e:
                logger.warning(
                    "Tool cache save failed (%s, auto): %s", tool_name, e
                )
        return

    logger.warning("Unknown cache.mode (%s): %s", tool_name, mode)


def _format_tool_call_log_line(tool_name: str, depth: int) -> str:
    """Discord 表示用のツール呼び出しログ行を、`-#` サブテキスト記法で組み立てる。

    depth == 0 はメインの tool_call ループからの直接呼び出し、depth >= 1 は
    expert 内部などからのネスト呼び出しを表す。インデントは depth に応じて
    増やすが、MAX_TOOL_CALL_DEPTH を超える分は表示上それ以上インデントさせない。
    """
    indent_depth = min(depth, MAX_TOOL_CALL_DEPTH)
    if indent_depth <= 0:
        return f"-# 🔧 {tool_name}"
    indent = "  " * indent_depth
    return f"-# {indent}└ {tool_name}"


async def _notify_tool_call(context: dict, tool_name: str, depth: int) -> None:
    """client_type が discord のときのみ、Discord にツール呼び出しログを送信する。

    context["_tool_call_notifier"] が注入されている場合のみ送信する（Discord の
    通常会話フロー以外では注入されないため、二重のガードになる）。通知失敗は
    ツール実行自体を止めないよう例外を握りつぶし、警告ログのみ出す。
    """
    if context.get("client_type") != "discord":
        return
    notifier = context.get(_TOOL_CALL_NOTIFIER_KEY)
    if notifier is None:
        return
    try:
        await notifier(_format_tool_call_log_line(tool_name, depth))
    except Exception as e:
        logger.warning("Failed to send tool call log (%s): %s", tool_name, e)


def _make_call_tool(llm_tools: dict, context: dict) -> Callable:
    """入れ子のツール呼び出し用の call_tool 関数を作る。

    Parameters
    ----------
    llm_tools : dict
        load_llm_tools() の戻り値
    context : dict
        _tool_call_depth が積まれた状態の context（execute_tool_call に
        そのまま再入する）

    Returns
    -------
    Callable
        async def call_tool(tool_name: str, tool_input: dict) -> dict
    """
    async def call_tool(tool_name: str, tool_input: dict) -> dict:
        return await execute_tool_call(tool_name, tool_input, llm_tools, context)
    return call_tool


async def execute_tool_call(
    tool_name: str,
    tool_input: dict,
    llm_tools: dict[str, dict],
    context: dict,
) -> dict:
    """tool_name に対応するツールの execute() を呼び出し、結果を返す。

    まず llm_tools のキー（YAML stem）で検索し、見つからない場合は
    各エントリの schema["name"] で検索する。

    execute に渡す context には、すべてのツール（/tools, /config/tools 問わず）から
    利用できる ``call_tool(tool_name, tool_input) -> dict`` 関数が
    ``context["call_tool"]`` として自動的に注入される。これにより、
    ラッパーツール等から既存の LLM ツールを再利用できる。

    入れ子呼び出しは ``MAX_TOOL_CALL_DEPTH`` までに制限される（無限ネストによる
    トークン浪費の防止）。同名ツールの再帰呼び出し自体は、パラメータを変えた
    正当な利用ケースを妨げないよう禁止せず、深さ上限のみで制御する。

    ``context["client_type"] == "discord"`` かつ ``context["_tool_call_notifier"]``
    が注入されている場合、ツール実行前に `` -# 🔧 <tool_name>`` 形式のログ行を
    通知する。llm_expert 内部から本関数が再入された場合も同じ context 経由で
    notifier が伝播するため、ネストしたツール呼び出しも同じ仕組みで記録される。

    Parameters
    ----------
    tool_name : str
        LLM が返した tool_call の function.name
    tool_input : dict
        ツールに渡す入力パラメータ
    llm_tools : dict
        load_llm_tools() の戻り値
    context : dict
        ツール実行コンテキスト（各種クライアントなど）

    Returns
    -------
    dict
        ツールの実行結果 dict。エラー時もエラー情報を含む dict を返す（例外は伝播させない）。
    """
    depth = context.get(_TOOL_CALL_DEPTH_KEY, 0)
    if depth >= MAX_TOOL_CALL_DEPTH:
        logger.warning(
            "Tool call depth reached the limit (%d): %s",
            MAX_TOOL_CALL_DEPTH,
            tool_name,
        )
        return {
            "success": False,
            "tool_name": tool_name,
            "summary": "",
            "data": None,
            "error": f"ツール呼び出しの深さが上限({MAX_TOOL_CALL_DEPTH})に達しました",
        }

    tool_entry = llm_tools.get(tool_name)
    yaml_stem = tool_name if tool_entry is not None else None

    if tool_entry is None:
        for key, entry in llm_tools.items():
            if entry["schema"].get("function", {}).get("name") == tool_name:
                tool_entry = entry
                yaml_stem = key
                break

    if tool_entry is None:
        logger.warning("Tool not found: %s", tool_name)
        return {
            "success": False,
            "tool_name": tool_name,
            "summary": "",
            "data": None,
            "error": f"ツール '{tool_name}' が見つかりません",
        }

    try:
        tool_config = tool_entry.get("tool_config", {})
        tool_context = {**context, **tool_config}
        tool_context[_TOOL_CALL_DEPTH_KEY] = depth + 1
        tool_context["call_tool"] = _make_call_tool(llm_tools, tool_context)
        await _notify_tool_call(context, tool_name, depth)
        result = await tool_entry["execute"](tool_input, tool_context)
        await _save_tool_cache(yaml_stem, tool_input, tool_config, result)
        if isinstance(result, dict):
            result.pop("cache_writes", None)
        return result
    except Exception as e:
        logger.error("Tool execution error (%s): %s", tool_name, e)
        return {
            "success": False,
            "tool_name": tool_name,
            "summary": "",
            "data": None,
            "error": f"ツール実行中にエラーが発生しました: {e}",
        }
