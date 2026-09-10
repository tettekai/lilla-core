"""discord_bot.py のテスト。
Discord への接続はすべてモック、環境変数は .env を読まずテスト用の値を使う。
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import lilla_core.handlers.message_handler
import lilla_core.handlers.task_handler
import lilla_core.bot_client

# DMChannel は isinstance チェックが通るよう実クラスとして定義する
_MockDMChannel = type("DMChannel", (), {})


class _TestDMChannel(_MockDMChannel):
    """isinstance(channel, discord.DMChannel) が True になるテスト用 DMChannel"""


class _PrivilegedIntentsRequired(Exception):
    """discord.errors.PrivilegedIntentsRequired のテスト用スタブ（except節が拾える実例外クラス）"""


def _make_typing_cm() -> MagicMock:
    """async with channel.typing(): で使える非同期コンテキストマネージャを返す"""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=None)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _real_extract_command_content(content: str):
    """本物の extract_command_content へ委譲する（判定ロジックを二重管理しない）。

    handlers.command_handler はモックへ差し替えているため、実ファイルを別名で
    直接ロードして使う。既知コマンドの登録は bot の import 時に実行される
    load_all_commands() が行う。
    """
    module = getattr(_real_extract_command_content, "_module", None)
    if module is None:
        path = Path(__file__).parent.parent / "src" / "lilla_core" / "handlers" / "command_handler.py"
        spec = importlib.util.spec_from_file_location(
            "command_handler_under_test", str(path)
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _real_extract_command_content._module = module
    return module.extract_command_content(content)


def _unload_module(name: str) -> None:
    """patch.dict 有効下で再ロードさせるため、モジュールを sys.modules から取り除く。

    `from lilla_core.handlers import approval_flow` のようなサブモジュール import は
    親パッケージの属性を先に見るため、sys.modules から消すだけでは古い
    モジュールオブジェクト（前のテストのモックを掴んだままのもの）が再利用される。
    親パッケージの属性もあわせて削除する。
    """
    sys.modules.pop(name, None)
    pkg_name, _, attr = name.rpartition(".")
    if pkg_name:
        pkg = sys.modules.get(pkg_name)
        if pkg is not None and hasattr(pkg, attr):
            delattr(pkg, attr)


async def _flush_discord_tasks(discord_bot) -> None:
    """_discord_active_tasks に登録されたバックグラウンドタスクを完了させる。"""
    tasks = list(discord_bot.message_handler._discord_active_tasks.values())
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_cfg() -> MagicMock:
    """bot が参照する AppConfig モック。"""
    cfg = MagicMock()
    cfg.proxy.resolve_url.return_value = None
    cfg.env.http_proxy_user = None
    cfg.env.http_proxy_pass = None
    cfg.memory.max_history_turns = 5
    cfg.env.mongodb_uri = "mongodb://localhost:27017"
    cfg.mongodb.db_name = "test_db"
    cfg.env.discord_token = "test-discord-token"
    cfg.discord.error_channel = "error-log"
    cfg.discord.my_user_id = "12345"
    cfg.discord.approval_channel = "lilla-approval"
    return cfg


@pytest.fixture
def mock_bot_instance() -> MagicMock:
    """discord.ext.commands.Bot の戻り値モック。"""
    instance = MagicMock()
    instance.http = MagicMock()
    instance.user = MagicMock(name="bot_user")
    instance.process_commands = AsyncMock()
    # @bot.event デコレータはそのまま関数を返す identity デコレータにする
    instance.event = MagicMock(side_effect=lambda f: f)
    return instance


@pytest.fixture
def mock_commands(mock_bot_instance: MagicMock) -> MagicMock:
    """discord.ext.commands モック。"""
    mock = MagicMock()
    mock.Bot.return_value = mock_bot_instance
    return mock


@pytest.fixture
def mock_llm_module() -> MagicMock:
    """lilla_core.api.llm_client モック。"""
    mock = MagicMock()
    mock.chat_to_llm = AsyncMock(return_value="default reply")
    mock.chat_to_llm_with_tools = AsyncMock(return_value={
        "content": "tool loop reply",
        "tool_calls": None,
        "finish_reason": "stop",
        "raw_message": {"role": "assistant", "content": "tool loop reply"},
    })
    return mock


@pytest.fixture
def mock_llm_tool_loader() -> MagicMock:
    """lilla_core.loaders.llm_tool_loader モック。"""
    mock = MagicMock()
    mock.get_llm_tools = lambda: {}
    mock.build_tools_param = lambda x: []
    mock.execute_tool_call = AsyncMock(return_value="tool result")
    return mock


@pytest.fixture
def mock_conversation_service() -> MagicMock:
    """services.conversation_service モック。"""
    mock = MagicMock()
    mock.run_conversation = AsyncMock(return_value="default reply")
    return mock


@pytest.fixture
def mock_memory_manager_instance() -> MagicMock:
    """MemoryManager インスタンスのモック。"""
    instance = MagicMock()
    instance.ensure_indexes = AsyncMock()
    instance.build_system_prompt = AsyncMock(return_value="test system prompt")
    instance.load_conversation_history = AsyncMock(return_value=[])
    instance.add_conversation = AsyncMock()
    return instance


@pytest.fixture
def mock_memory_manager_module(mock_memory_manager_instance: MagicMock) -> MagicMock:
    """services.memory_manager モック。"""
    module = MagicMock()
    module.MemoryManager = MagicMock(return_value=mock_memory_manager_instance)
    module.get_memory_manager = MagicMock(return_value=mock_memory_manager_instance)
    return module


@pytest.fixture
def mock_command_handler() -> MagicMock:
    """lilla_core.handlers.command_handler モック。"""
    mock = MagicMock()
    mock.extract_command_content = _real_extract_command_content
    return mock


@pytest.fixture
def mock_conversation_repo_instance() -> MagicMock:
    return MagicMock(init_collection=AsyncMock())


@pytest.fixture
def mock_user_memo_repo_instance() -> MagicMock:
    return MagicMock(init_collection=AsyncMock())


@pytest.fixture
def mock_tool_cache_repo_instance() -> MagicMock:
    return MagicMock(init_collection=AsyncMock())


@pytest.fixture
def mock_current_media_repo_instance() -> MagicMock:
    return MagicMock(
        init_collection=AsyncMock(),
        ensure_initialized=AsyncMock(),
    )


@pytest.fixture
def mock_button_actions_repo_instance() -> MagicMock:
    return MagicMock(
        init_collection=AsyncMock(),
        ensure_indexes=AsyncMock(),
        save=AsyncMock(),
        find_one_and_delete=AsyncMock(return_value=None),
    )


@pytest.fixture
def mock_sleep_summary_repo_instance() -> MagicMock:
    return MagicMock(init_collection=AsyncMock())


@pytest.fixture
def mock_heart_rate_summary_repo_instance() -> MagicMock:
    return MagicMock(init_collection=AsyncMock())


@pytest.fixture
def mock_asken_daily_repo_instance() -> MagicMock:
    return MagicMock(init_collection=AsyncMock())


@pytest.fixture
def mock_pending_tool_calls_repo_instance() -> MagicMock:
    return MagicMock(
        init_collection=AsyncMock(),
        exists_pending=AsyncMock(return_value=False),
        save=AsyncMock(),
        complete=AsyncMock(return_value=None),
    )


@pytest.fixture
def mock_habit_repo_instance() -> MagicMock:
    return MagicMock(init_collection=AsyncMock())


@pytest.fixture
def startup_repo_instances(
    mock_conversation_repo_instance: MagicMock,
    mock_user_memo_repo_instance: MagicMock,
    mock_tool_cache_repo_instance: MagicMock,
    mock_current_media_repo_instance: MagicMock,
    mock_button_actions_repo_instance: MagicMock,
    mock_sleep_summary_repo_instance: MagicMock,
    mock_heart_rate_summary_repo_instance: MagicMock,
    mock_asken_daily_repo_instance: MagicMock,
    mock_pending_tool_calls_repo_instance: MagicMock,
    mock_habit_repo_instance: MagicMock,
) -> list:
    """on_ready で初期化される全リポジトリモック。"""
    return [
        mock_conversation_repo_instance,
        mock_user_memo_repo_instance,
        mock_tool_cache_repo_instance,
        mock_current_media_repo_instance,
        mock_button_actions_repo_instance,
        mock_sleep_summary_repo_instance,
        mock_heart_rate_summary_repo_instance,
        mock_asken_daily_repo_instance,
        mock_pending_tool_calls_repo_instance,
        mock_habit_repo_instance,
    ]


@pytest.fixture
def mock_extension_points() -> MagicMock:
    """`lilla_core.core.extension_points` のモック。

    `get_extra_startup_repos` / `get_message_hook` / `get_startup_tasks` を
    持ち、`discord_bot` フィクスチャで具体的な戻り値をセットする。
    """
    mock = MagicMock()
    mock.get_extra_startup_repos = MagicMock(return_value=[])
    mock.get_message_hook = MagicMock(return_value=AsyncMock(return_value=False))
    mock.get_startup_tasks = MagicMock(return_value=[])
    return mock


@pytest.fixture
def with_mocked_modules(
    mock_cfg: MagicMock,
    mock_commands: MagicMock,
    mock_llm_module: MagicMock,
    mock_llm_tool_loader: MagicMock,
    mock_conversation_service: MagicMock,
    mock_memory_manager_module: MagicMock,
    mock_command_handler: MagicMock,
    mock_extension_points: MagicMock,
    mock_conversation_repo_instance: MagicMock,
    mock_user_memo_repo_instance: MagicMock,
    mock_tool_cache_repo_instance: MagicMock,
    mock_button_actions_repo_instance: MagicMock,
    mock_pending_tool_calls_repo_instance: MagicMock,
):
    """依存モジュールを patch.dict で差し替える。

    `lilla_core.core.extension_points` をモックし、`discord_bot` フィクスチャで
    `get_extra_startup_repos` / `get_message_hook` / `get_startup_tasks` の
    戻り値をセットする。
    """
    mock_discord = MagicMock()
    mock_discord.DMChannel = _MockDMChannel
    mock_discord.errors.PrivilegedIntentsRequired = _PrivilegedIntentsRequired
    mock_discord_ext = MagicMock()
    mock_discord_ext.commands = mock_commands
    with patch.dict(
        sys.modules,
        {
            "discord": mock_discord,
            "discord.ext": mock_discord_ext,
            "discord.ext.commands": mock_commands,
            "aiohttp": MagicMock(),
            "lilla_core.core.config": MagicMock(get_config=lambda: mock_cfg),
            "lilla_core.core.extension_points": mock_extension_points,
            "lilla_core.core.logging_setup": MagicMock(setup_logging=lambda: None),
            "lilla_core.api.llm_client": mock_llm_module,
            "lilla_core.handlers.command_handler": mock_command_handler,
            "lilla_core.handlers.task_handler": MagicMock(),
            "lilla_core.loaders.task_tool_loader": MagicMock(load_all_tools=lambda: {}),
            "lilla_core.loaders.llm_tool_loader": mock_llm_tool_loader,
            "lilla_core.repository.conversation_repository": MagicMock(
                get_conversation_repo=MagicMock(return_value=mock_conversation_repo_instance)
            ),
            "lilla_core.repository.user_memo_repository": MagicMock(
                get_user_memo_repo=MagicMock(return_value=mock_user_memo_repo_instance)
            ),
            "lilla_core.repository.tool_cache_repository": MagicMock(
                get_tool_cache_repo=MagicMock(return_value=mock_tool_cache_repo_instance)
            ),
            "lilla_core.repository.button_actions_repository": MagicMock(
                get_button_actions_repo=MagicMock(return_value=mock_button_actions_repo_instance)
            ),
            "lilla_core.repository.pending_tool_calls_repository": MagicMock(
                get_pending_tool_calls_repo=MagicMock(return_value=mock_pending_tool_calls_repo_instance)
            ),
            "lilla_core.services.memory_manager": mock_memory_manager_module,
            "lilla_core.services.conversation_service": mock_conversation_service,
        },
    ):
        yield


@pytest.fixture
def discord_bot(
    with_mocked_modules,
    mock_cfg: MagicMock,
    mock_memory_manager_instance: MagicMock,
    mock_extension_points: MagicMock,
    startup_repo_instances: list,
):
    """patch.dict 有効後に bot / bot_client をロードする。"""
    _unload_module("lilla_core.bot")
    _unload_module("lilla_core.bot_client")
    _unload_module("lilla_core.handlers.approval_flow")
    _unload_module("lilla_core.handlers.interaction_handler")
    _unload_module("lilla_core.handlers.message_handler")
    from lilla_core import bot as discord_bot_mod
    import lilla_core.bot_client  # noqa: F401

    lilla_core.handlers.message_handler._memory_manager = mock_memory_manager_instance
    discord_bot_mod.bot.user.mentioned_in.return_value = MagicMock()
    discord_bot_mod._config.discord.my_user_id = "12345"
    discord_bot_mod._config.discord.approval_channel = "lilla-approval"
    discord_bot_mod.llm_tools = {}
    lilla_core.handlers.message_handler.run_conversation = AsyncMock(return_value="default reply")
    lilla_core.handlers.message_handler._discord_active_tasks.clear()

    # bot.py が data_collector_handler モジュール属性を持たなくなったが、
    # 既存テストが `monkeypatch.setattr(discord_bot, "data_collector_handler",
    # mock)` で差し替えるスタイルを維持する。`get_message_hook()` の
    # side_effect で毎回この属性を読み直せば、テストで setattr された最新の
    # ハンドラが `on_message` の呼び出しに反映される。
    discord_bot_mod.data_collector_handler = AsyncMock(return_value=False)
    mock_extension_points.get_message_hook.side_effect = (
        lambda: discord_bot_mod.data_collector_handler
    )

    # 全リポジトリインスタンス（コア・拡張問わず）を追加起動リポジトリとして
    # 差し込む。`_CORE_STARTUP_REPOS` は空リストに差し替えて重複起動を避ける
    # （テストは初期化される全リポジトリの init_collection 呼び出しだけを
    # 見ており、コア/拡張の分類を意識しない）。
    discord_bot_mod._CORE_STARTUP_REPOS = []
    mock_extension_points.get_extra_startup_repos.return_value = [
        (lambda inst=inst: inst) for inst in startup_repo_instances
    ]

    # 追加起動タスクはデフォルトで空（`TestMain` で必要なテストが個別に設定）。
    mock_extension_points.get_startup_tasks.return_value = []

    # `from lilla_core.handlers import task_handler` はパッケージ属性経由のため、
    # 先行テストで実モジュールが読み込まれていると patch.dict が効かない。
    discord_bot_mod.task_handler = MagicMock()
    discord_bot_mod.task_handler.start_scheduler = MagicMock()
    yield discord_bot_mod
    _unload_module("lilla_core.bot")
    _unload_module("lilla_core.bot_client")
    _unload_module("lilla_core.handlers.approval_flow")
    _unload_module("lilla_core.handlers.interaction_handler")
    _unload_module("lilla_core.handlers.message_handler")


@pytest.fixture
def bot(discord_bot):
    """`import bot` と同等のエイリアス。"""
    return discord_bot


@pytest.fixture
def bot_client(discord_bot):
    """patch.dict 有効後にロード済みの bot_client。"""
    from lilla_core import bot_client as bot_client_mod
    return bot_client_mod


@pytest.fixture()
def mock_message() -> MagicMock:
    """Discord Message のモック"""
    msg = MagicMock()
    msg.author = MagicMock(name="other_user")
    msg.author.id = 12345
    msg.content = "hello"
    msg.channel = MagicMock()
    msg.channel.typing.return_value = _make_typing_cm()
    msg.channel.send = AsyncMock()
    msg.reply = AsyncMock()
    msg.attachments = []
    return msg


# ---------------------------------------------------------------------------
# TestBotInstanceSharing
# ---------------------------------------------------------------------------


class TestBotInstanceSharing:
    """bot モジュールが bot_client の共有インスタンスを参照していることの確認。"""

    def test_bot_is_bot_client_instance(self, bot, bot_client) -> None:
        """bot.bot は bot_client.bot と同一オブジェクトである。"""
        assert bot.bot is bot_client.bot

    def test_bot_does_not_create_own_client(self, bot, mock_commands) -> None:
        """bot モジュール側で commands.Bot() を生成していない。"""
        # Client の生成は bot_client への import 時の一度きり
        assert mock_commands.Bot.call_count == 1


# ---------------------------------------------------------------------------
# TestOnMessage
# ---------------------------------------------------------------------------


class TestOnMessage:
    async def test_ignores_own_message(self, discord_bot, mock_message: MagicMock) -> None:
        """bot 自身のメッセージは無視する"""
        mock_message.author = discord_bot.bot.user  # 同じオブジェクト → 等しい
        await discord_bot.on_message(mock_message)
        mock_message.reply.assert_not_called()

    async def test_routes_command_to_handle_command(
        self, discord_bot, mock_message: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """! で始まるメッセージは command_handler.handle_command に委譲する"""
        mock_message.content = "!runtask sometool"
        mock_handle = AsyncMock()
        # message_handler が import 時にバインドした MagicMock を直接参照する
        monkeypatch.setattr(discord_bot.message_handler.command_handler, "handle_command", mock_handle)
        await discord_bot.on_message(mock_message)
        mock_handle.assert_called_once_with(mock_message, "!runtask sometool", discord_bot.tools, discord_bot.bot)

    async def test_responds_to_mention(
        self,
        discord_bot,
        mock_message: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """メンションされたとき LLM の返答をリプライする"""
        mock_message.content = "おはよう"
        discord_bot.bot.user.mentioned_in.return_value = True
        monkeypatch.setattr(discord_bot.message_handler, "run_conversation", AsyncMock(return_value="おはようございます！"))
        await discord_bot.on_message(mock_message)
        await _flush_discord_tasks(discord_bot)
        mock_message.reply.assert_called_once_with("おはようございます！")

    async def test_responds_to_dm(
        self,
        discord_bot,
        mock_message: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """DM チャンネルではメンションなしでも返答する"""
        discord_bot.bot.user.mentioned_in.return_value = False
        # DMChannel のインスタンス（isinstance チェックが通る）に差し替える
        dm_channel = _TestDMChannel()
        dm_channel.typing = MagicMock(return_value=_make_typing_cm())
        dm_channel.send = AsyncMock()
        dm_channel.id = 100
        mock_message.channel = dm_channel

        # 実 discord がロードされている場合、discord_bot 内の DMChannel を _MockDMChannel に差し替える
        monkeypatch.setattr(discord_bot.discord, "DMChannel", _MockDMChannel)
        # data_collector_handler も実 discord.DMChannel の isinstance チェックを含むためモックする
        monkeypatch.setattr(discord_bot, "data_collector_handler", AsyncMock(return_value=False))
        monkeypatch.setattr(discord_bot.message_handler, "run_conversation", AsyncMock(return_value="DM reply"))
        await discord_bot.on_message(mock_message)
        await _flush_discord_tasks(discord_bot)
        mock_message.reply.assert_called_once_with("DM reply")

    async def test_no_response_when_not_mentioned_not_dm(
        self, discord_bot, mock_message: MagicMock
    ) -> None:
        """メンションなし・DM でないとき返答しない"""
        discord_bot.bot.user.mentioned_in.return_value = False
        # channel は MagicMock なので isinstance(..., _MockDMChannel) が False
        await discord_bot.on_message(mock_message)
        mock_message.reply.assert_not_called()

    async def test_llm_error_notifies_error_channel(
        self,
        discord_bot,
        mock_message: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """LLM がエラーを起こしたときエラーチャンネルに通知し、返信しない"""
        discord_bot.bot.user.mentioned_in.return_value = True
        monkeypatch.setattr(discord_bot.message_handler, "run_conversation", AsyncMock(side_effect=Exception("LLM down")))

        mock_notify = AsyncMock()
        monkeypatch.setattr(discord_bot.message_handler, "notify_error", mock_notify)

        await discord_bot.on_message(mock_message)
        await _flush_discord_tasks(discord_bot)

        mock_message.reply.assert_not_called()
        mock_notify.assert_called_once()

    async def test_ignores_message_from_non_owner_when_my_user_id_configured(
        self, discord_bot, mock_message: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """discord.my_user_id が設定されているとき、それ以外のユーザーのメッセージは無視する"""
        monkeypatch.setattr(discord_bot._config.discord, "my_user_id", "99999")
        mock_message.author.id = 12345  # 99999 と異なる
        discord_bot.bot.user.mentioned_in.return_value = True
        await discord_bot.on_message(mock_message)
        mock_message.reply.assert_not_called()

    async def test_processes_message_from_owner_when_my_user_id_configured(
        self, discord_bot, mock_message: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """discord.my_user_id が設定されているとき、一致するユーザーのメッセージは処理する"""
        monkeypatch.setattr(discord_bot._config.discord, "my_user_id", "12345")
        mock_message.author.id = 12345
        discord_bot.bot.user.mentioned_in.return_value = True
        monkeypatch.setattr(discord_bot.message_handler, "run_conversation", AsyncMock(return_value="hi"))
        await discord_bot.on_message(mock_message)
        await _flush_discord_tasks(discord_bot)
        mock_message.reply.assert_called_once_with("hi")

    async def test_llm_error_no_error_channel_configured(
        self,
        discord_bot,
        mock_message: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """エラーチャンネルが未設定のときは返信も通知もしない"""
        discord_bot.bot.user.mentioned_in.return_value = True
        monkeypatch.setattr(discord_bot.message_handler, "run_conversation", AsyncMock(side_effect=Exception("LLM down")))
        monkeypatch.setattr(discord_bot._config, "discord_error_channel", None)

        mock_notify = AsyncMock()
        monkeypatch.setattr(discord_bot.message_handler, "notify_error", mock_notify)

        await discord_bot.on_message(mock_message)
        await _flush_discord_tasks(discord_bot)

        mock_message.reply.assert_not_called()

    async def test_splits_multiblock_response(
        self,
        discord_bot,
        mock_message: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """\\n\\n を含む返答が複数メッセージに分割されて送信される。"""
        discord_bot.bot.user.mentioned_in.return_value = True
        monkeypatch.setattr(discord_bot.message_handler, "run_conversation", AsyncMock(return_value="ブロック1\n\nブロック2"))
        # asyncio.sleep を即時完了させる
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        await discord_bot.on_message(mock_message)
        await _flush_discord_tasks(discord_bot)
        mock_message.reply.assert_called_once_with("ブロック1")
        mock_message.channel.send.assert_called_once_with("ブロック2")

    async def test_saves_discord_message_info_with_assistant_reply(
        self,
        discord_bot,
        mock_message: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """通常会話の返信にも discord_channel_id / discord_message_ids が付与される。"""
        discord_bot.bot.user.mentioned_in.return_value = True
        mock_message.channel.id = 777
        sent = MagicMock()
        sent.id = 1000
        mock_message.reply = AsyncMock(return_value=sent)
        monkeypatch.setattr(discord_bot.message_handler, "run_conversation", AsyncMock(return_value="返答"))

        await discord_bot.on_message(mock_message)
        await _flush_discord_tasks(discord_bot)

        # 0 件目は user メッセージ、1 件目が assistant の返信
        call = discord_bot.message_handler._memory_manager.add_conversation.call_args_list[1]
        assert call[0][0] == {"role": "assistant", "content": "返答"}
        assert call.kwargs["discord_channel_id"] == 777
        assert call.kwargs["discord_message_ids"] == [1000]

    async def test_saves_message_id_per_split_block(
        self,
        discord_bot,
        mock_message: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """分割送信では各ブロックに対応する Discord メッセージ ID が保存される。"""
        discord_bot.bot.user.mentioned_in.return_value = True
        mock_message.channel.id = 777
        first = MagicMock()
        first.id = 1000
        second = MagicMock()
        second.id = 1001
        mock_message.reply = AsyncMock(return_value=first)
        mock_message.channel.send = AsyncMock(return_value=second)
        monkeypatch.setattr(
            discord_bot.message_handler, "run_conversation", AsyncMock(return_value="ブロック1\n\nブロック2")
        )
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())

        await discord_bot.on_message(mock_message)
        await _flush_discord_tasks(discord_bot)

        assistant_calls = discord_bot.message_handler._memory_manager.add_conversation.call_args_list[1:]
        assert [c[0][0]["content"] for c in assistant_calls] == ["ブロック1", "ブロック2"]
        assert [c.kwargs["discord_message_ids"] for c in assistant_calls] == [[1000], [1001]]
        assert all(c.kwargs["discord_channel_id"] == 777 for c in assistant_calls)

    async def test_image_attachment_builds_list_content(
        self,
        discord_bot,
        mock_message: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """画像添付があるとき content がリスト形式で run_conversation に渡される。"""
        discord_bot.bot.user.mentioned_in.return_value = True

        attachment = MagicMock()
        attachment.content_type = "image/png"
        attachment.url = "https://cdn.discordapp.com/test.png"
        mock_message.attachments = [attachment]
        mock_message.content = "この画像は？"

        fake_bytes = b"fake-png-bytes"
        monkeypatch.setattr(discord_bot.message_handler.image_attachment, "download_attachment_bytes", AsyncMock(return_value=fake_bytes))

        mock_run = AsyncMock(return_value="画像の説明")
        monkeypatch.setattr(discord_bot.message_handler, "run_conversation", mock_run)

        await discord_bot.on_message(mock_message)
        await _flush_discord_tasks(discord_bot)

        content_arg = mock_run.call_args.kwargs["override_last_user_content"]
        assert isinstance(content_arg, list)
        assert content_arg[0] == {"type": "text", "text": "この画像は？"}
        assert content_arg[1]["type"] == "image_url"
        assert content_arg[1]["image_url"]["url"].startswith("data:image/png;base64,")

    async def test_image_attachment_saves_placeholder_to_memory(
        self,
        discord_bot,
        mock_message: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """画像添付時に MongoDB には base64 ではなく [画像 N 枚添付] が保存される。"""
        discord_bot.bot.user.mentioned_in.return_value = True

        attachment = MagicMock()
        attachment.content_type = "image/jpeg"
        attachment.url = "https://cdn.discordapp.com/test.jpg"
        mock_message.attachments = [attachment]
        mock_message.content = "説明して"

        monkeypatch.setattr(discord_bot.message_handler.image_attachment, "download_attachment_bytes", AsyncMock(return_value=b"bytes"))
        monkeypatch.setattr(discord_bot.message_handler, "run_conversation", AsyncMock(return_value="返答"))

        await discord_bot.on_message(mock_message)
        await _flush_discord_tasks(discord_bot)

        saved = discord_bot.message_handler._memory_manager.add_conversation.call_args_list[0][0][0]
        assert saved["role"] == "user"
        assert "[画像 1 枚添付]" in saved["content"]
        assert "base64" not in saved["content"]

    async def test_image_only_no_text_saves_placeholder(
        self,
        discord_bot,
        mock_message: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """テキストなし・画像のみの場合も [画像 N 枚添付] が保存される。"""
        discord_bot.bot.user.mentioned_in.return_value = True

        attachment = MagicMock()
        attachment.content_type = "image/webp"
        attachment.url = "https://cdn.discordapp.com/test.webp"
        mock_message.attachments = [attachment]
        mock_message.content = ""

        monkeypatch.setattr(discord_bot.message_handler.image_attachment, "download_attachment_bytes", AsyncMock(return_value=b"bytes"))
        monkeypatch.setattr(discord_bot.message_handler, "run_conversation", AsyncMock(return_value="返答"))

        await discord_bot.on_message(mock_message)
        await _flush_discord_tasks(discord_bot)

        saved = discord_bot.message_handler._memory_manager.add_conversation.call_args_list[0][0][0]
        assert saved["content"] == "[画像 1 枚添付]"

    async def test_multiple_images_builds_correct_content_parts(
        self,
        discord_bot,
        mock_message: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """複数画像添付で content_parts が画像の数だけ追加される。"""
        discord_bot.bot.user.mentioned_in.return_value = True

        def make_attachment(mime: str, url: str) -> MagicMock:
            a = MagicMock()
            a.content_type = mime
            a.url = url
            return a

        mock_message.attachments = [
            make_attachment("image/png", "https://cdn.discordapp.com/a.png"),
            make_attachment("image/jpeg", "https://cdn.discordapp.com/b.jpg"),
        ]
        mock_message.content = ""

        monkeypatch.setattr(discord_bot.message_handler.image_attachment, "download_attachment_bytes", AsyncMock(return_value=b"bytes"))
        monkeypatch.setattr(discord_bot.message_handler, "run_conversation", AsyncMock(return_value="返答"))

        await discord_bot.on_message(mock_message)
        await _flush_discord_tasks(discord_bot)

        call_args = discord_bot.message_handler.run_conversation.call_args.kwargs["override_last_user_content"]
        assert isinstance(call_args, list)
        assert len(call_args) == 2

        saved = discord_bot.message_handler._memory_manager.add_conversation.call_args_list[0][0][0]
        assert "[画像 2 枚添付]" in saved["content"]

    async def test_oversized_image_skips_conversation(
        self,
        discord_bot,
        mock_message: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """上限超過の画像はダウンロードも会話処理も行わず notify_error で通知する。"""
        discord_bot.bot.user.mentioned_in.return_value = True

        attachment = MagicMock()
        attachment.content_type = "image/png"
        attachment.url = "https://cdn.discordapp.com/huge.png"
        attachment.filename = "huge.png"
        attachment.size = 9 * 1024 * 1024
        mock_message.attachments = [attachment]
        mock_message.content = "この画像は？"

        mock_download = AsyncMock()
        monkeypatch.setattr(
            discord_bot.message_handler.image_attachment, "download_attachment_bytes", mock_download
        )
        mock_notify = AsyncMock()
        monkeypatch.setattr(
            discord_bot.message_handler.image_attachment, "notify_error", mock_notify
        )
        mock_run = AsyncMock(return_value="返答")
        monkeypatch.setattr(discord_bot.message_handler, "run_conversation", mock_run)

        await discord_bot.on_message(mock_message)
        await _flush_discord_tasks(discord_bot)

        mock_download.assert_not_awaited()
        mock_run.assert_not_awaited()
        discord_bot.message_handler._memory_manager.add_conversation.assert_not_called()
        mock_notify.assert_awaited_once()
        assert "8MB" in mock_notify.await_args[0][2]

    async def test_image_download_failure_notifies_error(
        self,
        discord_bot,
        mock_message: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """画像ダウンロードの失敗は notify_error 経由で通知され、会話処理は行われない。"""
        discord_bot.bot.user.mentioned_in.return_value = True

        attachment = MagicMock()
        attachment.content_type = "image/png"
        attachment.url = "https://cdn.discordapp.com/test.png"
        attachment.filename = "test.png"
        attachment.size = 1024
        mock_message.attachments = [attachment]
        mock_message.content = "この画像は？"

        error = RuntimeError("network down")
        monkeypatch.setattr(
            discord_bot.message_handler.image_attachment,
            "download_attachment_bytes",
            AsyncMock(side_effect=error),
        )
        mock_notify = AsyncMock()
        monkeypatch.setattr(
            discord_bot.message_handler.image_attachment, "notify_error", mock_notify
        )
        mock_run = AsyncMock(return_value="返答")
        monkeypatch.setattr(discord_bot.message_handler, "run_conversation", mock_run)

        await discord_bot.on_message(mock_message)
        await _flush_discord_tasks(discord_bot)

        mock_run.assert_not_awaited()
        mock_notify.assert_awaited_once()
        assert mock_notify.await_args[0][2] is error

    async def test_unsupported_mime_type_ignored(
        self,
        discord_bot,
        mock_message: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """非対応 MIME タイプの添付は無視してテキストのみで処理する。"""
        discord_bot.bot.user.mentioned_in.return_value = True

        attachment = MagicMock()
        attachment.content_type = "application/pdf"
        attachment.url = "https://cdn.discordapp.com/doc.pdf"
        mock_message.attachments = [attachment]
        mock_message.content = "テスト"

        mock_download = AsyncMock()
        monkeypatch.setattr(discord_bot.message_handler.image_attachment, "download_attachment_bytes", mock_download)
        mock_run = AsyncMock(return_value="返答")
        monkeypatch.setattr(discord_bot.message_handler, "run_conversation", mock_run)

        await discord_bot.on_message(mock_message)
        await _flush_discord_tasks(discord_bot)

        mock_download.assert_not_called()
        assert mock_run.call_args.kwargs.get("override_last_user_content") is None
        saved = discord_bot.message_handler._memory_manager.add_conversation.call_args_list[0][0][0]
        assert saved == {"role": "user", "content": "テスト"}


# ---------------------------------------------------------------------------
# TestExternalCommandApproval
# ---------------------------------------------------------------------------


class TestExternalCommandApproval:
    """外部ユーザーからのコマンド承認フローのテスト。"""

    @pytest.fixture()
    def external_message(self, mock_message: MagicMock) -> MagicMock:
        """承認チャンネル以外のチャンネルにいる外部ユーザーのメッセージ。"""
        # 一般チャンネル名（承認チャンネルではない）
        mock_message.channel.name = "general"
        return mock_message

    async def test_external_command_sends_approval_request(
        self, discord_bot, external_message: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """外部ユーザーの ! コマンドは承認依頼へ回す。"""
        monkeypatch.setattr(discord_bot._config.discord, "my_user_id", "99999")
        external_message.author.id = 12345  # 非オーナー
        external_message.content = "!runtask xxx"
        mock_send = AsyncMock()
        monkeypatch.setattr(discord_bot.message_handler.approval_flow, "send_approval_request", mock_send)

        await discord_bot.on_message(external_message)

        mock_send.assert_called_once_with(discord_bot.bot, external_message, "!runtask xxx")
        external_message.reply.assert_not_called()

    async def test_external_dm_ignored(
        self, discord_bot, external_message: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """外部ユーザーの DM は承認フローへ回さず無視する。"""
        monkeypatch.setattr(discord_bot._config.discord, "my_user_id", "99999")
        external_message.author.id = 12345
        external_message.content = "!runtask xxx"
        dm_channel = _TestDMChannel()
        external_message.channel = dm_channel
        monkeypatch.setattr(discord_bot.discord, "DMChannel", _MockDMChannel)
        mock_send = AsyncMock()
        monkeypatch.setattr(discord_bot.message_handler.approval_flow, "send_approval_request", mock_send)

        await discord_bot.on_message(external_message)

        mock_send.assert_not_called()

    async def test_external_write_to_approval_channel_ignored(
        self, discord_bot, external_message: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """承認チャンネル自体への外部書き込みは無視する（ループ防止）。"""
        monkeypatch.setattr(discord_bot._config.discord, "my_user_id", "99999")
        external_message.author.id = 12345
        external_message.content = "!runtask xxx"
        external_message.channel.name = "lilla-approval"
        mock_send = AsyncMock()
        monkeypatch.setattr(discord_bot.message_handler.approval_flow, "send_approval_request", mock_send)

        await discord_bot.on_message(external_message)

        mock_send.assert_not_called()

    async def test_external_non_command_ignored(
        self, discord_bot, external_message: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """外部ユーザーの通常会話（! 以外）は無視する。"""
        monkeypatch.setattr(discord_bot._config.discord, "my_user_id", "99999")
        external_message.author.id = 12345
        external_message.content = "こんにちは"
        mock_send = AsyncMock()
        monkeypatch.setattr(discord_bot.message_handler.approval_flow, "send_approval_request", mock_send)

        await discord_bot.on_message(external_message)

        mock_send.assert_not_called()
        external_message.reply.assert_not_called()


# ---------------------------------------------------------------------------
# TestAgentResultApproval
# ---------------------------------------------------------------------------


_UUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


def _frontmatter_message(body: str = "検索結果です", correlation_id: str = _UUID) -> str:
    """外部エージェントからの結果メッセージ（FrontMatter 付き）を組み立てる。"""
    return f"---\ncorrelation_id: {correlation_id}\n---\n\n{body}"


class TestAgentResultApproval:
    """外部エージェントからの結果メッセージを !toolresult コマンドへ変換するフローのテスト。"""

    @pytest.fixture(autouse=True)
    def reset_pending_repo(self, mock_pending_tool_calls_repo_instance) -> None:
        """pending_tool_calls リポジトリのモック状態をリセットする。"""
        mock_pending_tool_calls_repo_instance.exists_pending.reset_mock()
        mock_pending_tool_calls_repo_instance.exists_pending.return_value = True
        mock_pending_tool_calls_repo_instance.exists_pending.side_effect = None

    @pytest.fixture()
    def external_message(self, discord_bot, mock_message: MagicMock, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
        """一般チャンネルにいる非オーナー（外部エージェント）のメッセージ。"""
        monkeypatch.setattr(discord_bot._config.discord, "my_user_id", "99999")
        mock_message.author.id = 12345
        mock_message.channel.name = "general"
        mock_message.content = _frontmatter_message()
        return mock_message

    async def test_converts_to_toolresult_command(
        self, discord_bot, mock_pending_tool_calls_repo_instance, external_message: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """pending レコードが存在する場合、!toolresult コマンドへ変換して承認依頼を出す。"""
        mock_send = AsyncMock()
        monkeypatch.setattr(discord_bot.message_handler.approval_flow, "send_approval_request", mock_send)

        await discord_bot.on_message(external_message)

        mock_pending_tool_calls_repo_instance.exists_pending.assert_called_once_with(_UUID)
        mock_send.assert_called_once_with(
            discord_bot.bot, external_message, f"!toolresult {_UUID}\n検索結果です"
        )

    async def test_ignores_unknown_correlation_id(
        self, discord_bot, mock_pending_tool_calls_repo_instance, external_message: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """pending レコードが存在しない correlation_id は無視する。"""
        mock_pending_tool_calls_repo_instance.exists_pending.return_value = False
        mock_send = AsyncMock()
        monkeypatch.setattr(discord_bot.message_handler.approval_flow, "send_approval_request", mock_send)

        await discord_bot.on_message(external_message)

        mock_send.assert_not_called()

    async def test_ignores_message_without_frontmatter(
        self, discord_bot, mock_pending_tool_calls_repo_instance, external_message: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FrontMatter のない通常メッセージは照会もせず無視する。"""
        external_message.content = "こんにちは"
        mock_send = AsyncMock()
        monkeypatch.setattr(discord_bot.message_handler.approval_flow, "send_approval_request", mock_send)

        await discord_bot.on_message(external_message)

        mock_pending_tool_calls_repo_instance.exists_pending.assert_not_called()
        mock_send.assert_not_called()

    async def test_known_command_takes_precedence(
        self, discord_bot, mock_pending_tool_calls_repo_instance, external_message: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """既知コマンドを含む場合はそちらを優先し、FrontMatter 変換は行わない。"""
        external_message.content = "!runtask xxx\n" + _frontmatter_message()
        mock_send = AsyncMock()
        monkeypatch.setattr(discord_bot.message_handler.approval_flow, "send_approval_request", mock_send)

        await discord_bot.on_message(external_message)

        mock_pending_tool_calls_repo_instance.exists_pending.assert_not_called()
        assert mock_send.call_args[0][2].startswith("!runtask xxx")

    async def test_repo_error_is_ignored(
        self, discord_bot, mock_pending_tool_calls_repo_instance, external_message: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """照会に失敗した場合は承認依頼を出さない。"""
        mock_pending_tool_calls_repo_instance.exists_pending.side_effect = Exception("db down")
        mock_send = AsyncMock()
        monkeypatch.setattr(discord_bot.message_handler.approval_flow, "send_approval_request", mock_send)

        await discord_bot.on_message(external_message)

        mock_send.assert_not_called()

    async def test_owner_frontmatter_message_goes_to_conversation(
        self, discord_bot, mock_pending_tool_calls_repo_instance, mock_message: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """オーナーからの FrontMatter 付きメッセージは通常の会話として扱う。"""
        monkeypatch.setattr(discord_bot._config.discord, "my_user_id", "12345")
        mock_message.author.id = 12345
        mock_message.content = _frontmatter_message()
        discord_bot.bot.user.mentioned_in.return_value = True
        mock_send = AsyncMock()
        monkeypatch.setattr(discord_bot.message_handler.approval_flow, "send_approval_request", mock_send)
        monkeypatch.setattr(discord_bot.message_handler, "run_conversation", AsyncMock(return_value="はーい"))

        await discord_bot.on_message(mock_message)
        await _flush_discord_tasks(discord_bot)

        mock_send.assert_not_called()
        mock_pending_tool_calls_repo_instance.exists_pending.assert_not_called()
        mock_message.reply.assert_called_once_with("はーい")

    async def test_approve_runs_command_restored_from_approval_message(
        self, discord_bot, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """承認ボタン押下時は承認依頼メッセージから実行内容を復元して実行する。

        元メッセージは一切参照しない（`fetch_message` を呼ばない）ため、承認待ちの間に
        元メッセージが編集されても実行内容は変わらない。
        """
        monkeypatch.setattr(
            discord_bot.discord, "InteractionType", MagicMock(component=object())
        )
        approval_flow = discord_bot.interaction_handler.approval_flow
        interaction = _make_interaction(
            type_value=discord_bot.discord.InteractionType.component,
            custom_id="approve:100:200",
        )
        interaction.response.edit_message = AsyncMock()
        interaction.message = MagicMock()
        interaction.message.attachments = []
        interaction.message.content = (
            "承認依頼テキスト\n\n"
            f"{approval_flow.command_marker()}\n"
            f"!toolresult {_UUID}\n検索結果です"
        )

        original_channel = MagicMock()
        mock_get_channel = MagicMock(return_value=original_channel)
        monkeypatch.setattr(discord_bot.bot, "get_channel", mock_get_channel)

        mock_handle = AsyncMock()
        monkeypatch.setattr(approval_flow.command_handler, "handle_command", mock_handle)

        await discord_bot.on_interaction(interaction)

        # 返信先チャンネルの解決だけを行い、元メッセージは再取得しない
        mock_get_channel.assert_called_once_with(100)
        original_channel.fetch_message.assert_not_called()
        passed_message, passed_content, passed_tools, passed_bot = mock_handle.call_args[0]
        assert passed_content == f"!toolresult {_UUID}\n検索結果です"
        assert passed_message.content == passed_content
        assert passed_message.attachments == []
        assert passed_message.channel is original_channel
        assert passed_tools is discord_bot.tools
        assert passed_bot is discord_bot.bot


# ---------------------------------------------------------------------------
# TestDataCollectorNotification
# ---------------------------------------------------------------------------


class TestDataCollectorNotification:
    """data collector 通知処理のテスト。

    collector はオーナーと別のアカウントで通知を投稿するため、
    discord.my_user_id によるオーナー判定より前に通知処理が行われることを確認する。
    """

    async def test_notification_from_non_owner_is_processed(
        self, discord_bot, mock_message: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """discord_my_user_id 設定時でも、非オーナー（collector）からの通知は処理される。"""
        monkeypatch.setattr(discord_bot._config.discord, "my_user_id", "99999")
        mock_message.author.id = 12345  # 非オーナー（collector アカウント想定）
        mock_handler = AsyncMock(return_value=True)
        monkeypatch.setattr(discord_bot, "data_collector_handler", mock_handler)
        mock_send = AsyncMock()
        monkeypatch.setattr(discord_bot.message_handler.approval_flow, "send_approval_request", mock_send)

        await discord_bot.on_message(mock_message)

        mock_handler.assert_called_once_with(mock_message)
        # 通知として処理された場合は承認フロー・返信に進まない
        mock_send.assert_not_called()
        mock_message.reply.assert_not_called()

    async def test_notification_processed_stops_further_handling(
        self, discord_bot, mock_message: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """通知として処理された場合は後続のコマンド処理を行わない。"""
        mock_message.content = "!runtask sometool"
        mock_handler = AsyncMock(return_value=True)
        monkeypatch.setattr(discord_bot, "data_collector_handler", mock_handler)
        mock_handle = AsyncMock()
        monkeypatch.setattr(discord_bot.message_handler.command_handler, "handle_command", mock_handle)

        await discord_bot.on_message(mock_message)

        mock_handle.assert_not_called()

    async def test_non_notification_falls_through(
        self, discord_bot, mock_message: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """通知でないメッセージ（handler が False）は通常フローに進む。"""
        mock_message.content = "!runtask sometool"
        mock_handler = AsyncMock(return_value=False)
        monkeypatch.setattr(discord_bot, "data_collector_handler", mock_handler)
        mock_handle = AsyncMock()
        monkeypatch.setattr(discord_bot.message_handler.command_handler, "handle_command", mock_handle)

        await discord_bot.on_message(mock_message)

        mock_handler.assert_called_once_with(mock_message)
        mock_handle.assert_called_once()


# ---------------------------------------------------------------------------
# TestToolCallLoop
# ---------------------------------------------------------------------------


class TestToolCallLoop:
    """llm_tools が読み込まれているときの tool_call ループの統合テスト。

    tool_call ループの詳細なロジックは test_conversation_service.py でテストする。
    ここでは on_message が run_conversation を正しく呼び出すことを確認する。
    """

    @pytest.fixture(autouse=True)
    def setup_llm_tools(self, discord_bot, monkeypatch: pytest.MonkeyPatch) -> None:
        """各テスト前に llm_tools を偽ツールで差し替える"""
        monkeypatch.setattr(discord_bot, "llm_tools", {"llm_obsidian_write": {}})

    async def test_run_conversation_called_with_llm_tools(
        self, discord_bot, mock_message: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """llm_tools あり時に run_conversation が llm_tools と client_type を渡して呼ばれる。"""
        discord_bot.bot.user.mentioned_in.return_value = True
        mock_run = AsyncMock(return_value="完了しました")
        monkeypatch.setattr(discord_bot.message_handler, "run_conversation", mock_run)

        await discord_bot.on_message(mock_message)
        await _flush_discord_tasks(discord_bot)

        mock_run.assert_called_once()
        call_args, call_kwargs = mock_run.call_args
        assert call_args == (discord_bot.llm_tools,)
        assert call_kwargs["client_type"] == "discord"
        assert call_kwargs["override_last_user_content"] is None
        assert call_kwargs["llm_name"] is None
        assert callable(call_kwargs["tool_call_notifier"])
        mock_message.reply.assert_called_once_with("完了しました")

    async def test_run_conversation_called_with_active_llm_name(
        self, discord_bot, mock_message: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`!model` で上書きされたモデル名が run_conversation に渡される。"""
        discord_bot.bot.user.mentioned_in.return_value = True
        mock_run = AsyncMock(return_value="完了しました")
        monkeypatch.setattr(discord_bot.message_handler, "run_conversation", mock_run)
        monkeypatch.setattr(discord_bot.message_handler, "get_active_llm_name", lambda: "deepseek-pro")

        await discord_bot.on_message(mock_message)
        await _flush_discord_tasks(discord_bot)

        assert mock_run.call_args.kwargs["llm_name"] == "deepseek-pro"

    async def test_tool_call_notifier_sends_to_message_channel(
        self, discord_bot, mock_message: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """tool_call_notifier を呼ぶと、そのメッセージのチャンネルに送信される。"""
        discord_bot.bot.user.mentioned_in.return_value = True
        mock_run = AsyncMock(return_value="完了しました")
        monkeypatch.setattr(discord_bot.message_handler, "run_conversation", mock_run)

        await discord_bot.on_message(mock_message)
        await _flush_discord_tasks(discord_bot)

        notifier = mock_run.call_args.kwargs["tool_call_notifier"]
        await notifier("-# 🔧 llm_health_get")

        mock_message.channel.send.assert_called_once_with("-# 🔧 llm_health_get")


# ---------------------------------------------------------------------------
# TestOnInteraction
# ---------------------------------------------------------------------------


def _make_interaction(
    *,
    type_value: object,
    custom_id: str | None = "action:uuid-1",
) -> MagicMock:
    """on_interaction 用の Interaction モックを作る。"""
    interaction = MagicMock()
    interaction.type = type_value
    interaction.data = {"custom_id": custom_id} if custom_id is not None else {}
    interaction.channel = MagicMock(name="interaction_channel")
    interaction.user = MagicMock()
    interaction.user.id = 12345
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


class TestOnInteraction:
    """on_interaction ハンドラのテスト。"""

    @pytest.fixture(autouse=True)
    def setup_interaction_type(self, discord_bot, monkeypatch: pytest.MonkeyPatch) -> None:
        """discord.InteractionType.component を比較可能な値に固定する。"""
        sentinel = object()
        monkeypatch.setattr(
            discord_bot.discord,
            "InteractionType",
            MagicMock(component=sentinel),
        )
        # 共有 sentinel をテストから参照できるようにする
        self._component = sentinel

    @pytest.fixture(autouse=True)
    def reset_button_actions_repo(self, mock_button_actions_repo_instance) -> None:
        """ButtonActionsRepository のモック状態をリセットする。"""
        mock_button_actions_repo_instance.find_one_and_delete.reset_mock()
        mock_button_actions_repo_instance.find_one_and_delete.return_value = None

    async def test_ignores_non_component_interaction(self, discord_bot) -> None:
        """component 以外の interaction は無視する。"""
        interaction = _make_interaction(type_value=object())  # 別の値
        await discord_bot.on_interaction(interaction)
        interaction.response.send_message.assert_not_called()
        interaction.response.defer.assert_not_called()

    async def test_ignores_non_action_custom_id(self, discord_bot) -> None:
        """custom_id が action: で始まらない場合は無視する。"""
        interaction = _make_interaction(type_value=self._component, custom_id="other:foo")
        await discord_bot.on_interaction(interaction)
        interaction.response.send_message.assert_not_called()
        interaction.response.defer.assert_not_called()

    async def test_returns_expired_when_record_missing(
        self, discord_bot, mock_button_actions_repo_instance, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """レコードが見つからない場合「有効期限切れ」を ephemeral で返す。"""
        mock_button_actions_repo_instance.find_one_and_delete.return_value = None
        interaction = _make_interaction(type_value=self._component)
        await discord_bot.on_interaction(interaction)
        interaction.response.send_message.assert_called_once()
        args, kwargs = interaction.response.send_message.call_args
        assert "有効期限切れ" in args[0]
        assert kwargs.get("ephemeral") is True
        interaction.response.defer.assert_not_called()

    async def test_executes_tool_and_reports_pr_url(
        self, discord_bot, mock_button_actions_repo_instance, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """レコードが見つかれば execute_tool_call を呼び出し、memory_entry（PR URL を含む）を followup で通知する。"""
        record = {
            "_id": "uuid-1",
            "tool": "obsidian_write",
            "params": {"notes": [], "commit_message": "x"},
            "title": "テスト",
        }
        mock_button_actions_repo_instance.find_one_and_delete.return_value = record

        mock_execute = AsyncMock(return_value={
            "success": True,
            "tool_name": "obsidian_write",
            "memory_entry": "Obsidian にノートを書き込みました。PR: https://example.com/pr/1",
            "data": {"pr_url": "https://example.com/pr/1"},
            "error": None,
        })
        monkeypatch.setattr(discord_bot.interaction_handler, "execute_tool_call", mock_execute)
        monkeypatch.setattr(discord_bot.interaction_handler, "build_tool_context", lambda: {})

        interaction = _make_interaction(type_value=self._component)
        await discord_bot.on_interaction(interaction)

        interaction.response.defer.assert_called_once()
        mock_execute.assert_called_once()
        args, kwargs = mock_execute.call_args
        assert args[0] == "obsidian_write"
        assert args[1] == record["params"]
        # context に client_type が入る
        ctx = args[3]
        assert ctx["client_type"] == "discord"

        interaction.followup.send.assert_called_once()
        msg = interaction.followup.send.call_args.args[0]
        assert "https://example.com/pr/1" in msg

    async def test_reports_error_when_tool_fails(
        self, discord_bot, mock_button_actions_repo_instance, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ツール実行が失敗した場合 followup でエラー通知する。"""
        record = {
            "_id": "uuid-1",
            "tool": "obsidian_write",
            "params": {},
            "title": "t",
        }
        mock_button_actions_repo_instance.find_one_and_delete.return_value = record

        mock_execute = AsyncMock(return_value={
            "success": False,
            "tool_name": "obsidian_write",
            "memory_entry": None,
            "data": None,
            "error": "Bitbucket API エラー",
        })
        monkeypatch.setattr(discord_bot.interaction_handler, "execute_tool_call", mock_execute)
        monkeypatch.setattr(discord_bot.interaction_handler, "build_tool_context", lambda: {})

        interaction = _make_interaction(type_value=self._component)
        await discord_bot.on_interaction(interaction)

        interaction.followup.send.assert_called_once()
        msg = interaction.followup.send.call_args.args[0]
        assert "エラー" in msg
        assert "Bitbucket API エラー" in msg

    async def test_habit_completion_is_dispatched_as_llm_tool(
        self, discord_bot, mock_button_actions_repo_instance,
        mock_conversation_repo_instance, mock_conversation_service,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """習慣の完了ボタンも特別扱いせず execute_tool_call で実行される。"""
        record = {
            "_id": "uuid-1",
            "tool": "llm_complete_habit",
            "params": {"habit_id": "65f0000000000000000000aa"},
            "title": "トイレ掃除",
        }
        mock_button_actions_repo_instance.find_one_and_delete.return_value = record

        mock_execute = AsyncMock(return_value={
            "success": True,
            "tool_name": "llm_complete_habit",
            "memory_entry": "完了しました",
            "data": {"message": "「トイレ掃除」を完了にしました。"},
            "error": None,
        })
        monkeypatch.setattr(discord_bot.interaction_handler, "execute_tool_call", mock_execute)
        monkeypatch.setattr(discord_bot.interaction_handler, "build_tool_context", lambda: {})

        interaction = _make_interaction(type_value=self._component)
        await discord_bot.on_interaction(interaction)

        args, _ = mock_execute.call_args
        assert args[0] == "llm_complete_habit"
        assert args[1] == record["params"]
        assert args[3]["client_type"] == "discord"

        msg = interaction.followup.send.call_args.args[0]
        assert msg == "「トイレ掃除」を完了にしました。"

    async def test_button_action_is_not_saved_to_conversation_history(
        self, discord_bot, mock_button_actions_repo_instance,
        mock_conversation_repo_instance, mock_conversation_service,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """ボタン経由のツール実行は会話履歴に残さない（run_conversation を経由しない）。"""
        record = {
            "_id": "uuid-1",
            "tool": "llm_complete_habit",
            "params": {"habit_id": "65f0000000000000000000aa"},
            "title": "トイレ掃除",
        }
        mock_button_actions_repo_instance.find_one_and_delete.return_value = record

        mock_execute = AsyncMock(return_value={
            "success": True,
            "tool_name": "llm_complete_habit",
            "memory_entry": "完了しました",
            "data": {"message": "「トイレ掃除」を完了にしました。"},
            "error": None,
        })
        monkeypatch.setattr(discord_bot.interaction_handler, "execute_tool_call", mock_execute)
        monkeypatch.setattr(discord_bot.interaction_handler, "build_tool_context", lambda: {})

        interaction = _make_interaction(type_value=self._component)
        await discord_bot.on_interaction(interaction)

        mock_conversation_service.run_conversation.assert_not_called()
        mock_conversation_repo_instance.save.assert_not_called()

    async def test_approve_disables_buttons_and_runs_command(
        self, discord_bot, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """承認ボタンは編集してボタンを無効化し、承認依頼メッセージの実行内容を実行する。"""
        approval_flow = discord_bot.interaction_handler.approval_flow
        interaction = _make_interaction(
            type_value=self._component, custom_id="approve:100:200"
        )
        interaction.response.edit_message = AsyncMock()
        interaction.message = MagicMock()
        interaction.message.attachments = []
        interaction.message.content = (
            f"承認依頼テキスト\n\n{approval_flow.command_marker()}\n!runtask xxx"
        )

        original_channel = MagicMock()
        mock_get_channel = MagicMock(return_value=original_channel)
        monkeypatch.setattr(discord_bot.bot, "get_channel", mock_get_channel)

        mock_handle = AsyncMock()
        monkeypatch.setattr(approval_flow.command_handler, "handle_command", mock_handle)

        await discord_bot.on_interaction(interaction)

        interaction.response.edit_message.assert_called_once()
        edit_content = interaction.response.edit_message.call_args.kwargs["content"]
        assert "✅ 承認済み" in edit_content
        # 元メッセージの再取得（fetch_message）は行わない
        original_channel.fetch_message.assert_not_called()
        passed_message, passed_content, passed_tools, passed_bot = mock_handle.call_args[0]
        assert passed_content == "!runtask xxx"
        assert passed_message.content == "!runtask xxx"
        assert passed_tools is discord_bot.tools
        assert passed_bot is discord_bot.bot

    async def test_reject_disables_buttons_and_skips_command(
        self, discord_bot, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """拒否ボタンは編集してボタンを無効化し、コマンドは実行しない。"""
        interaction = _make_interaction(
            type_value=self._component, custom_id="reject:100:200"
        )
        interaction.response.edit_message = AsyncMock()
        interaction.message = MagicMock()
        interaction.message.content = "承認依頼テキスト"

        mock_handle = AsyncMock()
        monkeypatch.setattr(discord_bot.interaction_handler.approval_flow.command_handler, "handle_command", mock_handle)

        await discord_bot.on_interaction(interaction)

        interaction.response.edit_message.assert_called_once()
        edit_content = interaction.response.edit_message.call_args.kwargs["content"]
        assert "❌ 拒否済み" in edit_content
        mock_handle.assert_not_called()


# ---------------------------------------------------------------------------
# TestOnReady
# ---------------------------------------------------------------------------


class TestOnReady:
    """on_ready の初期化ループのテスト。"""

    @pytest.fixture(autouse=True)
    def reset_startup_mocks(self, discord_bot, startup_repo_instances) -> None:
        """各テスト前に全リポジトリの init_collection とスケジューラをリセットする。"""
        for repo in startup_repo_instances:
            repo.init_collection.reset_mock(side_effect=True)
        discord_bot.task_handler.start_scheduler.reset_mock()

    async def test_calls_all_repo_init_and_starts_scheduler(self, discord_bot, startup_repo_instances) -> None:
        """全リポジトリの init_collection とスケジューラ起動が呼ばれる。"""
        await discord_bot.on_ready()
        for repo in startup_repo_instances:
            repo.init_collection.assert_called_once()
        discord_bot.task_handler.start_scheduler.assert_called_once_with(
            discord_bot.tools, discord_bot.bot, discord_bot.llm_tools
        )

    async def test_one_failure_does_not_block_others_or_scheduler(
        self, discord_bot, startup_repo_instances, mock_conversation_repo_instance
    ) -> None:
        """1 つの init_collection が例外を投げても、残りとスケジューラ起動は実行される。"""
        mock_conversation_repo_instance.init_collection.side_effect = Exception("boom")

        await discord_bot.on_ready()

        # 失敗したリポジトリも呼ばれている（例外は捕捉される）
        mock_conversation_repo_instance.init_collection.assert_called_once()
        # 残りのリポジトリの init_collection は呼ばれる
        for repo in startup_repo_instances[1:]:
            repo.init_collection.assert_called_once()
        # スケジューラ起動はループの成否に関わらず呼ばれる
        discord_bot.task_handler.start_scheduler.assert_called_once()


# ---------------------------------------------------------------------------
# TestMain
# ---------------------------------------------------------------------------


class TestMain:
    """main() の起動順序テスト。

    拡張が登録した起動タスクを順に await し、その後で bot.start() する。
    """

    async def test_calls_startup_tasks_in_order_then_bot_start(
        self, discord_bot, mock_extension_points, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """登録された startup_tasks を順に await した後、bot.start が呼ばれる。"""
        call_order = []
        task_a = AsyncMock(side_effect=lambda *_args, **_kwargs: call_order.append("a"))
        task_b = AsyncMock(side_effect=lambda *_args, **_kwargs: call_order.append("b"))
        mock_extension_points.get_startup_tasks.return_value = [task_a, task_b]

        mock_start = AsyncMock(side_effect=lambda *_args, **_kwargs: call_order.append("bot_start"))
        monkeypatch.setattr(discord_bot.bot, "start", mock_start)

        await discord_bot.main()

        assert call_order == ["a", "b", "bot_start"]
        task_a.assert_awaited_once_with(
            discord_bot.tools, discord_bot.llm_tools, discord_bot.bot
        )
        task_b.assert_awaited_once_with(
            discord_bot.tools, discord_bot.llm_tools, discord_bot.bot
        )
        mock_start.assert_awaited_once_with(discord_bot._config.env.discord_token)

    async def test_no_startup_tasks_still_calls_bot_start(
        self, discord_bot, mock_extension_points, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """startup_tasks が空でも bot.start は呼ばれる（コア単体起動の想定）。"""
        mock_extension_points.get_startup_tasks.return_value = []
        mock_start = AsyncMock()
        monkeypatch.setattr(discord_bot.bot, "start", mock_start)

        await discord_bot.main()

        mock_start.assert_awaited_once_with(discord_bot._config.env.discord_token)

    async def test_privileged_intents_required_logs_and_exits(
        self, discord_bot, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """PrivilegedIntentsRequired 発生時、原因・対処法を含む ERROR ログを出しプロセスを終了する。"""
        error = _PrivilegedIntentsRequired(None)
        mock_start = AsyncMock(side_effect=error)
        monkeypatch.setattr(discord_bot.bot, "start", mock_start)
        mock_exit = MagicMock(side_effect=SystemExit(1))
        monkeypatch.setattr(discord_bot.sys, "exit", mock_exit)

        with caplog.at_level("ERROR"):
            with pytest.raises(SystemExit):
                await discord_bot.main()

        mock_exit.assert_called_once_with(1)
        error_records = [r for r in caplog.records if r.levelname == "ERROR"]
        assert len(error_records) == 1
        message = error_records[0].getMessage()
        assert "MESSAGE CONTENT INTENT" in message
        assert "Developer Portal" in message
        assert "Privileged Gateway Intents" in message
        assert error_records[0].exc_info is not None
        assert error_records[0].exc_info[1] is error

    async def test_other_start_exception_propagates_unchanged(
        self, discord_bot, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PrivilegedIntentsRequired 以外の例外はそのまま伝播する（既存の fail-fast を維持）。"""
        mock_start = AsyncMock(side_effect=RuntimeError("boom"))
        monkeypatch.setattr(discord_bot.bot, "start", mock_start)

        with pytest.raises(RuntimeError, match="boom"):
            await discord_bot.main()
