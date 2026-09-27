from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, TypeVar
from zoneinfo import ZoneInfo

import yaml
from dotenv import dotenv_values, load_dotenv
from pydantic import BaseModel, ConfigDict, Field, create_model, field_validator, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from lilla_core.utils.resource_loader import SourceSpec, load_text_resources

logger = logging.getLogger(__name__)


def _apply_dotenv_to_os_environ() -> None:
    """`.env` の値を `os.environ` へ反映する（起動時に 1 度だけ呼ぶ）。

    LLM プロバイダーの API キー（`GROK_API_KEY` など、`api_key_env` で指定した
    名前）はプロバイダごとに変数名が異なるため `EnvConfig` のフィールドにせず、
    `api/llm_client.py` が `os.environ.get(provider.api_key_env)` で直接読む。
    そのままでは `.env` に書いても反映されないため、ここで `.env` の内容を
    `os.environ` へ書き戻す。既に `os.environ` にある値は上書きしない
    （python-dotenv の既定動作）。`EnvConfig` の各フィールド（`DISCORD_TOKEN` など）は
    引き続き `EnvConfigSettingsSource` が個別に解決するため、ここでの反映と
    重複しても副作用はない。
    """
    load_dotenv(".env", override=False)


_apply_dotenv_to_os_environ()


#: `LlmProviderConfig.type` のうち、実際に LLM へ HTTP を出す具体プロバイダーの種別。
CONCRETE_LLM_PROVIDER_TYPES = ("ollama", "openai_compat")


class LlmProviderConfig(BaseModel):
    """LLMプロバイダーの設定モデル。

    `type` が `ollama` / `openai_compat` のエントリは具体プロバイダーで、`url` /
    `model` が必須。`resolver` のエントリは回しごとに具体プロバイダー名を選ぶ
    スクリプト（`script`）と、選べなかったときの落とし先（`fallback`）を持ち、
    自身は HTTP を出さない（`url` / `model` などは書かない）。
    """

    type: Literal["ollama", "openai_compat", "resolver"]
    url: str | None = None
    model: str | None = None
    # ollama タイプのみ
    wakeup_file: str | None = None
    wol_sleep_seconds: int = 30
    # openai_compat タイプのみ
    api_key_env: str | None = None
    # プロバイダー固有の追加パラメータ（POSTボディのトップレベルに展開される）
    extra_params: dict[str, Any] = {}
    # resolver タイプのみ
    # `resolve(ctx)` を定義した Python ファイルのパス（`${config_root}` 展開・
    # `file:` 接頭は任意。相対パスは `CONFIG_ROOT` 基準。`CONFIG_ROOT` 配下のみ）
    script: str | None = None
    # `resolve` が具体名を返せなかったときに使う具体プロバイダー名
    fallback: str | None = None
    # `async def resolve` の待ち時間の上限（秒）。超えたら `fallback` へ落とす
    timeout_seconds: float = 10.0

    @model_validator(mode="after")
    def _validate_fields_for_type(self) -> "LlmProviderConfig":
        """`type` ごとに必須・不可の項目が揃っていることを検証する。

        具体プロバイダーは `url` / `model` が必須で `script` / `fallback` を持たない。
        resolver は `script` / `fallback` が必須で、HTTP を出さないため `url` /
        `model` / `api_key_env` / `wakeup_file` / `extra_params` を持たない。
        """
        if self.type == "resolver":
            missing = [name for name in ("script", "fallback") if not getattr(self, name)]
            if missing:
                raise ValueError(
                    f"llm provider of type 'resolver' requires: {', '.join(missing)}"
                )
            unexpected = [
                name
                for name in ("url", "model", "api_key_env", "wakeup_file")
                if getattr(self, name) is not None
            ]
            if self.extra_params:
                unexpected.append("extra_params")
            if unexpected:
                raise ValueError(
                    "llm provider of type 'resolver' must not set: " + ", ".join(unexpected)
                )
            if self.timeout_seconds <= 0:
                raise ValueError("llm provider timeout_seconds must be positive")
        else:
            missing = [name for name in ("url", "model") if not getattr(self, name)]
            if missing:
                raise ValueError(
                    f"llm provider of type '{self.type}' requires: {', '.join(missing)}"
                )
            unexpected = [name for name in ("script", "fallback") if getattr(self, name) is not None]
            if unexpected:
                raise ValueError(
                    f"llm provider of type '{self.type}' must not set: {', '.join(unexpected)}"
                )
        return self


