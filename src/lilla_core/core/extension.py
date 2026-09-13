"""拡張（`Extension`）の基底クラスと、そのロード・参照 API。

コア（`lilla_core`）は拡張モジュールを直接 import せず、環境変数
`LILLA_EXTENSIONS`（カンマ区切りの import パス）が指すモジュールを
`load_extensions()` で読み込む。各モジュールは `Extension` のインスタンスを
`extension` 属性として 1 つだけ export する。未設定・空なら 0 個で、
コア単体起動になる。

`Extension` は Adapter 型で、全メソッドに「何もしない」デフォルトがある。
拡張は使うものだけをオーバーライドする。

貢献キーの衝突（`name` / ツール実行 context のキー / `client_type`）は
**拡張どうし** のときロード時に fail-fast する。静かな後勝ちはしない。
コアが持つ内蔵デフォルト（`client_type="discord"` のプロンプトなど）との
重複は衝突とみなさず、拡張側が優先される。

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

#: コア自身が配送を持つ `client_type`。拡張の `result_deliveries()` では使えない。
_RESERVED_DELIVERY_CLIENT_TYPES = frozenset({"discord"})

# 型エイリアス（可読性向上目的のためだけ）
StartupRepoFactory = Callable[[], Any]
DeliveryFn = Callable[[str], Awaitable[None]]
PromptProvider = Callable[[], str]
ConversationStartHook = Callable[[Any], Awaitable[None]]
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
        自動で解決したり、読み込み順を並べ替えたりはしない。
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

    def client_prompt_providers(self) -> dict[str, PromptProvider]:
        """システムプロンプトへ追記する文字列のプロバイダを `client_type` ごとに返す。"""
        return {}

    def conversation_start_hooks(self) -> dict[str, ConversationStartHook]:
        """`run_conversation` の冒頭で呼ばれるフックを `client_type` ごとに返す。"""
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
_client_prompt_providers: dict[str, PromptProvider] = {}
_conversation_start_hooks: dict[str, ConversationStartHook] = {}


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


def _validate_required_sections(extensions: list[Extension], provided: set[str]) -> None:
    """`required_config_sections()` が指す YAML セクションが実在することを検証する。

    Args:
        extensions: ロード順に並んだ拡張のリスト。
        provided: 拡張が `config_models()` で提供するセクション名の集合。

    Raises:
        ValueError: 誰も提供しておらず、コア確定のセクションでもない名前を要求した場合。
    """
    from lilla_core.core.config import core_config_section_names

    available = provided | set(core_config_section_names())
    for ext in extensions:
        for section in ext.required_config_sections():
            if section not in available:
                raise ValueError(
                    f"Extension '{ext.name}' requires config section '{section}', "
                    "but no loaded extension provides it"
                )


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
        ValueError: 名前または貢献キーが衝突している場合、コア確定のセクション名 /
            `EnvConfig` フィールド名を提供した場合、または誰も提供していない
            セクションを `required_config_sections()` が要求している場合。
    """
    from lilla_core.core.config import core_config_section_names, core_env_field_names

    for ext in extensions:
        if not isinstance(ext, Extension):
            raise TypeError(
                f"Expected an Extension instance, got {type(ext).__name__}"
            )
    _validate_names(extensions)

    config_models = _merge_unique(
        extensions, "config_models", "config model", reserved=core_config_section_names()
    )
    env_fields = _merge_unique(
        extensions, "env_fields", "env field", reserved=core_env_field_names()
    )
    _validate_required_sections(extensions, set(config_models))

    tool_context = _merge_unique(extensions, "tool_context_providers", "tool context provider")
    deliveries = _merge_unique(
        extensions,
        "result_deliveries",
        "result delivery",
        reserved=_RESERVED_DELIVERY_CLIENT_TYPES,
    )
    prompts = _merge_unique(extensions, "client_prompt_providers", "client prompt provider")
    start_hooks = _merge_unique(
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
        ValueError: 名前または貢献キーが衝突している場合、または
            `required_config_sections()` が誰も提供しないセクションを要求した場合。
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


def get_result_delivery(client_type: str) -> DeliveryFn | None:
    """`client_type` の結果配送関数を返す。未登録なら `None`。"""
    return _result_deliveries.get(client_type)


def get_client_prompt_provider(client_type: str) -> PromptProvider | None:
    """`client_type` のクライアント固有プロンプトのプロバイダを返す。未登録なら `None`。

    見つからない場合、呼び出し側はコアの内蔵デフォルト（`"discord"` のみ）へ
    フォールバックする。
    """
    return _client_prompt_providers.get(client_type)


def get_conversation_start_hook(client_type: str) -> ConversationStartHook | None:
    """`client_type` の会話開始フックを返す。未登録なら `None`。"""
    return _conversation_start_hooks.get(client_type)


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
