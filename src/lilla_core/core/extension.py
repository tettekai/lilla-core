"""拡張（`Extension`）の基底クラスと、そのロード・参照 API。

コア（`lilla_core`）は拡張モジュールを直接 import せず、環境変数
`LILLA_EXTENSIONS`（カンマ区切りの import パス）が指すモジュールを
`load_extensions()` で読み込む。各モジュールは `Extension` のインスタンスを
`extension` 属性として 1 つだけ export する。未設定・空なら 0 個で、
コア単体起動になる。

`Extension` は Adapter 型で、全メソッドに「何もしない」デフォルトがある。
拡張は使うものだけをオーバーライドする。

貢献キーの衝突（`name` / ツール実行 context のキー / 結果配送の `client_type`）は
**拡張どうし** のときロード時に fail-fast する。静かな後勝ちはしない。
一方、クライアント固有プロンプトと会話開始フックは「同じ `client_type` に複数の
拡張が足す」のが自然なため排他にせず、ロード順に連結する（加算式）。
コアが持つ内蔵デフォルト（`client_type="discord"` のプロンプトなど）との
重複は衝突とみなさず、拡張側が優先される。

拡張どうしの依存は宣言的に書く。`requires` に依存する拡張の `name` を並べると、
その拡張がロード済みで、かつ `LILLA_EXTENSIONS` 上で自分より前に並んでいることを
ロード時に検証する（自動並べ替えはしない）。他の拡張が提供する YAML セクション・
秘匿フィールド・ツール実行 context キーを読むだけなら、`required_config_sections()` /
`required_env_fields()` / `required_tool_context_keys()` で「誰かが提供していること」を
検証させる。汎用の `validate()` フックは持たず、実行時の検査は `setup()` で行う。

設定の差分は `config_models()` / `env_fields()` で申告する。`load_extensions()`
が全拡張の申告をマージし、`core/config.py` の `compose_config()` で 1 つの
`AppConfig` へ組んでプロセスの設定に据える。ホストが `AppConfig` のサブクラスを
書いて import 副作用で `set_config()` する仕組みは使わない（呼んでも合成結果で
上書きされる）。そのため `LILLA_EXTENSIONS` の並び順は設定の合成に影響しない。

`LILLA_EXTENSIONS` に並べたモジュールは同一プロセスで動く **信頼コード**
であり、サンドボックスではない。
"""
from __future__ import annotations

import importlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from pydantic import BaseModel

logger = logging.getLogger(__name__)

#: 拡張モジュールの import パスを並べる環境変数名（カンマ区切り）。
EXTENSIONS_ENV_VAR = "LILLA_EXTENSIONS"

#: 各拡張モジュールが `Extension` インスタンスを export する属性名。
EXTENSION_ATTR = "extension"

#: このコアが提供する `Extension` 契約のバージョン。契約を破壊的に変えたとき
#: （メソッドのシグネチャ・戻り値の形・context のフィールドの削除や改名）に上げる。
#: メソッドや context フィールドの追加は非破壊なので上げない。
EXTENSION_API_VERSION = 1

#: このコアがロードを受け付ける契約バージョンの集合。旧バージョンとの互換層を
#: 持つときはここへ足す。
SUPPORTED_EXTENSION_API_VERSIONS = frozenset({EXTENSION_API_VERSION})

#: コア自身が配送を持つ `client_type`。拡張の `result_deliveries()` では使えない。
_RESERVED_DELIVERY_CLIENT_TYPES = frozenset({"discord"})

#: コアが task ツールの実行 context へ必ず注入するキー（`handlers/task_handler.py` /
#: `commands/runtask.py`）。LLM ツール側の共通キーは `loaders/llm_tool_loader.py` の
#: `_CORE_RUNTIME_CONTEXT_KEYS` が正で、両方をまとめたものが `_core_tool_context_keys()`。
CORE_TASK_CONTEXT_KEYS = frozenset({"discord_client", "now", "llm_tools", "params"})

# 型エイリアス（可読性向上目的のためだけ）
StartupRepoFactory = Callable[[], Any]
DeliveryFn = Callable[[str], Awaitable[None]]
PromptProvider = Callable[[], str]
ConversationStartHook = Callable[["ConversationContext"], Awaitable[None]]
ContextValueProvider = Callable[[], Any]