class DiscordChannelConfig(BaseModel):
    """lilla.yaml の `discord.channels:` に並べる 1 チャンネル分の登録エントリ。

    `name` は設定上の別名で、Discord 側の現在のチャンネル名と一致していなくてよい
    （リネームされても設定を追随させずに済む）。`channel_id` は既存の
    `approval_channel_id` などと同じ snowflake 文字列で、YAML では引用符で囲むこと。
    """

    model_config = ConfigDict(extra="ignore")

    name: str
    channel_id: str
    # メンションなしでも会話を始めてよいチャンネルかどうか。既定は `False` で、
    # 登録しただけでは受信条件は現行（メンション or DM）のまま変わらない。
    mention_optional: bool = False

    @field_validator("name", "channel_id")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        """前後の空白を除いた値を返し、空になるものは起動時に落とす。

        名前解決は「前後空白を除いた完全一致（大文字小文字は区別する）」で行うため、
        突き合わせの基準を揃えるべく保持する値の側を正規化しておく。
        """
        stripped = value.strip()
        if not stripped:
            raise ValueError("discord.channels entries must have a non-empty name and channel_id")
        return stripped


class DiscordConfig(BaseModel):
    """lilla.yaml の `discord:` セクション（コア確定分）。"""

    # コア確定のセクション名は予約済みで、拡張がここへ型付きのフィールドを足すことは
    # できない（`compose_config()` が重複として落とす）。`extra="ignore"` は、利用側が
    # YAML に書いた未知のキーで起動が落ちないようにするためのもの。
    model_config = ConfigDict(extra="ignore")

    # リラはオーナー専用の個人アシスタントという前提であり、オーナー判定ができない
    # 状態（未設定）のまま動き続けること自体が思想と矛盾するため、デフォルト値を
    # 持たせず起動時に fail-fast させる。
    my_user_id: str
    error_channel_id: str | None = None
    approval_channel_id: str | None = None
    # 入口の作法を変えたいチャンネルの登録リスト。未設定・空リストなら受信動作は
    # 現行のまま（メンション or DM）で、会話履歴は登録の有無によらず全チャンネル
    # 横断のままにする。
    channels: list[DiscordChannelConfig] = []

    @model_validator(mode="after")
    def _validate_unique_channels(self) -> "DiscordConfig":
        """`discord.channels` の `name` / `channel_id` の重複を起動時に落とす。

        重複を許すと「どちらのエントリが効いているか」が並び順に依存して見えなく
        なるため、静かな先勝ちにせず fail-fast させる。
        """
        for label, values in (
            ("name", [entry.name for entry in self.channels]),
            ("channel_id", [entry.channel_id for entry in self.channels]),
        ):
            duplicates = sorted({value for value in values if values.count(value) > 1})
            if duplicates:
                raise ValueError(
                    f"discord.channels has duplicate {label}: {', '.join(duplicates)}"
                )
        return self

    def find_channel_by_id(self, channel_id: str | int) -> DiscordChannelConfig | None:
        """`channel_id` に一致する登録チャンネルを返す（無ければ `None`）。

        Discord 側の ID は int、設定側は snowflake 文字列で持つため、文字列へ
        揃えたうえで比較する。

        Args:
            channel_id: 探す Discord チャンネル ID（int / str のどちらでもよい）。

        Returns:
            一致した登録エントリ。登録が無ければ `None`。
        """
        key = str(channel_id).strip()
        for entry in self.channels:
            if entry.channel_id == key:
                return entry
        return None

    def find_channel_by_name(self, name: str) -> DiscordChannelConfig | None:
        """設定上の別名に一致する登録チャンネルを返す（無ければ `None`）。

        突き合わせは前後空白を除いた完全一致で、大文字小文字は区別する。

        Args:
            name: 探す登録チャンネルの `name`。

        Returns:
            一致した登録エントリ。登録が無ければ `None`。
        """
        key = name.strip()
        for entry in self.channels:
            if entry.name == key:
                return entry
        return None


class PathsConfig(BaseModel):
    """lilla.yaml の `paths:` セクション。"""

    # ツールの `.py` を探すルート。ロードを許す範囲は「このルート + 拡張の `tool_roots()`
    # + `config_root/tools`」から `loaders/tool_paths.py` が導き、別途のホワイトリスト設定
    # は持たない（旧 `allowed_tool_paths` は読まず、YAML にあっても無視する）。
    tool_root: Path = Path("/app/tools")


class MongodbConfig(BaseModel):
    """lilla.yaml の `mongodb:` セクション（接続 URI は `env` 側）。"""

    db_name: str = "lilla"


class ProxyConfig(BaseModel):
    """lilla.yaml の `proxy:` セクション。"""

    http: str | None = None
    https: str | None = None
    no_proxy: str | None = None

    def resolve_url(self) -> str | None:
        """実際に使うプロキシ URL を返す（https を優先し、無ければ http）。"""
        return self.https or self.http


