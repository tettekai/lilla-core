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
`AppConfig` へ組んでプロセスの設定に据える。拡張の YAML セクションはコア確定の
トップレベル節とは別の `extensions:` の下に置かれ（`get_config().extensions.<節名>`）、
コアが後から節を確定しても拡張側の名前と衝突しない。ホストが `AppConfig` のサブクラスを
書いて import 副作用で `set_config()` する仕組みは使わない（呼んでも合成結果で
上書きされる）。そのため `LILLA_EXTENSIONS` の並び順は設定の合成に影響しない。

観測用ダッシュボードへの差し込みは `dashboard_*()` の 4 メソッドで申告する。
経路の識別子は `name` ただ 1 つで、ハッシュ・API 接頭辞・公開コールバック・
静的 URL はすべてコアが `name` から導出する（`DashboardPageEntry`）。そのため
`name` は URL に埋まる文字列でもあり、形（`_EXTENSION_NAME_RE`）と予約語
（`RESERVED_EXTENSION_NAMES`）をロード時に検査する。ダッシュボードの HTTP
サーバー本体はコアにはまだ無く、申告を集めて配るところまでがコアの役目。

`LILLA_EXTENSIONS` に並べたモジュールは同一プロセスで動く **信頼コード**
であり、サンドボックスではない。
"""
from __future__ import annotations

import importlib
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Literal

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

#: 観測用ダッシュボードの経路を `Extension.name` から導出するときの接頭辞。
#: 拡張は経路そのものを申告せず、`name` を 1 つ決めるだけでよい。
DASHBOARD_API_PREFIX = "/api"
DASHBOARD_PUBLIC_PREFIX = "/oauth"
DASHBOARD_STATIC_PREFIX = "/static/ext"

#: 拡張ページのフロントエンドモジュールのファイル名。`dashboard_static_dir()` が
#: 返すディレクトリ直下に置く。
DASHBOARD_PAGE_MODULE = "page.js"

#: `DashboardPage.group` に指定できる値。常用ナビか、管理メニューか。
DASHBOARD_GROUPS = frozenset({"main", "admin"})

#: `Extension.name` に使える形（先頭は英小文字か数字、以降は英小文字・数字・ハイフン）。
#: `name` はダッシュボードの URL パス・ハッシュ・静的ディレクトリ名へそのまま埋まるため、
#: パスを壊す文字（`/` や `..`、空白、`%` など）を弾く。
_EXTENSION_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

#: 拡張が `name` に使えない名前。ダッシュボードの URL 構造（`/api` `/oauth` `/static`）と、
#: 組み込み画面・認証エンドポイントの第 1 セグメントを押さえる。`name` はそのまま
#: `/api/{name}` や `#/{name}` になるため、ここを空けておかないと拡張が正当な申告を
#: しただけで組み込みの経路を食う。ダッシュボード本体は将来コアへ移す想定で、
#: 予約はその移設先を先に確保しておくもの。
RESERVED_EXTENSION_NAMES = frozenset(
    {
        # URL 構造
        "api",
        "oauth",
        "static",
        "admin",
        "dashboard",
        # 認証エンドポイント
        "auth",
        "login",
        "logout",
        "setup",
        # 組み込み画面
        "home",
        "conversations",
        "memos",
        "logs",
    }
)

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


@dataclass(frozen=True)
class DashboardPage:
    """拡張が観測用ダッシュボードへ足すタブ 1 つ分の申告。

    識別子は持たない。ハッシュ・API 接頭辞・静的 URL はすべて拡張の `name` から
    導出する（`DashboardPageEntry` 参照）ので、経路を申告する欄は作らない。
    """

    #: ナビに表示する文字列。多言語化したい拡張は `dashboard_page()` の中で
    #: `lilla_core.ui.messages.t()` の戻り値を入れる（メソッドなので呼ばれるたびに
    #: 評価され、ロケール切り替えに追随する）。
    label: str
    #: 常用ナビ（`"main"`）か、管理メニュー（`"admin"`）か。
    group: Literal["main", "admin"]

    def __post_init__(self) -> None:
        """`label` が非空文字列で、`group` が既知の値であることを検証する。

        `Literal` は型注釈でしかなく実行時には効かないため、ここで明示的に見る。

        Raises:
            ValueError: `label` が空、または `group` が `DASHBOARD_GROUPS` にない場合。
        """
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("DashboardPage.label must be a non-empty string")
        if self.group not in DASHBOARD_GROUPS:
            groups = ", ".join(sorted(DASHBOARD_GROUPS))
            raise ValueError(
                f"DashboardPage.group must be one of {groups}, got {self.group!r}"
            )


@dataclass(frozen=True)
class DashboardRoute:
    """拡張が観測用ダッシュボードへ足す HTTP ルート 1 本分の申告。

    コアはダッシュボードの HTTP サーバー本体を持たないため、ここでは aiohttp の
    型を使わずコア独自の形で受け取る。ホストが自分のフレームワークのルートへ
    変換して載せる（`Extension` の契約を特定の HTTP ライブラリのバージョンに
    縛らないため）。
    """

    #: HTTP メソッド。小文字で書いても大文字へ正規化される。
    method: str
    #: 完全なパス。`/api/{name}` 配下（セッション認証の内側）または
    #: `/oauth/{name}` 配下（認証の外側）でなければロード時に落ちる。
    path: str
    #: リクエストを処理する非同期ハンドラー。型はホストのフレームワーク次第。
    handler: Callable[..., Any]

    def __post_init__(self) -> None:
        """メソッド・パス・ハンドラーの形を検証し、メソッドを大文字へ正規化する。

        Raises:
            ValueError: `method` が空、`path` が `/` 始まりでない、または
                `handler` が callable でない場合。
        """
        if not isinstance(self.method, str) or not self.method.strip():
            raise ValueError("DashboardRoute.method must be a non-empty string")
        if not isinstance(self.path, str) or not self.path.startswith("/"):
            raise ValueError(
                f"DashboardRoute.path must start with '/', got {self.path!r}"
            )
        if not callable(self.handler):
            raise ValueError("DashboardRoute.handler must be callable")
        object.__setattr__(self, "method", self.method.strip().upper())


@dataclass(frozen=True)
class DashboardPageEntry:
    """`DashboardPage` に `name` からの導出値を添えた、ホストが読む形。

    導出の規則はここ 1 か所だけに持ち、ホスト側で組み立て直さない。
    `name = "google-oauth"` なら次のようになる::

        hash             #/google-oauth      （group="admin" なら #/admin/google-oauth）
        api_prefix       /api/google-oauth
        callback_prefix  /oauth/google-oauth
        static_url       /static/ext/google-oauth/
        module_url       /static/ext/google-oauth/page.js
    """

    #: 申告した拡張の `name`。
    name: str
    #: ナビに表示する文字列。
    label: str
    #: `"main"`（常用ナビ）か `"admin"`（管理メニュー）。
    group: str
    #: SPA のハッシュ。
    hash: str
    #: セッション認証の内側に載るこの拡張の API 接頭辞。
    api_prefix: str
    #: 認証の外側に載るこの拡張の公開エンドポイントの接頭辞。
    callback_prefix: str
    #: 静的ファイルの配信 URL（末尾はスラッシュ）。
    static_url: str
    #: フロントエンドが `import()` する JS モジュールの URL。
    module_url: str

    @classmethod
    def from_page(cls, name: str, page: DashboardPage) -> DashboardPageEntry:
        """拡張の `name` とページ申告から、経路を導出したエントリを組み立てる。

        Args:
            name: 申告した拡張の `name`。
            page: `Extension.dashboard_page()` の戻り値。

        Returns:
            導出済みの `DashboardPageEntry`。
        """
        static_url = _dashboard_static_url(name)
        return cls(
            name=name,
            label=page.label,
            group=page.group,
            hash=f"#/admin/{name}" if page.group == "admin" else f"#/{name}",
            api_prefix=_dashboard_api_prefix(name),
            callback_prefix=_dashboard_public_prefix(name),
            static_url=static_url,
            module_url=f"{static_url}{DASHBOARD_PAGE_MODULE}",
        )


@dataclass(frozen=True)
class DashboardStaticMount:
    """拡張が同梱する静的ファイルの配信先（URL 接頭辞とディレクトリ）。"""

    #: 申告した拡張の `name`。
    name: str
    #: 配信 URL の接頭辞（末尾はスラッシュ）。認証の外側に載る。
    url_prefix: str
    #: 配信するローカルディレクトリ。
    directory: Path


def _dashboard_api_prefix(name: str) -> str:
    """`name` からセッション API の接頭辞を導出する。"""
    return f"{DASHBOARD_API_PREFIX}/{name}"


def _dashboard_public_prefix(name: str) -> str:
    """`name` から公開エンドポイント（認証の外側）の接頭辞を導出する。"""
    return f"{DASHBOARD_PUBLIC_PREFIX}/{name}"


def _dashboard_static_url(name: str) -> str:
    """`name` から静的ファイルの配信 URL 接頭辞を導出する（末尾スラッシュ付き）。"""
    return f"{DASHBOARD_STATIC_PREFIX}/{name}/"


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

        コアが起動時に `AppConfig` の `extensions` の下へ合成し、
        `get_config().extensions.<セクション名>`（型付きなら `core/config.py` の
        `get_section()`）で読めるようにする。YAML でも `extensions:` の下に書き、
        トップレベルに書くと起動時に落ちる。`extensions:` の名前空間はコア確定の
        節と別なので、コアと同名のセクションを申告してもよい。

        セクションが必須かどうかはモデルから導出され、全フィールドにデフォルトが
        あれば `lilla.yaml` に節が無くてもよく、必須フィールドを 1 つでも持つなら
        節そのものが必須になる。
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
        """自分では提供しないが `get_config().extensions` で読む YAML セクション名を返す。

        どの拡張も `config_models()` で提供していない名前を書いた場合はロード時に
        fail-fast する。コア確定のトップレベル節は常にあり、`extensions:` の下にも
        無いため、ここへは書かない（書くと誰も提供していない扱いで落ちる）。
        存在の検査だけを行い、拡張どうしの依存を自動で解決したり、読み込み順を
        並べ替えたりはしない（順序は `requires`）。
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

    def tool_config_roots(self) -> list[Path]:
        """この拡張が同梱する既定のツール YAML（`llm_*.yaml` / `task_*.yaml`）のディレクトリを返す。

        ローダーは「拡張の `tool_config_roots()`（ロード順）→ `${CONFIG_ROOT}/tools`」の
        順に YAML を集め、同じファイル名（stem）は `${CONFIG_ROOT}/tools` 側が丸ごと
        上書きする（利用者の設定が常に勝つ）。拡張どうしで同じ stem を同梱した場合は
        fail-fast する。利用者が同梱ツールを無効化したいときは、`${CONFIG_ROOT}/tools` に
        同名の YAML を置いて `enabled: false` と書く。
        """
        return []

    def locale_dirs(self) -> list[Path]:
        """この拡張が同梱する UI 文言カタログ（`{locale}.yaml`）のディレクトリを返す。

        ファイル名はコアと同じ `ja.yaml` / `en.yaml` などで、カタログの
        **トップレベルのキーはこの拡張の `name` ただ 1 つ** でなければならない
        （例: `name = "lilla-habits"` なら YAML は `lilla-habits:` の 1 ノードだけを
        持ち、呼び出しは `t("lilla-habits.notify.title")` になる）。コアが自動で
        prefix を付けることはしない。規約に反するカタログはカタログの読み込み時に
        `ValueError` で落ちる。存在しないディレクトリは WARNING を出して読み飛ばす。
        """
        return []

    def command_packages(self) -> list[str]:
        """`load_all_commands()` が追加で走査するパッケージの import パスを返す。"""
        return []

    def dashboard_page(self) -> DashboardPage | None:
        """観測用ダッシュボードへ足すタブを返す。タブを出さないなら `None`。

        1 拡張あたりページは高々 1 つ。ハッシュ・API 接頭辞・静的 URL はこの拡張の
        `name` から導出されるため、経路を指定する引数は持たない（導出結果は
        `DashboardPageEntry` を参照）。

        ページを出す拡張は `dashboard_static_dir()` も返し、そのディレクトリ直下に
        `page.js` を置くこと（返さない場合はロード時に落ちる）。ページを出さずに
        `dashboard_public_routes()` だけを申告してもよい。
        """
        return None

    def dashboard_static_dir(self) -> Path | None:
        """ホストが `/static/ext/{name}/` に載せる静的ファイルのディレクトリを返す。

        認証の外側で配信されるため、機密を含むファイルを置かないこと。
        `dashboard_page()` を返すなら、このディレクトリ直下に `page.js` を置く。
        `page.js` は ES モジュールで、ホストが `import()` したあと `mount(el, ctx)` /
        `unmount()` を呼ぶ（`ctx` の中身はホストの契約）。
        """
        return None

    def dashboard_routes(self) -> list[DashboardRoute]:
        """ホストがセッション認証の内側へ足す HTTP ルートを返す。

        パスは `/api/{name}` 配下（`/api/{name}` 自身を含む）のみ許可し、外れた
        パスはロード時に fail-fast する。
        """
        return []

    def dashboard_public_routes(self) -> list[DashboardRoute]:
        """ホストが認証ミドルウェアの **外側** へ載せる HTTP ルートを返す。

        OAuth の戻り先のように、ブラウザがセッションなしで打つ公開エンドポイント用。
        パスは `/oauth/{name}` 配下（`/oauth/{name}` 自身を含む）のみ許可し、外れた
        パスはロード時に fail-fast する。

        **ここに載せたルートは誰でも叩ける。** ホスト前段のアクセス制御で公開
        コールバックだけを通す構成でも、`state` の検証など「その要求が自分の
        始めたフローのものか」の確認は拡張側の責任とする。認証が要る処理は
        `dashboard_routes()` へ置くこと。
        """
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
_dashboard_pages: list[DashboardPageEntry] = []
_dashboard_static_mounts: list[DashboardStaticMount] = []
_dashboard_routes: list[DashboardRoute] = []
_dashboard_public_routes: list[DashboardRoute] = []


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
    """`name` が設定済み・一意で、経路に使える形かつ予約名でないことを検証する。

    `name` はダッシュボードの URL パス・ハッシュ・静的ディレクトリ名へそのまま
    埋まるため、形の検査は経路を申告する拡張だけでなく全拡張に課す（`name` が
    後からダッシュボードへ出るようになったときに、名前の変更を強いられないため）。

    Raises:
        ValueError: `name` が未設定・空文字・非文字列の場合、`_EXTENSION_NAME_RE` に
            合わない場合、`RESERVED_EXTENSION_NAMES` の名前を使った場合、または
            重複している場合。
    """
    seen: set[str] = set()
    for ext in extensions:
        name = getattr(ext, "name", "")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(
                f"Extension {type(ext).__name__} must define a non-empty 'name'"
            )
        if not _EXTENSION_NAME_RE.match(name):
            raise ValueError(
                f"Extension name '{name}' must match {_EXTENSION_NAME_RE.pattern} "
                "(it is used as-is in dashboard URLs and hashes)"
            )
        if name in RESERVED_EXTENSION_NAMES:
            reserved = ", ".join(sorted(RESERVED_EXTENSION_NAMES))
            raise ValueError(
                f"Extension name '{name}' is reserved by the core (reserved: {reserved})"
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


def _validate_dashboard_routes(
    ext: Extension, method_name: str, prefix: str
) -> list[DashboardRoute]:
    """1 拡張分の HTTP ルート申告を検証して返す。

    パスは `prefix` 自身か、`prefix` の直下（`prefix + "/"` 始まり）のみ許可する。
    前方一致だけだと `name = "foo"` の拡張が `/api/foobar` を申告できてしまうため、
    区切りまで見る。

    Args:
        ext: 申告した拡張。
        method_name: 申告を返すメソッド名（`"dashboard_routes"` など）。
        prefix: 許可するパスの接頭辞（`"/api/google-oauth"` など）。

    Returns:
        検証済みの `DashboardRoute` のリスト（申告順）。

    Raises:
        ValueError: 戻り値がリストでない、要素が `DashboardRoute` でない、または
            パスが `prefix` 配下でない場合。
    """
    routes = getattr(ext, method_name)()
    if not isinstance(routes, (list, tuple)):
        raise ValueError(
            f"Extension '{ext.name}' must return a list from {method_name}(), "
            f"got {type(routes).__name__}"
        )
    validated: list[DashboardRoute] = []
    for route in routes:
        if not isinstance(route, DashboardRoute):
            raise ValueError(
                f"Extension '{ext.name}' must return DashboardRoute instances from "
                f"{method_name}(), got {type(route).__name__}"
            )
        if route.path != prefix and not route.path.startswith(f"{prefix}/"):
            raise ValueError(
                f"Extension '{ext.name}' declared dashboard route '{route.path}', "
                f"which is outside its allowed prefix '{prefix}'"
            )
        validated.append(route)
    return validated


def _collect_dashboard(
    extensions: list[Extension],
) -> tuple[
    list[DashboardPageEntry],
    list[DashboardStaticMount],
    list[DashboardRoute],
    list[DashboardRoute],
]:
    """全拡張のダッシュボード申告を集め、経路を導出して検証する。

    経路の導出規則はコアだけが持ち、ホストは戻り値を読むだけにする。並び順は
    ロード順（`LILLA_EXTENSIONS` の並び）。

    Args:
        extensions: ロード順に並んだ拡張のリスト。

    Returns:
        `(ページ一覧, 静的配信の一覧, セッションルート, 公開ルート)`。

    Raises:
        ValueError: `dashboard_page()` が `DashboardPage` でも `None` でもない場合、
            ページを出すのに `dashboard_static_dir()` を返さない場合、または
            申告したルートのパスが許可された接頭辞の外にある場合。
    """
    pages: list[DashboardPageEntry] = []
    mounts: list[DashboardStaticMount] = []
    routes: list[DashboardRoute] = []
    public_routes: list[DashboardRoute] = []

    for ext in extensions:
        page = ext.dashboard_page()
        if page is not None and not isinstance(page, DashboardPage):
            raise ValueError(
                f"Extension '{ext.name}' must return a DashboardPage or None from "
                f"dashboard_page(), got {type(page).__name__}"
            )

        static_dir = ext.dashboard_static_dir()
        if page is not None and static_dir is None:
            raise ValueError(
                f"Extension '{ext.name}' declares a dashboard page but no "
                f"dashboard_static_dir(); the page's '{DASHBOARD_PAGE_MODULE}' "
                "would be missing"
            )

        if page is not None:
            pages.append(DashboardPageEntry.from_page(ext.name, page))

        if static_dir is not None:
            directory = Path(static_dir)
            if not directory.is_dir():
                logger.warning(
                    "Dashboard static dir of extension '%s' does not exist: %s",
                    ext.name,
                    directory,
                )
            mounts.append(
                DashboardStaticMount(
                    name=ext.name,
                    url_prefix=_dashboard_static_url(ext.name),
                    directory=directory,
                )
            )

        routes.extend(
            _validate_dashboard_routes(
                ext, "dashboard_routes", _dashboard_api_prefix(ext.name)
            )
        )
        public_routes.extend(
            _validate_dashboard_routes(
                ext, "dashboard_public_routes", _dashboard_public_prefix(ext.name)
            )
        )

    return pages, mounts, routes, public_routes


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
            受け付ける契約バージョンでない場合、コア確定の `EnvConfig` フィールド名を
            提供した場合、`requires` の拡張が未ロードか
            自分より後ろに並んでいる場合、または誰も提供していない名前を
            `required_config_sections()` / `required_env_fields()` /
            `required_tool_context_keys()` が要求している場合、
            `locale_dirs()` のカタログが名前空間の規約に違反している場合、または
            ダッシュボードの申告（`name` の形・予約名・ルートのパス接頭辞・
            ページを出すのに静的ディレクトリが無い）が規約に違反している場合。
    """
    from lilla_core.core.config import core_env_field_names

    for ext in extensions:
        if not isinstance(ext, Extension):
            raise TypeError(
                f"Expected an Extension instance, got {type(ext).__name__}"
            )
    _validate_names(extensions)
    _validate_api_versions(extensions)
    _validate_requires(extensions)

    # 拡張の YAML セクションは `extensions:` の下に置かれるため、コア確定の
    # トップレベル節と同名でも衝突しない（拡張どうしの重複だけを落とす）。
    config_models = _merge_unique(extensions, "config_models", "config model")
    env_fields = _merge_unique(
        extensions, "env_fields", "env field", reserved=core_env_field_names()
    )
    _validate_required(
        extensions,
        "required_config_sections",
        "config section",
        set(config_models),
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
    dashboard_pages, static_mounts, dash_routes, public_routes = _collect_dashboard(
        extensions
    )
    _validate_message_catalogs(extensions)

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
    _dashboard_pages.clear()
    _dashboard_pages.extend(dashboard_pages)
    _dashboard_static_mounts.clear()
    _dashboard_static_mounts.extend(static_mounts)
    _dashboard_routes.clear()
    _dashboard_routes.extend(dash_routes)
    _dashboard_public_routes.clear()
    _dashboard_public_routes.extend(public_routes)

    _clear_message_catalog_cache()


def _validate_message_catalogs(extensions: list[Extension]) -> None:
    """これから登録する拡張の UI 文言カタログを全ロケール分組み立てて検証する。

    カタログは `t()` がキーを引くまで読まれないため、ここで一度組み立てておかないと
    名前空間の規約違反が最初の文言参照（＝メッセージ処理の最中）まで表面化しない。
    他の貢献キーの検証と同じくグローバルを書き換える前に行うので、失敗しても
    壊れた登録は残らない。`ui/messages.py` はこのモジュールを import するので、
    循環 import を避けて関数内で遅延 import する。

    Args:
        extensions: これから登録する `Extension` インスタンス（ロード順）。

    Raises:
        ValueError: 拡張のカタログのトップレベルキーがその拡張の `name` と異なる場合、
            または拡張名がコアのカタログのトップレベルキーと衝突している場合。
    """
    from lilla_core.ui import messages

    messages.validate_catalogs(get_locale_dirs(extensions))


def _clear_message_catalog_cache() -> None:
    """UI 文言カタログのキャッシュを捨てる。

    合成カタログは登録済み拡張の `locale_dirs()` に依存するため、登録内容が
    変わったら捨てる必要がある。`ui/messages.py` はこのモジュールを import
    するので、循環 import を避けて関数内で遅延 import する。
    """
    from lilla_core.ui import messages

    messages.clear_cache()


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


def get_tool_config_roots() -> list[tuple[str, Path]]:
    """全拡張が同梱するツール YAML のディレクトリを `(拡張名, ディレクトリ)` でロード順に返す。

    拡張名は、同じ stem を複数の拡張が同梱していたときのエラーメッセージに使う。
    """
    roots: list[tuple[str, Path]] = []
    for ext in _extensions:
        roots.extend((ext.name, Path(root)) for root in ext.tool_config_roots())
    return roots


def get_locale_dirs(
    extensions: list[Extension] | None = None,
) -> list[tuple[str, Path]]:
    """拡張が同梱する UI 文言カタログのディレクトリを `(拡張名, ディレクトリ)` でロード順に返す。

    拡張名は、カタログのトップレベルキーと突き合わせる名前空間の検査に使う。

    Args:
        extensions: 対象の拡張。`None` なら登録済みのものを使う。`set_extensions()` が
            登録前の検証で「これから登録する拡張」を渡す。

    Returns:
        `(拡張名, ディレクトリ)` のリスト。
    """
    targets = _extensions if extensions is None else extensions
    dirs: list[tuple[str, Path]] = []
    for ext in targets:
        dirs.extend((ext.name, Path(directory)) for directory in ext.locale_dirs())
    return dirs


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


def get_dashboard_pages() -> list[DashboardPageEntry]:
    """全拡張のダッシュボードページを、経路を導出済みの形でロード順に返す。

    ホストはこの一覧からナビとハッシュルーティングを組み立てる。経路の導出は
    コアに閉じているので、ホスト側で `name` から組み立て直さないこと。
    """
    return list(_dashboard_pages)


def get_dashboard_static_mounts() -> list[DashboardStaticMount]:
    """全拡張の静的ファイル配信先（URL 接頭辞とディレクトリ）をロード順に返す。"""
    return list(_dashboard_static_mounts)


def get_dashboard_routes() -> list[DashboardRoute]:
    """全拡張のセッション認証の内側に載せる HTTP ルートをロード順に返す。"""
    return list(_dashboard_routes)


def get_dashboard_public_routes() -> list[DashboardRoute]:
    """全拡張の認証の外側に載せる公開 HTTP ルートをロード順に返す。

    ホストはこれらを認証ミドルウェアの対象外として載せる。
    """
    return list(_dashboard_public_routes)


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