@dataclass(frozen=True)
class SetupContext:
    """`Extension.setup()` に渡す起動時の文脈。

    位置引数を並べる代わりに 1 つのオブジェクトで渡すことで、後からフィールドを
    足しても既存の拡張の `setup()` シグネチャを壊さない（フィールド追加は非破壊、
    既存フィールドの削除・改名は破壊的変更として扱う）。
    """

    #: task ツールのレジストリ（`load_all_tools()` の戻り値）。
    tools: Any
    #: LLM ツールのレジストリ（`get_llm_tools()` の戻り値）。
    llm_tools: Any
    #: Discord クライアント。
    bot: Any
    #: プロセス全体で共有する設定（`get_config()` と同じインスタンス）。
    config: Any


@dataclass(frozen=True)
class ConversationContext:
    """会話開始フック（`Extension.conversation_start_hooks()`）に渡す会話 1 回分の文脈。

    `SetupContext` と同じく、位置引数ではなく 1 つのオブジェクトで渡す。フィールドの
    追加は非破壊、既存フィールドの削除・改名は破壊的変更として扱う。
    """

    #: 会話の送信元クライアント種別（`"discord"` や拡張が増やす種別）。
    client_type: str
    #: クライアント拡張が `run_conversation(client_state=...)` で渡した任意の状態
    #: （接続中クライアントの集合など）。コアは中身を解釈しない。
    client_state: Any = None
    #: Discord からの会話ならそのチャンネル ID。
    discord_channel_id: int | None = None
    #: 使用する LLM プロバイダー名。`None` なら既定。
    llm_name: str | None = None