class PromptConfig(BaseModel):
    """lilla.yaml の `prompt:` セクション（コア確定分）。"""

    # コア確定のセクション名は予約済みで、拡張がここへ型付きのフィールドを足すことは
    # できない（`compose_config()` が重複として落とす）。`extra="ignore"` は、利用側が
    # YAML に書いた未知のキーで起動が落ちないようにするためのもの。
    model_config = ConfigDict(extra="ignore")

    system: SourceSpec | None = None
    conversation: SourceSpec | None = None
    discord: SourceSpec | None = None


class BotConfig(BaseModel):
    """lilla.yaml の `bot:` セクション。"""

    # 履歴系（max_history_turns / conversation_ttl_hours）の正は `memory:` のため、
    # `bot:` に残っている名残キーは読まずに無視する。
    model_config = ConfigDict(extra="ignore")

    log_ttl_hours: dict[str, int] = {"debug": 72, "info": 240, "warning": 720, "error": 720}
    # Discord の画像添付として受け付ける最大サイズ（MB）。一般的なスマートフォンの
    # 撮影写真（2〜8MB 程度）は通らせつつ、巨大画像によるメモリ圧迫と LLM
    # リクエストの失敗を防ぐための既定値。
    max_image_attachment_size_mb: int = 8


class MemoryConfig(BaseModel):
    """lilla.yaml の `memory:` セクション。"""

    max_history_turns: int = 30
    conversation_ttl_hours: int = 72
    history_days: int = 2
    session_memory_ttl_hours: int = 3


class ToolsConfig(BaseModel):
    """lilla.yaml の `tools:` セクション。"""

    main_available_tools: list[str] | None = None


class MongodataCommandConfig(BaseModel):
    """lilla.yaml の `commands.mongodata:` セクション。"""

    allowed_collections: list[str] = []


class CommandsConfig(BaseModel):
    """lilla.yaml の `commands:` セクション。"""

    mongodata: MongodataCommandConfig = MongodataCommandConfig()


class DashboardConfig(BaseModel):
    """lilla.yaml の `dashboard:` セクション（観測用ダッシュボードの HTTP サーバー）。

    全フィールドに既定があるため、`lilla.yaml` に節そのものが無くてもよい
    （既定のまま `0.0.0.0:8765` で起動する）。
    """

    #: listen するアドレス。既定は全インターフェース（コンテナ運用が前提）。
    #: **このポートはパスワード認証つきの管理画面を開く。** パスワード未登録の
    #: 間は `POST /api/setup` に先に到達した者が管理者パスワードを決められる
    #: ブートストラップなので、公開ネットワークへ晒さないこと。同一ホストからしか
    #: 使わない運用では `127.0.0.1` に絞れる（`SECURITY.md` 参照）。
    host: str = "0.0.0.0"
    #: listen するポート。
    port: int = 8765
    #: ログインセッション Cookie に `Secure` 属性を付けるか。HTTPS / 前段の
    #: アクセス制御を前提とした安全側の既定。LAN 内で `http://<host>:8765` へ
    #: 直接アクセスする運用では `false` にしないと、ログインはできても Cookie が
    #: ブラウザから送信されずログイン状態を維持できない。
    cookie_secure: bool = True


class ExtensionsConfig(BaseModel):
    """lilla.yaml の `extensions:` セクション（拡張が申告した節の置き場）。

    コア単体ではフィールドを 1 つも持たず、`compose_config()` が拡張の
    `config_model()` の申告をこのモデルのサブクラスへ足す（キーは各拡張の `name` から
    導いた節名。`extension_section_name()`）。拡張の節をコア確定の
    トップレベル節と別の名前空間に置くことで、コアが後から節を確定しても拡張側の
    名前と衝突しない。読み出しは `get_config().extensions.<節名>`（型付きなら
    `get_section()`）。

    コア確定の他の節と違い、未知のキーは無視せず起動時に落とす。ロードしていない
    拡張の節（拡張を外したあとの払い残し）を黙って残さないため。
    """

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _treat_null_as_empty(cls, data: Any) -> Any:
        """`extensions:` だけ書いて中身が空（YAML の `null`）の場合を空の節として扱う。"""
        return {} if data is None else data


class UiConfig(BaseModel):
    """lilla.yaml の `ui:` セクション。

    Discord に見せる文言のロケールと、アプリが「人間側の今日 / いま」として
    扱うタイムゾーンを決める。

    ロケールはカタログ（`lilla_core/locales/{locale}.yaml`）に無い名前を指定しても
    起動は落とさず、`lilla_core.ui.messages.t()` が既定ロケールへフォールバックする。

    タイムゾーンは IANA 名（`Asia/Tokyo` など）の文字列、または未指定。未指定なら
    実行環境の OS のローカルタイムゾーンに従う。ロケールと異なり、受け付けられない
    名前や空文字はフォールバックせず起動時に失敗する（気付かないまま日付が
    ずれた状態で動き続けるのを防ぐため）。
    """

    locale: str = "ja"
    timezone: str | None = None

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str | None) -> str | None:
        """`ui.timezone` が IANA タイムゾーン名として解決できることを検証する。

        `None`（キーなし / YAML の `null`）はそのまま通し、OS のローカル
        タイムゾーンを使う意味になる。空文字や `ZoneInfo` が受け付けない名前は
        `ValueError` を送出して起動時に失敗させる。
        """
        if value is None:
            return None
        if not value.strip():
            raise ValueError("ui.timezone must not be empty; omit the key to follow the OS timezone")
        try:
            ZoneInfo(value)
        except Exception as e:
            raise ValueError(f"ui.timezone is not a valid IANA timezone name: {value!r}") from e
        return value


