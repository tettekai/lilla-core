"""拡張がコア（`lilla_core`）へ差し込むための拡張ポイント。

`lilla_core/bot.py` は拡張モジュールを
直接 import しないよう、以下の登録ポイントを介して拡張の初期化処理を
受け取る。

- 起動時リポジトリ: `on_ready` で `init_collection()` を呼ぶ追加リポジトリ
  ファクトリを登録する
- メッセージフック: `on_message` の冒頭で必ず 1 度呼ばれるハンドラ
  （外部からの通知の入口などに使う）。未登録時はデフォルトフック
  （常に `False` を返す）を返し、コア単体起動でも安全に動くようにする
- 起動タスク: `main()` で `bot.start()` の前に順に await される非同期関数
- 結果配送: `!toolresult` が外部エージェントの結果を届ける先を
  `client_type` ごとに差し替えるための非同期関数（Discord 以外の
  クライアントへの配送処理を追加する場合など）
- クライアント固有プロンプト: `client_type` ごとにシステムプロンプトへ
  追記する文字列を返すプロバイダ。プロンプトは「呼ぶたびにファイルから
  読み直す」設計のため、文字列ではなく都度評価される関数を登録する
- 会話開始フック: `run_conversation` の冒頭で `client_type` ごとに呼ばれる
  非同期関数（会話処理本体が始まる前にクライアント固有の前処理を挟みたい
  場合に使う）
- ツール実行 context プロバイダ: `build_tool_context()` が context へ注入する
  値を、context のキー名ごとに供給する関数。新しいツールを増やしてもコアを編集せずに済むよう、
  コア側は登録内容を列挙するだけにする

いずれの getter も呼び出し時点のリスト・関数を返す。呼び出し側は
モジュールレベルで結果を固定せず、`on_ready` / `on_message` / `main` の
それぞれの実行時に都度取得すること（順序依存を暗黙にしないため）。
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

# 型エイリアス（可読性向上目的のためだけ）
StartupRepoFactory = Callable[[], Any]
MessageHook = Callable[[Any], Awaitable[bool]]
StartupTask = Callable[[Any, Any, Any], Awaitable[None]]
DeliveryFn = Callable[[str], Awaitable[None]]
PromptProvider = Callable[[], str]
ConversationStartHook = Callable[[Any], Awaitable[None]]
ContextValueProvider = Callable[[], Any]


_extra_startup_repos: list[StartupRepoFactory] = []
_message_hook: MessageHook | None = None
_startup_tasks: list[StartupTask] = []
_result_deliveries: dict[str, DeliveryFn] = {}
_client_prompt_providers: dict[str, PromptProvider] = {}
_conversation_start_hooks: dict[str, ConversationStartHook] = {}
_tool_context_providers: dict[str, ContextValueProvider] = {}


async def _default_message_hook(_message: Any) -> bool:
    """未登録時のデフォルトメッセージフック。常に `False` を返す。

    コア単体起動時（拡張が未登録）の場合 `False` を返すことで
    「通知としては処理されなかった」＝後続の通常フローへ進めることを示す。
    """
    return False


def register_startup_repo(factory: StartupRepoFactory) -> None:
    """`on_ready` で `init_collection()` を呼ぶリポジトリファクトリを登録する。"""
    _extra_startup_repos.append(factory)


def get_extra_startup_repos() -> list[StartupRepoFactory]:
    """登録済みの追加起動リポジトリファクトリ一覧のコピーを返す。

    呼び出し側での意図しない書き換えを防ぐためコピーを返す。
    """
    return list(_extra_startup_repos)


def register_message_hook(hook: MessageHook) -> None:
    """`on_message` の冒頭で必ず 1 度呼ばれるハンドラを登録する。

    1 個の登録のみを想定。複数回呼んだ場合は最後の登録が有効になる
    （複数登録の合成は行わない）。
    """
    global _message_hook
    _message_hook = hook


def get_message_hook() -> MessageHook:
    """現在有効なメッセージフックを返す（未登録時はデフォルトフック）。"""
    if _message_hook is not None:
        return _message_hook
    return _default_message_hook


def register_startup_task(task: StartupTask) -> None:
    """`main()` で `bot.start()` の前に await する非同期関数を登録する。

    シグネチャは `async def task(tools, llm_tools, bot) -> None`。
    """
    _startup_tasks.append(task)


def get_startup_tasks() -> list[StartupTask]:
    """登録済みの起動タスク一覧のコピーを返す。"""
    return list(_startup_tasks)


def register_result_delivery(client_type: str, fn: DeliveryFn) -> None:
    """指定した `client_type` 向けの結果配送関数を登録する。

    `!toolresult` が外部エージェントからの結果を届ける先を、依頼元の
    クライアント種別ごとに差し替えるための拡張ポイント。

    同じ `client_type` を二度登録した場合は後勝ち（合成はしない。
    `register_message_hook` と同じ規約）。

    Args:
        client_type: 依頼レコードの `client_type`（例: 拡張が増やす種別）。
        fn: `async def fn(text: str) -> None` 形式の非同期関数。
            同期関数は登録しない想定。
    """
    _result_deliveries[client_type] = fn


def get_result_delivery(client_type: str) -> DeliveryFn | None:
    """登録済みの配送関数を返す。未登録なら `None`。

    未登録（コア単体起動など）の場合、呼び出し側は Discord 配送へ
    フォールバックする。
    """
    return _result_deliveries.get(client_type)


def register_client_prompt_provider(client_type: str, provider: PromptProvider) -> None:
    """指定した `client_type` 向けのクライアント固有プロンプトのプロバイダを登録する。

    プロンプトは呼ぶたびにファイルから読み直す設計（`AppConfig` の各
    `prompt_*` プロパティが `load_text_resources` を都度呼ぶ）のため、
    文字列ではなく「呼ばれるたびに評価される関数」を登録する。取得側は
    値をキャッシュしない。

    同じ `client_type` を二度登録した場合は後勝ち（合成はしない。
    `register_result_delivery` と同じ規約）。

    Args:
        client_type: プロンプトを差し込むクライアント種別（例: `"discord"`）。
        provider: 引数なしでプロンプト本文を返す関数。
    """
    _client_prompt_providers[client_type] = provider


def get_client_prompt_provider(client_type: str) -> PromptProvider | None:
    """登録済みのクライアント固有プロンプトのプロバイダを返す。未登録なら `None`。

    未登録（コア単体起動など）の場合、呼び出し側はクライアント固有プロンプトを
    付与しない。
    """
    return _client_prompt_providers.get(client_type)


def register_conversation_start_hook(client_type: str, fn: ConversationStartHook) -> None:
    """指定した `client_type` の会話開始時に呼ばれるフックを登録する。

    `run_conversation` の冒頭で、その会話の `client_type` に対応するフックが
    登録されていれば 1 度だけ await される。

    同じ `client_type` を二度登録した場合は後勝ち（合成はしない。
    `register_result_delivery` と同じ規約）。

    Args:
        client_type: フックを走らせるクライアント種別（例: 拡張が増やす種別）。
        fn: `async def fn(ws_clients) -> None` 形式の非同期関数。
            `ws_clients` は接続中クライアント集合（無ければ `None`）。
    """
    _conversation_start_hooks[client_type] = fn


def get_conversation_start_hook(client_type: str) -> ConversationStartHook | None:
    """登録済みの会話開始フックを返す。未登録なら `None`。

    未登録（コア単体起動など）の場合、呼び出し側は何もしない。
    """
    return _conversation_start_hooks.get(client_type)


def register_tool_context_provider(name: str, provider: ContextValueProvider) -> None:
    """ツール実行 context へ注入する値のプロバイダを、context キー名で登録する。

    `provider` は `build_tool_context()` が呼ぶたびに評価される（レジストリ側は
    値をキャッシュしない）。シングルトンを返すかどうかは provider 自身の責務。

    同じ `name` を二度登録した場合は後勝ち（合成はしない。
    `register_result_delivery` と同じ規約）。

    Args:
        name: ツール実行 context のキー名（例: `"external_api_client"`）。
        provider: 引数なしで context へ入れる値を返す関数。
    """
    _tool_context_providers[name] = provider


def get_tool_context_providers() -> dict[str, ContextValueProvider]:
    """登録済みの全プロバイダを name をキーにした dict のコピーで返す。

    他の拡張ポイントが「1 件を引く」形なのに対し、`build_tool_context()` は
    登録内容を列挙して context を組み立てるため、全件を返す。

    呼び出し側での意図しない書き換えを防ぐためコピーを返す
    （`get_extra_startup_repos` / `get_startup_tasks` と同じ規約）。
    """
    return dict(_tool_context_providers)