class Extension:
    """コアへ機能を差し込むための拡張の基底クラス。

    拡張は本クラスを継承し、`name` に一意な名前を与えたうえで、必要な
    メソッドだけをオーバーライドする。オーバーライドしなかったメソッドは
    「貢献なし・何もしない」として扱われる。

    インスタンスはモジュールの import 時に生成されるため、コンストラクタで
    拡張固有のモジュールを import しないこと（設定の差し込みより先に走って
    しまう）。依存は各メソッドの中で遅延 import する。
    """

    #: 拡張の一意な名前。ログと衝突検査に使う。空文字・未設定はロード時に落とす。
    name: str = ""

    #: この拡張が書かれた `Extension` 契約のバージョン。既定は現在のコアの
    #: `EXTENSION_API_VERSION`。`SUPPORTED_EXTENSION_API_VERSIONS` に無い値を宣言した
    #: 拡張はロード時に fail-fast する（古い契約のまま動いて起動後に壊れるのを防ぐ）。
    api_version: int = EXTENSION_API_VERSION

    #: 依存する拡張の `name` のタプル。ここに並べた拡張がロードされていない、または
    #: `LILLA_EXTENSIONS` 上で自分より後ろに並んでいる場合はロード時に fail-fast する。
    #: コアは順序を並べ替えないため、利用者が正しい順に並べる。
    requires: tuple[str, ...] = ()

    def config_models(self) -> dict[str, type[BaseModel]]:
        """この拡張が足す YAML セクションを「セクション名 -> モデル」で返す。

        コアが起動時に `AppConfig` へ合成し、`get_config().<セクション名>` で
        型付きで読めるようにする。セクションが必須かどうかはモデルから導出され、
        全フィールドにデフォルトがあれば `lilla.yaml` に節が無くてもよく、必須
        フィールドを 1 つでも持つなら節そのものが必須になる。
        """
        return {}

    def env_fields(self) -> dict[str, str]:
        """この拡張が足す秘匿フィールドを「フィールド名 -> OS 環境変数名」で返す。

        コアが起動時に `EnvConfig` へ合成し、`get_config().env.<フィールド名>` で
        読めるようにする。合成されるフィールドの型は常に `str | None`（既定値
        `None`）で、必須フィールドや文字列以外の型は表現できない。
        """
        return {}

    def required_config_sections(self) -> list[str]:
        """自分では提供しないが `get_config()` で読む YAML セクション名を返す。

        どの拡張も提供しておらず、コア確定のセクションでもない名前を書いた場合は
        ロード時に fail-fast する。存在の検査だけを行い、拡張どうしの依存を
        自動で解決したり、読み込み順を並べ替えたりはしない（順序は `requires`）。
        """
        return []

    def required_env_fields(self) -> list[str]:
        """自分では提供しないが `get_config().env` で読む秘匿フィールド名を返す。

        どの拡張も `env_fields()` で提供しておらず、コア確定の `EnvConfig` フィールドでも
        ない名前を書いた場合はロード時に fail-fast する。
        """
        return []

    def required_tool_context_keys(self) -> list[str]:
        """自分では提供しないが、自分のツールが実行 context から読むキー名を返す。

        どの拡張も `tool_context_providers()` で提供しておらず、コアが実行時に注入する
        共通キー（`client_type` / `call_tool` など）でもない名前を書いた場合はロード時に
        fail-fast する。
        """
        return []

    def tool_roots(self) -> list[Path]:
        """`paths.tool_root` に足す LLM/task ツールの探索ディレクトリを返す。"""
        return []

    def command_packages(self) -> list[str]:
        """`load_all_commands()` が追加で走査するパッケージの import パスを返す。"""
        return []

    def startup_repos(self) -> list[StartupRepoFactory]:
        """`on_ready` で `init_collection()` を呼ぶリポジトリファクトリを返す。"""
        return []

    def tool_context_providers(self) -> dict[str, ContextValueProvider]:
        """ツール実行 context へ注入する値のプロバイダを、context キー名で返す。"""
        return {}

    def result_deliveries(self) -> dict[str, DeliveryFn]:
        """`!toolresult` の結果配送関数を `client_type` ごとに返す。"""
        return {}

    def client_prompt_providers(self) -> dict[str, list[PromptProvider]]:
        """システムプロンプトへ追記する文字列のプロバイダを `client_type` ごとにリストで返す。

        同じ `client_type` に複数の拡張が足せる（加算式）。コアは全拡張分を
        ロード順に連結し、各プロバイダの戻り値を空行区切りで追記する。
        """
        return {}

    def conversation_start_hooks(self) -> dict[str, list[ConversationStartHook]]:
        """`run_conversation` の冒頭で呼ばれるフックを `client_type` ごとにリストで返す。

        同じ `client_type` に複数の拡張が足せる（加算式）。コアは全拡張分を
        ロード順に連結し、`ConversationContext` を渡して順に await する。
        """
        return {}

    async def on_message(self, message: Any) -> bool:
        """`on_message` の冒頭で呼ばれる。自身で処理したなら `True` を返す。

        `True` を返すと後続の拡張も通常の会話フローも動かない。
        """
        return False

    async def setup(self, ctx: SetupContext) -> None:
        """`bot.start()` の前に await される起動処理。例外は fail-fast。

        Args:
            ctx: ツールレジストリ・Discord クライアント・設定をまとめた起動時の文脈。
        """
        return None


_extensions: list[Extension] = []
_config_models: dict[str, Any] = {}
_env_fields: dict[str, str] = {}
_tool_context_providers: dict[str, ContextValueProvider] = {}
_result_deliveries: dict[str, DeliveryFn] = {}
_client_prompt_providers: dict[str, list[PromptProvider]] = {}
_conversation_start_hooks: dict[str, list[ConversationStartHook]] = {}