class LlmConfig(BaseModel):
    """lilla.yaml の `llm:` セクション。"""

    default: str = "ollama-gemma3"
    providers: dict[str, LlmProviderConfig] = {}
    max_tool_call_iterations: int = 10

    @model_validator(mode="after")
    def _validate_default_provider_exists(self) -> "LlmConfig":
        """`default` が `providers` のキーに存在すること、resolver の `fallback` が
        台帳にある具体プロバイダー（resolver 以外）を指すことを検証する。

        `llm:` セクション自体を省略した場合もクラスデフォルト
        （`default="ollama-gemma3"`, `providers={}`）に対してこの検証が走り、
        意図どおり起動時に `ValidationError` となる。
        """
        if self.default not in self.providers:
            available = ", ".join(sorted(self.providers)) or "(none)"
            raise ValueError(
                f"llm.default '{self.default}' is not defined in llm.providers. "
                f"Available providers: {available}"
            )
        for name, provider in self.providers.items():
            if provider.type != "resolver":
                continue
            target = self.providers.get(provider.fallback)
            if target is None:
                raise ValueError(
                    f"llm.providers.{name}.fallback '{provider.fallback}' is not defined in llm.providers"
                )
            if target.type == "resolver":
                raise ValueError(
                    f"llm.providers.{name}.fallback '{provider.fallback}' must be a concrete provider, "
                    "not a resolver"
                )
        return self


def resolve_llm_resolver_script_path(spec: str, config_root: str | Path) -> Path:
    """resolver 型プロバイダーの `script` を実ファイルパスへ解決する。

    書き方は資源パスの解決（`utils/resource_loader.py`）に合わせ、`${config_root}` を
    展開し、`file:` 接頭は付けても付けなくてもよい。スクリプトは単一ファイルなので
    `dir:` は受け付けない。相対パスは `config_root` 基準で解決する。

    `CONFIG_ROOT` は拡張モジュールと同じ信頼レベル（`SECURITY.md`）のため、解決後の
    パスが `config_root` の外を指す場合（`..` や外を指すシンボリックリンク）は落とす。

    Args:
        spec: `lilla.yaml` に書かれた `script` の値。
        config_root: `${config_root}` 展開と相対パスの基準に使うディレクトリ。

    Returns:
        シンボリックリンクを解決した絶対パス（存在確認はしない）。

    Raises:
        ValueError: `dir:` 指定、または `config_root` の外を指す場合。
    """
    root = Path(config_root).resolve()
    expanded = spec.replace("${config_root}", str(config_root)).strip()
    if expanded.startswith("dir:"):
        raise ValueError(f"llm resolver script must be a single file, not dir: {spec!r}")
    if expanded.startswith("file:"):
        expanded = expanded[len("file:"):]
    path = Path(expanded)
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(
            f"llm resolver script must be under CONFIG_ROOT ({root}): {spec!r}"
        )
    return resolved


class EnvConfig(BaseModel):
    """`.env` / OS 環境変数から読み込むコア設定（`cfg.env`）。"""

    discord_token: str
    mongodb_uri: str = "mongodb://localhost:27017"
    config_root: Path = Path("/app/config")
    http_proxy_user: str | None = None
    http_proxy_pass: str | None = None


