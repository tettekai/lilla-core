from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import yaml
from dotenv import dotenv_values, load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
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


class LlmProviderConfig(BaseModel):
    """LLMプロバイダーの設定モデル。"""

    type: Literal["ollama", "openai_compat"]  # "ollama" or "openai_compat"
    url: str
    model: str
    # ollama タイプのみ
    wakeup_file: str | None = None
    wol_sleep_seconds: int = 30
    # openai_compat タイプのみ
    api_key_env: str | None = None
    # プロバイダー固有の追加パラメータ（POSTボディのトップレベルに展開される）
    extra_params: dict[str, Any] = {}


class DiscordConfig(BaseModel):
    """lilla.yaml の `discord:` セクション（コア確定分）。"""

    # 拡張側が同じセクションに固有フィールドを追加できるよう extra を無視する。
    model_config = ConfigDict(extra="ignore")

    # リラはオーナー専用の個人アシスタントという前提であり、オーナー判定ができない
    # 状態（未設定）のまま動き続けること自体が思想と矛盾するため、デフォルト値を
    # 持たせず起動時に fail-fast させる。
    my_user_id: str
    error_channel: str | None = None
    approval_channel: str = "lilla-approval"


class PathsConfig(BaseModel):
    """lilla.yaml の `paths:` セクション。"""

    tool_root: Path = Path("/app/tools")
    allowed_tool_paths: str = "/app/tools,/app/config/tools"

    @property
    def allowed_tool_paths_list(self) -> list[Path]:
        """カンマ区切りの `allowed_tool_paths` を解決済み Path のリストにして返す。"""
        return [
            Path(p.strip()).resolve()
            for p in self.allowed_tool_paths.split(",")
            if p.strip()
        ]


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

    # 拡張側が同じセクションに固有フィールドを追加できるよう extra を無視する。
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
        """`default` が `providers` のキーに存在することを検証する。

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
        return self


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


class EnvConfigSettingsSource(PydanticBaseSettingsSource):
    """`.env` / OS 環境変数から `env` セクションを組み立てるカスタム設定ソース。

    OS 変数名は従来のまま（`DISCORD_TOKEN` など）で、`ENV__` プレフィックスは
    使わない。ここに列挙した変数だけが `cfg.env` に流れ、YAML 由来の項目を
    環境変数で上書きする経路は持たない。
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

    def _load(self, env_file: Any) -> dict[str, Any]:
        """OS 環境変数（優先）と `.env` から `{"env": {...}}` を組み立てる。"""
        file_values: dict[str, str | None] = {}
        if env_file and Path(env_file).is_file():
            file_values = dotenv_values(env_file, encoding="utf-8")

        values: dict[str, Any] = {}
        for field_name, var_name in self._VAR_NAMES.items():
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
    # `LlmConfig()` を直接デフォルト値にすると、クラス定義（モジュール import）の
    # 時点で即座にインスタンス化・検証されてしまい、YAML の内容に関わらず
    # import だけで落ちる。`default_factory` で AppConfig 構築時まで遅延させる。
    llm: LlmConfig = Field(default_factory=LlmConfig)
    ui: UiConfig = UiConfig()

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

    `src/extensions.py` で `AppConfigEx()` を組み立て、この関数で
    差し込むことで、`get_config()` の戻り値をコア既定から拡張版へ
    切り替える。1 プロセスで複数回呼ぶことは想定していないが、
    テスト用途では上書き可（`_config_instance` の直接リセットも可）。
    """
    global _config_instance
    _config_instance = instance


@lru_cache(maxsize=1)
def _default_config() -> AppConfig:
    """`set_config()` が一度も呼ばれなかった場合、素の `AppConfig` を組み立てて返す。
    """
    return AppConfig()


def get_config() -> AppConfig:
    """アプリ全体で共有する設定インスタンスを返す。

    NOTE: この関数はモジュールレベル（import 時に実行される場所）ではなく、
    必ず呼び出されるタイミング（関数内）で呼ぶこと。モジュールレベルで
    `cfg = get_config()` のように書くと、その時点のインスタンスを固定して
    しまい、後から `set_config()` されても反映されなくなる。

    型ヒント上は `AppConfig` を返すが、`extensions.py` が
    `set_config(AppConfigEx())` を呼んでいる場合はサブクラスの
    インスタンス（拡張フィールドを持つ）が返る（ダックタイピング）。
    """
    if _config_instance is not None:
        return _config_instance
    return _default_config()