def _merge_unique(
    extensions: list[Extension],
    method_name: str,
    label: str,
    reserved: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """各拡張の dict 貢献を 1 つへまとめる。キーが重複したら fail-fast する。

    Args:
        extensions: ロード順に並んだ拡張のリスト。
        method_name: 貢献を返すメソッド名（例: `"result_deliveries"`）。
        label: 例外メッセージに出す貢献の種類名。
        reserved: コアが押さえていて拡張が使えないキー。

    Returns:
        キーから値へのマージ済み dict。

    Raises:
        ValueError: 予約キーを使った場合、または拡張どうしでキーが重複した場合。
    """
    merged: dict[str, Any] = {}
    owners: dict[str, str] = {}
    for ext in extensions:
        for key, value in getattr(ext, method_name)().items():
            if key in reserved:
                raise ValueError(
                    f"Extension '{ext.name}' cannot provide {label} for reserved key "
                    f"'{key}' (handled by the core)"
                )
            if key in owners:
                raise ValueError(
                    f"Duplicate {label} key '{key}' provided by extensions "
                    f"'{owners[key]}' and '{ext.name}'"
                )
            owners[key] = ext.name
            merged[key] = value
    return merged


def _merge_lists(
    extensions: list[Extension], method_name: str, label: str
) -> dict[str, list[Any]]:
    """各拡張の「キー -> リスト」貢献を、キーごとにロード順で連結する（加算式）。

    Args:
        extensions: ロード順に並んだ拡張のリスト。
        method_name: 貢献を返すメソッド名（例: `"conversation_start_hooks"`）。
        label: 例外メッセージに出す貢献の種類名。

    Returns:
        キーから連結済みリストへの dict。

    Raises:
        ValueError: 値がリスト（またはタプル）でない場合。旧契約の
            「キー -> 関数 1 つ」の形をそのまま返した拡張をここで検出する。
    """
    merged: dict[str, list[Any]] = {}
    for ext in extensions:
        for key, values in getattr(ext, method_name)().items():
            if not isinstance(values, (list, tuple)):
                raise ValueError(
                    f"Extension '{ext.name}' must return a list for {label} "
                    f"'{key}', got {type(values).__name__}"
                )
            merged.setdefault(key, []).extend(values)
    return merged


def _validate_api_versions(extensions: list[Extension]) -> None:
    """各拡張の `api_version` がこのコアの受け付ける契約バージョンであることを検証する。

    Raises:
        ValueError: `api_version` が整数でない、または
            `SUPPORTED_EXTENSION_API_VERSIONS` に含まれない場合。
    """
    supported = ", ".join(str(v) for v in sorted(SUPPORTED_EXTENSION_API_VERSIONS))
    for ext in extensions:
        version = getattr(ext, "api_version", None)
        if isinstance(version, bool) or not isinstance(version, int):
            raise ValueError(
                f"Extension '{ext.name}' must define 'api_version' as an int, "
                f"got {version!r}"
            )
        if version not in SUPPORTED_EXTENSION_API_VERSIONS:
            raise ValueError(
                f"Extension '{ext.name}' declares api_version {version}, but this "
                f"lilla-core supports only api_version {supported} "
                f"(current: {EXTENSION_API_VERSION})"
            )


def _validate_names(extensions: list[Extension]) -> None:
    """`name` が設定済みかつ一意であることを検証する。

    Raises:
        ValueError: `name` が未設定・空文字・非文字列、または重複している場合。
    """
    seen: set[str] = set()
    for ext in extensions:
        name = getattr(ext, "name", "")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(
                f"Extension {type(ext).__name__} must define a non-empty 'name'"
            )
        if name in seen:
            raise ValueError(f"Duplicate extension name: '{name}'")
        seen.add(name)


def _validate_required(
    extensions: list[Extension], method_name: str, label: str, available: set[str]
) -> None:
    """各拡張の「要求側の申告」が、誰かの提供またはコア確定の名前で満たされることを検証する。

    `required_config_sections()` / `required_env_fields()` / `required_tool_context_keys()`
    の 3 つが同じ形で使う。存在の検査だけを行い、提供側の並び順は問わない。

    Args:
        extensions: ロード順に並んだ拡張のリスト。
        method_name: 要求する名前のリストを返すメソッド名。
        label: 例外メッセージに出す種類名（例: `"config section"`）。
        available: 提供済みの名前とコア確定の名前を合わせた集合。

    Raises:
        ValueError: 誰も提供しておらず、コア確定の名前でもない名前を要求した場合。
    """
    for ext in extensions:
        for key in getattr(ext, method_name)():
            if key not in available:
                raise ValueError(
                    f"Extension '{ext.name}' requires {label} '{key}', "
                    "but no loaded extension provides it"
                )


def _validate_requires(extensions: list[Extension]) -> None:
    """`requires` が指す拡張がロード済みで、かつ自分より前に並んでいることを検証する。

    コアは順序を並べ替えない。`on_message` の連鎖・`setup()` の await 順・
    `tool_roots()` の探索順は「ロード順」に依存するため、依存先が先に来るよう
    利用者が `LILLA_EXTENSIONS` を並べる。

    Args:
        extensions: ロード順に並んだ拡張のリスト。

    Raises:
        ValueError: `requires` が文字列のタプル / リストでない場合、依存先が
            ロードされていない場合、または依存先が自分より後ろに並んでいる場合。
    """
    position = {ext.name: index for index, ext in enumerate(extensions)}
    for index, ext in enumerate(extensions):
        requires = getattr(ext, "requires", ())
        if not isinstance(requires, (tuple, list)) or not all(
            isinstance(name, str) for name in requires
        ):
            raise ValueError(
                f"Extension '{ext.name}' must define 'requires' as a tuple of "
                f"extension names, got {requires!r}"
            )
        for name in requires:
            if name not in position:
                raise ValueError(
                    f"Extension '{ext.name}' requires extension '{name}', "
                    f"but it is not listed in {EXTENSIONS_ENV_VAR}"
                )
            if position[name] >= index:
                raise ValueError(
                    f"Extension '{ext.name}' requires extension '{name}', "
                    f"which must be listed before it in {EXTENSIONS_ENV_VAR}"
                )


def _core_tool_context_keys() -> frozenset[str]:
    """コアが実行時にツール context へ必ず注入するキーの集合を返す。

    LLM ツール側は `loaders/llm_tool_loader.py` の定義を正とし、`call_tool`（入れ子
    呼び出し用に各階層で作り直される）も含める。task ツール側は
    `CORE_TASK_CONTEXT_KEYS`。拡張の `tool_context_providers()` はこれらのキーを
    提供できない（コアの注入で静かに上書きされるのを防ぐため、ロード時に落とす）。
    循環 import を避けるため呼び出し時に読む。
    """
    from lilla_core.loaders.llm_tool_loader import _CORE_RUNTIME_CONTEXT_KEYS

    return _CORE_RUNTIME_CONTEXT_KEYS | CORE_TASK_CONTEXT_KEYS | {"call_tool"}


def set_extensions(extensions: list[Extension]) -> None:
    """拡張インスタンスのリストを検証してプロセスへ登録する。

    モジュールの import を伴わないため、テストから直接呼べる。
    `load_extensions()` は import 後にこの関数を呼ぶ。

    設定の差分（`config_models()` / `env_fields()`）もここでマージ・検証するが、
    `AppConfig` への合成そのものは行わない。プロセスの設定を差し替えるのは
    `load_extensions()` の役目で、テストが拡張を登録するだけで設定を壊さずに済む。

    Args:
        extensions: ロード順に並んだ `Extension` インスタンス。

    Raises:
        TypeError: `Extension` のインスタンスでない要素が含まれる場合。
        ValueError: 名前または貢献キーが衝突している場合、`api_version` がこのコアの
            受け付ける契約バージョンでない場合、コア確定のセクション名 /
            `EnvConfig` フィールド名を提供した場合、`requires` の拡張が未ロードか
            自分より後ろに並んでいる場合、または誰も提供していない名前を
            `required_config_sections()` / `required_env_fields()` /
            `required_tool_context_keys()` が要求している場合。
    """
    from lilla_core.core.config import core_config_section_names, core_env_field_names

    for ext in extensions:
        if not isinstance(ext, Extension):
            raise TypeError(
                f"Expected an Extension instance, got {type(ext).__name__}"
            )
    _validate_names(extensions)
    _validate_api_versions(extensions)
    _validate_requires(extensions)

    config_models = _merge_unique(
        extensions, "config_models", "config model", reserved=core_config_section_names()
    )
    env_fields = _merge_unique(
        extensions, "env_fields", "env field", reserved=core_env_field_names()
    )
    _validate_required(
        extensions,
        "required_config_sections",
        "config section",
        set(config_models) | set(core_config_section_names()),
    )
    _validate_required(
        extensions,
        "required_env_fields",
        "env field",
        set(env_fields) | set(core_env_field_names()),
    )

    core_context_keys = _core_tool_context_keys()
    tool_context = _merge_unique(
        extensions,
        "tool_context_providers",
        "tool context provider",
        reserved=core_context_keys,
    )
    _validate_required(
        extensions,
        "required_tool_context_keys",
        "tool context key",
        set(tool_context) | set(core_context_keys),
    )
    deliveries = _merge_unique(
        extensions,
        "result_deliveries",
        "result delivery",
        reserved=_RESERVED_DELIVERY_CLIENT_TYPES,
    )
    prompts = _merge_lists(extensions, "client_prompt_providers", "client prompt provider")
    start_hooks = _merge_lists(
        extensions, "conversation_start_hooks", "conversation start hook"
    )

    global _extensions
    _extensions = list(extensions)
    _config_models.clear()
    _config_models.update(config_models)
    _env_fields.clear()
    _env_fields.update(env_fields)
    _tool_context_providers.clear()
    _tool_context_providers.update(tool_context)
    _result_deliveries.clear()
    _result_deliveries.update(deliveries)
    _client_prompt_providers.clear()
    _client_prompt_providers.update(prompts)
    _conversation_start_hooks.clear()
    _conversation_start_hooks.update(start_hooks)


def reset_extensions() -> None:
    """登録済みの拡張をすべて捨てる（テスト用）。"""
    set_extensions([])


def load_extensions(spec: str | None = None) -> list[Extension]:
    """`LILLA_EXTENSIONS` が指すモジュールを import して拡張を登録する。

    登録のあと、拡張が申告した YAML セクションと秘匿フィールドを
    `compose_config()` で `AppConfig` へ合成し、`set_config()` でプロセスの設定に
    据える。拡張が 0 個、または申告が空なら素の `AppConfig` になる。

    コアの他の初期化（`get_config()` / コマンド・ツールのロード）より前に
    呼ぶこと。ここで組んだ設定インスタンスを以降の `get_config()` が返すため。

    Args:
        spec: カンマ区切りの import パス。`None` なら `LILLA_EXTENSIONS` を読む。

    Returns:
        ロード順に並んだ `Extension` インスタンスのリスト。

    Raises:
        ImportError: モジュールを import できない場合。
        AttributeError: モジュールが `extension` 属性を持たない場合。
        TypeError: `extension` が `Extension` インスタンスでない場合。
        ValueError: 名前または貢献キーが衝突している場合、`requires` の依存が
            満たされない場合、または `required_*()` が誰も提供しない名前を要求した場合。
        pydantic.ValidationError: 合成後のモデルで設定の検証に失敗した場合。
    """
    if spec is None:
        spec = os.environ.get(EXTENSIONS_ENV_VAR, "")

    module_paths = [part.strip() for part in spec.split(",") if part.strip()]
    extensions: list[Extension] = []
    for module_path in module_paths:
        module = importlib.import_module(module_path)
        if not hasattr(module, EXTENSION_ATTR):
            raise AttributeError(
                f"Extension module '{module_path}' must export "
                f"a single '{EXTENSION_ATTR}' attribute"
            )
        extensions.append(getattr(module, EXTENSION_ATTR))

    # コアの他モジュールより先に拡張モジュールを import させるため、`config` の
    # import は拡張の import が終わったここまで遅らせる。
    from lilla_core.core.config import compose_config, set_config

    set_extensions(extensions)
    set_config(compose_config(get_config_models(), get_env_fields()))
    if extensions:
        logger.info("Extensions loaded: %s", ", ".join(ext.name for ext in extensions))
    return get_extensions()


def get_extensions() -> list[Extension]:
    """登録済み拡張のリストのコピーをロード順で返す。"""
    return list(_extensions)


def get_config_models() -> dict[str, Any]:
    """全拡張の YAML セクションモデルのマージ済み dict のコピーを返す。"""
    return dict(_config_models)


def get_env_fields() -> dict[str, str]:
    """全拡張の秘匿フィールドのマージ済み dict のコピーを返す。"""
    return dict(_env_fields)


def get_startup_repos() -> list[StartupRepoFactory]:
    """全拡張の起動時リポジトリファクトリをロード順で連結して返す。"""
    factories: list[StartupRepoFactory] = []
    for ext in _extensions:
        factories.extend(ext.startup_repos())
    return factories


def get_tool_roots() -> list[Path]:
    """全拡張の追加ツール探索ディレクトリをロード順で連結して返す。"""
    roots: list[Path] = []
    for ext in _extensions:
        roots.extend(ext.tool_roots())
    return roots


def get_command_packages() -> list[str]:
    """全拡張の追加コマンドパッケージをロード順で返す（重複は除く）。"""
    packages: list[str] = []
    for ext in _extensions:
        for package in ext.command_packages():
            if package not in packages:
                packages.append(package)
    return packages


def get_tool_context_providers() -> dict[str, ContextValueProvider]:
    """ツール実行 context プロバイダのマージ済み dict のコピーを返す。"""
    return dict(_tool_context_providers)


def build_tool_context() -> dict[str, Any]:
    """登録済みプロバイダを評価し、ツール実行 context の拡張由来部分を組み立てて返す。

    LLM ツール（`services/conversation_service.py`）と task ツール
    （`handlers/task_handler.py` / `commands/runtask.py`）の両方がこの結果を土台にし、
    そこへコア確定のキー（`client_type` / `discord_client` / `now` など）を重ねる。
    コアは登録内容を列挙するだけなので、ツール（＝必要なクライアント）が増えても
    本関数を編集する必要はない。1 つのプロバイダが失敗してもそのキーが欠けるだけで、
    他のプロバイダと呼び出し元の処理は妨げない。

    Returns:
        context キー名から、プロバイダの戻り値への dict。
    """
    context: dict[str, Any] = {}
    for name, provider in _tool_context_providers.items():
        try:
            context[name] = provider()
        except Exception as e:
            logger.debug("Skipped initialization of %s: %s", name, e)
    return context


def get_result_delivery(client_type: str) -> DeliveryFn | None:
    """`client_type` の結果配送関数を返す。未登録なら `None`。"""
    return _result_deliveries.get(client_type)


def get_client_prompt_providers(client_type: str) -> list[PromptProvider]:
    """`client_type` のクライアント固有プロンプトのプロバイダをロード順で返す。

    未登録なら空リスト。空の場合、呼び出し側はコアの内蔵デフォルト
    （`"discord"` のみ）へフォールバックする。
    """
    return list(_client_prompt_providers.get(client_type, []))


def get_conversation_start_hooks(client_type: str) -> list[ConversationStartHook]:
    """`client_type` の会話開始フックをロード順で返す。未登録なら空リスト。"""
    return list(_conversation_start_hooks.get(client_type, []))


async def run_setup_hooks(tools: Any, llm_tools: Any, bot: Any) -> None:
    """全拡張の `setup()` をロード順に await する。例外はそのまま伝播させる。

    `SetupContext` はここで 1 つだけ組み立て、全拡張へ同じインスタンスを渡す。
    `config` は呼び出し時点の `get_config()`（拡張の申告を合成済みのもの）。

    Args:
        tools: task ツールのレジストリ。
        llm_tools: LLM ツールのレジストリ。
        bot: Discord クライアント。
    """
    from lilla_core.core.config import get_config

    ctx = SetupContext(tools=tools, llm_tools=llm_tools, bot=bot, config=get_config())
    for ext in _extensions:
        await ext.setup(ctx)


async def dispatch_on_message(message: Any, bot: Any = None) -> bool:
    """全拡張の `on_message` をロード順に呼び、処理済みかどうかを返す。

    いずれかが `True` を返した時点で後続は呼ばない。例外が出た場合はその
    1 通の処理をそこで打ち切り（プロセスは落とさない）、ERROR ログと
    エラー通知チャンネルへ出したうえで `True` を返す。壊れたフックのまま
    通常の会話フローへ進むと原因を追いにくいため。

    Args:
        message: 受信した Discord メッセージ。
        bot: エラー通知に使う Discord クライアント。

    Returns:
        後続の標準処理を行わないなら `True`。
    """
    from lilla_core.core.error_notify import notify_error
    from lilla_core.ui.messages import t

    for ext in _extensions:
        try:
            if await ext.on_message(message):
                return True
        except Exception as e:
            logger.error(
                "Extension '%s' on_message failed: %s", ext.name, e, exc_info=True
            )
            await notify_error(bot, t("extension.on_message_error", name=ext.name), e)
            return True
    return False