class YamlConfigSettingsSource(PydanticBaseSettingsSource):
    """lilla.yaml をネスト構造のまま読み込むカスタム設定ソース。"""

    def __init__(self, settings_cls: type[BaseSettings], yaml_file: Path) -> None:
        super().__init__(settings_cls)
        self._yaml_file = yaml_file
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        """YAML ファイルを読み込み、セクション名をキーとした辞書をそのまま返す。"""
        if not self._yaml_file.exists():
            return {}
        with open(self._yaml_file, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        # `env:` は `.env` / OS 環境変数専用のセクションであり、YAML からは
        # 読み込まない（秘匿情報が構造設定側へ紛れ込むのを防ぐ）。
        if "env" in raw:
            logger.warning(
                "Not loading the `env:` section from lilla.yaml (use .env / environment variables instead): %s",
                self._yaml_file,
            )
            raw = {k: v for k, v in raw.items() if k != "env"}
        return raw

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        """指定フィールドの値を返す。"""
        return self._data.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        """YAML から読み込んだ設定値をすべて返す。"""
        return self._data


#: 拡張が `env_fields()` で申告した秘匿フィールドの「フィールド名 -> OS 環境変数名」。
#: `compose_config()` が起動時に書き換え、`EnvConfigSettingsSource` がコア確定の
#: `_VAR_NAMES` へ重ねて読む。合成した `AppConfig` のクラス属性として持たせない
#: のは、pydantic のモデル本体に置いたアンダースコア始まりの属性がプライベート属性
#: として扱われ、`settings_customise_sources()` から素直に読めないため。
_extra_env_var_names: dict[str, str] = {}


class EnvConfigSettingsSource(PydanticBaseSettingsSource):
    """`.env` / OS 環境変数から `env` セクションを組み立てるカスタム設定ソース。

    OS 変数名は従来のまま（`DISCORD_TOKEN` など）で、`ENV__` プレフィックスは
    使わない。ここに列挙した変数と、拡張が `env_fields()` で申告した変数だけが
    `cfg.env` に流れ、YAML 由来の項目を環境変数で上書きする経路は持たない。
    """

    _VAR_NAMES = {
        "discord_token": "DISCORD_TOKEN",
        "mongodb_uri": "MONGODB_URI",
        "config_root": "CONFIG_ROOT",
        "http_proxy_user": "HTTP_PROXY_USER",
        "http_proxy_pass": "HTTP_PROXY_PASS",
    }

    def __init__(self, settings_cls: type[BaseSettings], env_file: Any) -> None:
        super().__init__(settings_cls)
        self._data: dict[str, Any] = self._load(env_file)

    def _resolve_var_names(self) -> dict[str, str]:
        """このソースが読む「フィールド名 -> OS 環境変数名」を返す。

        コア確定の `_VAR_NAMES` に、拡張が申告して `compose_config()` が登録した
        分（`_extra_env_var_names`）を重ねる。名前の衝突はロード時に fail-fast
        済みのため、ここでの上書きは起きない。
        """
        return {**self._VAR_NAMES, **_extra_env_var_names}

    def _load(self, env_file: Any) -> dict[str, Any]:
        """OS 環境変数（優先）と `.env` から `{"env": {...}}` を組み立てる。"""
        file_values: dict[str, str | None] = {}
        if env_file and Path(env_file).is_file():
            file_values = dotenv_values(env_file, encoding="utf-8")

        values: dict[str, Any] = {}
        for field_name, var_name in self._resolve_var_names().items():
            raw = os.environ.get(var_name, file_values.get(var_name))
            if raw is not None:
                values[field_name] = raw
        return {"env": values} if values else {}

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        """指定フィールドの値を返す。"""
        return self._data.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        """環境変数から読み込んだ `env` セクションを返す。"""
        return self._data


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- `.env` / OS 環境変数から読み込む設定 ---
    env: EnvConfig

    # --- lilla.yaml から読み込む構造設定 ---
    discord: DiscordConfig
    paths: PathsConfig = PathsConfig()
    mongodb: MongodbConfig = MongodbConfig()
    proxy: ProxyConfig = ProxyConfig()
    prompt: PromptConfig = PromptConfig()
    bot: BotConfig = BotConfig()
    memory: MemoryConfig = MemoryConfig()
    tools: ToolsConfig = ToolsConfig()
    commands: CommandsConfig = CommandsConfig()
    dashboard: DashboardConfig = DashboardConfig()
    # `LlmConfig()` を直接デフォルト値にすると、クラス定義（モジュール import）の
    # 時点で即座にインスタンス化・検証されてしまい、YAML の内容に関わらず
    # import だけで落ちる。`default_factory` で AppConfig 構築時まで遅延させる。
    llm: LlmConfig = Field(default_factory=LlmConfig)
    ui: UiConfig = UiConfig()
    # 拡張が申告した節の置き場。中身は `compose_config()` が合成する（拡張 0 個なら空）。
    extensions: ExtensionsConfig = Field(default_factory=ExtensionsConfig)

    @model_validator(mode="before")
    @classmethod
    def _reject_extension_sections_at_top_level(cls, data: Any) -> Any:
        """拡張が申告した節がトップレベルに書かれていたら起動時に落とす。

        トップレベルの未知キーは `extra="ignore"` で黙って捨てられるため、
        `extensions:` の下へ移し忘れた節は、全フィールドに既定値があるモデルだと
        既定値のまま気付かれずに起動してしまう。合成済みモデル（`cls`）が持つ
        拡張の節名と突き合わせて検出する。コア確定の節と同名の拡張節は
        トップレベル側がコアのものなので対象外。
        """
        if not isinstance(data, dict):
            return data
        extension_sections = cls.model_fields["extensions"].annotation.model_fields
        misplaced = sorted(
            key
            for key in data
            if key in extension_sections and key not in AppConfig.model_fields
        )
        if misplaced:
            raise ValueError(
                "Extension config sections must be placed under 'extensions:' in lilla.yaml, "
                f"but found at the top level: {', '.join(misplaced)}"
            )
        return data

    @model_validator(mode="after")
    def _validate_llm_resolver_scripts(self) -> "AppConfig":
        """resolver 型プロバイダーの `script` が `CONFIG_ROOT` 配下の実在ファイルか検証する。

        `resolve` 関数の有無（モジュールの import が要る）はここでは見ず、起動時に
        `services/llm_resolver.py` の `validate_llm_resolvers()` が確かめる。
        """
        for name, provider in self.llm.providers.items():
            if provider.type != "resolver":
                continue
            try:
                path = resolve_llm_resolver_script_path(provider.script, self.env.config_root)
            except ValueError as e:
                raise ValueError(f"llm.providers.{name}.script: {e}") from e
            if not path.is_file():
                raise ValueError(f"llm.providers.{name}.script does not exist: {path}")
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """設定ソースの優先順位を定義する（高い順）: init > env > yaml > file_secret。

        YAML ファイルの場所（`CONFIG_ROOT`）は `EnvConfigSettingsSource` が
        OS 環境変数 / `.env` から解決した結果（`env.config_root`）と揃える。
        ここで `AppConfig()` や `get_config()` を呼び出すと循環するため、
        `EnvConfigSettingsSource` を先に 1 つ組み立て、その結果だけを使って
        YAML パスを決めたうえで、同じインスタンスをソース列に載せる。
        """
        env_settings_source = EnvConfigSettingsSource(settings_cls, cls.model_config.get("env_file"))
        config_root = Path(env_settings_source().get("env", {}).get("config_root", "/app/config"))
        yaml_file = config_root / "lilla.yaml"
        return (
            init_settings,
            env_settings_source,
            YamlConfigSettingsSource(settings_cls, yaml_file),
            file_secret_settings,
        )

    def _load_prompt(self, override: SourceSpec | None, subdir: str) -> str:
        """プロンプトを読み込む共通処理。

        override（各 prompt 設定）が指定されていればそのパスを使用し、未設定の
        場合は {config_root}/prompt/{subdir} を既定パスとして使用する。いずれも
        load_text_resources に委譲するため、${config_root} の置換や、ディレクトリ・
        ファイルが存在しない場合に空文字列を返す挙動は同ユーティリティに従う。
        """
        src = override or f"dir:{self.env.config_root}/prompt/{subdir}"
        return load_text_resources(src)

    @property
    def system_prompt(self) -> str:
        """システムプロンプトディレクトリから読み込んだシステムプロンプトを返す。

        prompt.system が設定されている場合はそのパスを使用し、${config_root} を
        env.config_root の値で置換する。未設定の場合は {config_root}/prompt/system を使用する。
        """
        return self._load_prompt(self.prompt.system, "system")

    @property
    def conversation_prompt(self) -> str:
        """会話プロンプトディレクトリから読み込んだプロンプトを返す。

        prompt.conversation が設定されている場合はそのパスを使用し、${config_root} を
        env.config_root の値で置換する。未設定の場合は {config_root}/prompt/conversation を使用する。
        ディレクトリが存在しない・ファイルが存在しない場合は空文字列を返す（load_text_resources の既存動作）。
        """
        return self._load_prompt(self.prompt.conversation, "conversation")

    @property
    def discord_client_prompt(self) -> str:
        """Discord クライアント固有のプロンプトディレクトリから読み込んだプロンプトを返す。

        prompt.discord が設定されている場合はそのパスを使用し、${config_root} を
        env.config_root の値で置換する。未設定の場合は {config_root}/prompt/discord を使用する。
        ディレクトリが存在しない・ファイルが存在しない場合は空文字列を返す。
        """
        return self._load_prompt(self.prompt.discord, "discord")

    def get_llm_provider(self, name: str | None = None) -> LlmProviderConfig:
        """LLMプロバイダー設定を取得する。

        name=None の場合は llm.default で指定したプロバイダーを返す。
        存在しない名前の場合は ValueError を raise する。
        """
        provider_name = name or self.llm.default
        if provider_name not in self.llm.providers:
            raise ValueError(f"LLM provider '{provider_name}' does not exist in the configuration")
        return self.llm.providers[provider_name]


_config_instance: AppConfig | None = None


def set_config(instance: AppConfig) -> None:
    """アプリ全体で共有する設定インスタンスを差し込む。

    通常は `load_extensions()` が `compose_config()` の結果をこの関数で据える。
    拡張モジュールが import 副作用として自前のサブクラスを差し込んでも、
    そのあとの合成結果で上書きされるため、設定の差分は `Extension.config_model()`
    / `Extension.env_fields()` から出すこと。テスト用途では上書き可
    （`_config_instance` の直接リセットも可）。
    """
    global _config_instance
    _config_instance = instance


class _UncomposedExtensionsConfig(ExtensionsConfig):
    """合成を経ずに組んだ設定（`_default_config()`）の `extensions:` 節。

    どの拡張が載るかを知らないまま組むため、`extensions:` の下の中身は検証せずに
    捨てる（`ExtensionsConfig` の未知キー検査は、申告を突き合わせる合成時だけ）。
    """

    model_config = ConfigDict(extra="ignore")


class _UncomposedAppConfig(AppConfig):
    """`load_extensions()` を経ずに `get_config()` が組む設定。

    運用スクリプトなど拡張をロードしないプロセスでも、`extensions:` を書いた
    `lilla.yaml` からコア確定の節を読めるようにする。拡張の節は読めない。
    """

    extensions: _UncomposedExtensionsConfig = Field(
        default_factory=_UncomposedExtensionsConfig
    )


@lru_cache(maxsize=1)
def _default_config() -> AppConfig:
    """`set_config()` が一度も呼ばれなかった場合、合成前の設定を組み立てて返す。

    `load_extensions()` を経ていない（＝どの拡張が載るか分からない）ため、
    `extensions:` の下の中身は検証せず空として扱う。起動時の検証（未知キーで
    落とす）は `compose_config()` の結果にだけ掛かる。
    """
    return _UncomposedAppConfig()


def get_config() -> AppConfig:
    """アプリ全体で共有する設定インスタンスを返す。

    NOTE: この関数はモジュールレベル（import 時に実行される場所）ではなく、
    必ず呼び出されるタイミング（関数内）で呼ぶこと。モジュールレベルで
    `cfg = get_config()` のように書くと、その時点のインスタンスを固定して
    しまい、後から `set_config()` されても反映されなくなる。

    型ヒント上は `AppConfig` を返すが、実体は `compose_config()` が組んだ
    サブクラスのインスタンスで、拡張が申告したセクションは `extensions` の下に
    属性として持つ（`get_config().extensions.<節名>`。ダックタイピング）。
    拡張のセクションを静的な型付きで読みたい場合は `get_section()` を使う。
    """
    if _config_instance is not None:
        return _config_instance
    return _default_config()


_SectionT = TypeVar("_SectionT", bound=BaseModel)


def extension_section_name(extension_name: str) -> str:
    """拡張の `name` から、その拡張の `extensions:` 下の節名を導く。

    ハイフンをアンダースコアに置き換えるだけ（`google-oauth` → `google_oauth`。
    ハイフンを含まない名前はそのまま）。拡張は節名を自分では申告しない。
    すでに節名の形（アンダースコア区切り）の文字列を渡しても同じ値を返す。
    """
    return extension_name.replace("-", "_")


def get_section(name: str, model: type[_SectionT], config: AppConfig | None = None) -> _SectionT:
    """合成済み設定の `extensions:` から拡張のセクションを取り出し、申告したモデルの型で返す。

    拡張が `Extension.config_model()` で申告したセクションは
    `get_config().extensions.<節名>` で読めるが、`get_config()` の戻り値の型は
    `AppConfig` のため静的には見えない。本関数は拡張の `name`（または節名）と
    モデルを受け取り、実際の値がそのモデルのインスタンスであることを検証したうえで
    型付きで返す（`get_section("google-oauth", GoogleConfig).client_id`）。

    探すのは `extensions:` の下だけで、コア確定のトップレベル節（`ui` など）は
    対象外。コアの節は `AppConfig` に型付きで定義済みのため `get_config().ui` で読む
    （拡張がコアと同名の節を申告できるため、両方を探すと名前が 2 か所を指しうる）。

    Args:
        name: 拡張の `name`（`google-oauth`）。`extension_section_name()` で節名へ
            直すので、節名（`google_oauth`）をそのまま渡してもよい。
        model: そのセクションのモデルクラス。
        config: 読み出す設定。`None` なら `get_config()`。

    Returns:
        `model` のインスタンス。

    Raises:
        ValueError: セクションが `extensions:` に存在しない（申告漏れ・名前違い）、
            または実際の値が `model` のインスタンスでない場合。
    """
    cfg = config if config is not None else get_config()
    section = extension_section_name(name)
    extensions = cfg.extensions
    if section not in type(extensions).model_fields:
        raise ValueError(
            f"Config section 'extensions.{section}' is not declared "
            "(declare it via Extension.config_model() or check the name)"
        )
    value = getattr(extensions, section)
    if not isinstance(value, model):
        raise ValueError(
            f"Config section 'extensions.{section}' is a {type(value).__name__}, "
            f"not {model.__name__}"
        )
    return value


def core_config_section_names() -> frozenset[str]:
    """コアが確定済みの YAML トップレベル節名（`AppConfig` のフィールド名）を返す。

    `env` と `extensions` も含む。拡張の節は `extensions:` の下に置かれるため、
    拡張が同じ名前の節を申告しても衝突しない。
    """
    return frozenset(AppConfig.model_fields)


def core_env_field_names() -> frozenset[str]:
    """コアが確定済みの `EnvConfig` フィールド名を返す。

    拡張が提供する秘匿フィールド名の検査に使う。
    """
    return frozenset(EnvConfig.model_fields)


def _validate_identifier(name: str, label: str) -> None:
    """合成に使う名前が Python の識別子として妥当であることを検証する。

    アンダースコア始まりを弾くのは、pydantic がモデル本体のアンダースコア始まりの
    属性をプライベート属性として扱い、フィールドにならないため。

    Raises:
        ValueError: 識別子でない、またはアンダースコアで始まる場合。
    """
    if not name.isidentifier() or name.startswith("_"):
        raise ValueError(
            f"Invalid {label} name: {name!r} "
            "(must be a valid Python identifier not starting with '_')"
        )


def _section_field(model: type[BaseModel]) -> tuple[type[BaseModel], Any]:
    """YAML セクション 1 つ分のフィールド定義（型, デフォルト）を組み立てる。

    セクションモデルの全フィールドにデフォルトがあれば、そのセクションは
    `lilla.yaml` に無くてもよい（`default_factory` で空のモデルを組む）。必須
    フィールドを 1 つでも持つ場合はセクション自体を必須にして、YAML に無ければ
    起動時に落とす。「必須項目のあるセクションを書き忘れたまま起動する」ことを
    防ぐための既定で、必須かどうかを拡張が個別に申告する経路は持たない。
    """
    if any(field.is_required() for field in model.model_fields.values()):
        return (model, ...)
    return (model, Field(default_factory=model))


def compose_config(
    config_models: dict[str, type[BaseModel]],
    env_fields: dict[str, str],
) -> AppConfig:
    """拡張が申告した差分を `AppConfig` へ合成し、組み立てたインスタンスを返す。

    `core/extension.py` の `load_extensions()` が、拡張の貢献をマージして
    衝突を検証したあとに一度だけ呼ぶ。`extension.py` が pydantic の組み立て
    詳細を知らずに済むよう、合成そのものはこのモジュールに閉じている。

    合成されるのは次の 2 つ。

    - YAML セクション: `ExtensionsConfig` を基盤に `pydantic.create_model` で追加し、
      それを `AppConfig` の `extensions` フィールドの型に据える（トップレベルには
      足さない）。セクションが必須かどうかは `_section_field()` がモデルから導出し、
      必須のセクションが 1 つでもあれば `extensions:` 自体も必須になる
    - 秘匿フィールド: `EnvConfig` へ同様に追加し、OS 変数名のマッピング
      （`_extra_env_var_names`）も合わせて延ばす。型は常に `str | None`
      （既定値 `None`）で、必須フィールドや非文字列は表現できない

    Args:
        config_models: `extensions:` 下の節名 -> セクションモデル（`load_extensions()` は
            `extension.get_config_models()` の、各拡張の `name` から導いた節名を渡す）。
        env_fields: `EnvConfig` に足すフィールド名 -> OS 環境変数名。

    Returns:
        合成済みモデルのインスタンス。どちらの申告も空なら素の `AppConfig`。

    Raises:
        ValueError: セクション名・フィールド名が識別子として妥当でない場合。
        pydantic.ValidationError: 合成後のモデルで設定の検証に失敗した場合。
    """
    for name in config_models:
        _validate_identifier(name, "config section")
    for name in env_fields:
        _validate_identifier(name, "env field")

    # モデルの構築より先に登録する。`EnvConfigSettingsSource` はインスタンス化
    # （＝設定ソースが走るタイミング）にこのレジストリを読むため。
    _extra_env_var_names.clear()
    _extra_env_var_names.update(env_fields)
    # 拡張の import 中に `get_config()` が踏まれていると、合成前の素の
    # `AppConfig` がキャッシュに残り続けるため捨てる。
    _default_config.cache_clear()

    if not config_models and not env_fields:
        return AppConfig()

    env_model: type[EnvConfig] = EnvConfig
    if env_fields:
        env_model = create_model(
            "ComposedEnvConfig",
            __base__=EnvConfig,
            **{name: (str | None, None) for name in env_fields},
        )

    field_definitions: dict[str, Any] = {"env": (env_model, ...)}
    if config_models:
        extensions_model = create_model(
            "ComposedExtensionsConfig",
            __base__=ExtensionsConfig,
            **{section: _section_field(model) for section, model in config_models.items()},
        )
        field_definitions["extensions"] = _section_field(extensions_model)

    composed = create_model("ComposedAppConfig", __base__=AppConfig, **field_definitions)
    return composed()
