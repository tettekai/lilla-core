"""memory_manager.py のテスト。"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

# ---------------------------------------------------------------------------
# モジュールレベルのモックセットアップ
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = "テスト用システムプロンプト"

_DISCORD_PROMPT = "Discordクライアント固有プロンプト"
_LILLA_CLIENT_PROMPT = "lilla-client固有プロンプト"


@pytest.fixture
def mock_cfg() -> MagicMock:
    """MemoryManager が参照する AppConfig モック。"""
    cfg = MagicMock()
    cfg.memory.conversation_ttl_hours = 72
    cfg.memory.max_history_turns = 10
    cfg.system_prompt = _SYSTEM_PROMPT
    cfg.memory.history_days = 2
    cfg.memory.session_memory_ttl_hours = 3
    cfg.discord_client_prompt = _DISCORD_PROMPT
    cfg.lilla_client_prompt = _LILLA_CLIENT_PROMPT
    return cfg


@pytest.fixture
def mock_conv_repo() -> MagicMock:
    repo = MagicMock()
    repo.ensure_indexes = AsyncMock()
    repo.save = AsyncMock()
    repo.load_with_time = AsyncMock(return_value=[])
    repo.load_with_time_since = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def mock_memo_repo() -> MagicMock:
    repo = MagicMock()
    repo.ensure_indexes = AsyncMock()
    repo.get_active = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def mock_tool_cache_repo() -> MagicMock:
    repo = MagicMock()
    repo.ensure_indexes = AsyncMock()
    repo.get_all_valid = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def mock_session_memory() -> MagicMock:
    mock = MagicMock()
    mock.get = MagicMock(return_value=None)
    return mock


@pytest.fixture
def with_mocked_modules(
    mock_conv_repo: MagicMock,
    mock_memo_repo: MagicMock,
    mock_tool_cache_repo: MagicMock,
    mock_session_memory: MagicMock,
):
    """依存モジュールを patch.dict で差し替える。"""
    with patch.dict(
        sys.modules,
        {
            "lilla_core.core.config": MagicMock(
                AppConfig=MagicMock(),
                # datetime_utils.local_timezone() が OS のローカルタイムゾーンへ
                # 解決するよう、ui.timezone だけ実値（未指定）にしておく
                get_config=MagicMock(return_value=MagicMock(ui=MagicMock(timezone=None))),
            ),
            "motor": MagicMock(),
            "motor.motor_asyncio": MagicMock(),
            "pymongo": MagicMock(),
            "lilla_core.repository.conversation_repository": MagicMock(
                get_conversation_repo=MagicMock(return_value=mock_conv_repo)
            ),
            "lilla_core.repository.user_memo_repository": MagicMock(
                get_user_memo_repo=MagicMock(return_value=mock_memo_repo)
            ),
            "lilla_core.repository.tool_cache_repository": MagicMock(
                get_tool_cache_repo=MagicMock(return_value=mock_tool_cache_repo)
            ),
            "lilla_core.services.session_memory_manager": MagicMock(
                get_session_memory_manager=MagicMock(return_value=mock_session_memory)
            ),
        },
    ):
        yield


@pytest.fixture
def mm_module(with_mocked_modules):
    """patch.dict 有効後に memory_manager をロードする。"""
    import importlib
    sys.modules.pop("lilla_core.services.memory_manager", None)
    loaded = importlib.import_module("lilla_core.services.memory_manager")
    yield loaded
    sys.modules.pop("lilla_core.services.memory_manager", None)


@pytest.fixture()
def manager(
    mm_module,
    mock_cfg: MagicMock,
    mock_conv_repo: MagicMock,
    mock_memo_repo: MagicMock,
    mock_tool_cache_repo: MagicMock,
    mock_session_memory: MagicMock,
):
    """テスト用 MemoryManager インスタンスを返す。"""
    mgr = mm_module.MemoryManager(mock_cfg)
    mgr._conv_repo = mock_conv_repo
    mgr._memo_repo = mock_memo_repo
    mgr._tool_cache_repo = mock_tool_cache_repo
    mgr._session_memory = mock_session_memory
    mgr._max_history_turns = 10
    return mgr


@pytest.fixture
def client_prompt_providers(mock_cfg: MagicMock, make_extension, use_extensions):
    """クライアント固有プロンプトのプロバイダを拡張として登録する。

    `build_system_prompt` はクライアント種別ごとのプロンプトを拡張の
    `client_prompt_providers()` から引くため、テスト側で拡張を 1 つ登録する。
    "discord" はコア内蔵のデフォルトもあるが、その実装は本物の
    `get_config()` を見るためモック設定を返さない。拡張側を優先させる。
    """
    use_extensions(make_extension(
        "prompt-pack",
        client_prompt_providers={
            "discord": [lambda: mock_cfg.discord_client_prompt],
            "lilla-client": [lambda: mock_cfg.lilla_client_prompt],
        },
    ))


# ---------------------------------------------------------------------------
# テスト
# ---------------------------------------------------------------------------


class TestAddConversation:
    async def test_delegates_to_conv_repo(self, manager, mock_conv_repo, mock_memo_repo, mock_tool_cache_repo, mock_session_memory) -> None:
        """add_conversation が ConversationRepository.save を呼ぶ。"""
        msg = {"role": "user", "content": "hello"}
        await manager.add_conversation(msg)
        mock_conv_repo.save.assert_called_once_with(
            msg, tags=None, discord_channel_id=None, discord_message_ids=None
        )

    async def test_passes_tags_and_discord_message_info(
        self, manager, mock_conv_repo, mock_memo_repo, mock_tool_cache_repo, mock_session_memory
    ) -> None:
        """tags・Discord メッセージ情報がそのまま save へ渡される。"""
        msg = {"role": "assistant", "content": "hello"}
        await manager.add_conversation(
            msg,
            tags=["toolresult", "dirty"],
            discord_channel_id=555,
            discord_message_ids=[10, 11],
        )
        mock_conv_repo.save.assert_called_once_with(
            msg,
            tags=["toolresult", "dirty"],
            discord_channel_id=555,
            discord_message_ids=[10, 11],
        )

    async def test_returns_saved_id(self, manager, mock_conv_repo, mock_memo_repo, mock_tool_cache_repo, mock_session_memory) -> None:
        """save が返した _id をそのまま返す。"""
        mock_conv_repo.save.return_value = "65f0000000000000000000aa"
        assert await manager.add_conversation({"role": "user", "content": "hi"}) == (
            "65f0000000000000000000aa"
        )


class TestLoadConversationHistoryWithTimestamps:
    async def test_prefixes_string_content(self, manager, mock_conv_repo, mock_memo_repo, mock_tool_cache_repo, mock_session_memory) -> None:
        """str content の先頭にタイムスタンプが付与される。"""
        t = datetime(2026, 4, 2, 10, 0, 0, tzinfo=timezone.utc)
        mock_conv_repo.load_with_time_since.return_value = [
            {"message": {"role": "user", "content": "hello"}, "time": t},
        ]
        result = await manager.load_conversation_history_with_timestamps()
        assert len(result) == 1
        assert result[0]["role"] == "user"
        # ローカル TZ でフォーマットされるため曜日/時刻は環境依存。プレフィックス形式のみ検証。
        content = result[0]["content"]
        assert content.endswith("hello")
        assert content.startswith("[")
        assert "] " in content

    async def test_timestamp_converted_to_local_timezone(
        self, manager, mock_conv_repo, mock_memo_repo, mock_tool_cache_repo, mock_session_memory
    ) -> None:
        """aware UTC のタイムスタンプがローカルタイムゾーンに変換されて付与される。

        tz_aware=True のクライアントが返す aware UTC datetime を前提とした
        H-3（naive UTC のローカル誤解釈）の回帰テスト。実行環境の TZ に
        依存しないよう、期待値はローカル変換で動的に計算する。
        """
        t = datetime(2026, 4, 2, 10, 0, 0, tzinfo=timezone.utc)
        mock_conv_repo.load_with_time_since.return_value = [
            {"message": {"role": "user", "content": "hello"}, "time": t},
        ]
        result = await manager.load_conversation_history_with_timestamps()
        from lilla_core.utils.datetime_utils import local_timezone
        expected_prefix = f"[{t.astimezone(local_timezone()).strftime('%b %d %H:%M')}] "
        assert result[0]["content"] == expected_prefix + "hello"

    async def test_empty_history(self, manager, mock_conv_repo, mock_memo_repo, mock_tool_cache_repo, mock_session_memory) -> None:
        """会話履歴が空のとき空リストを返す。"""
        mock_conv_repo.load_with_time_since.return_value = []
        result = await manager.load_conversation_history_with_timestamps()
        assert result == []

    async def test_history_window_starts_at_configured_timezone_midnight(
        self, manager, mock_conv_repo, mock_memo_repo, mock_tool_cache_repo, mock_session_memory
    ) -> None:
        """履歴を遡る起点が `ui.timezone` の日付の 0:00 になる（OS の TZ は見ない）。"""
        sys.modules["lilla_core.core.config"].get_config.return_value.ui.timezone = "Asia/Tokyo"
        mock_conv_repo.load_with_time_since.return_value = []

        await manager.load_conversation_history_with_timestamps()

        tokyo = ZoneInfo("Asia/Tokyo")
        since_local = mock_conv_repo.load_with_time_since.call_args[0][0].astimezone(tokyo)
        assert (since_local.hour, since_local.minute, since_local.second) == (0, 0, 0)
        # history_days = 2 なので「今日を含む 2 日分」＝今日の前日 0:00 が起点になる
        assert since_local.date() == datetime.now(tokyo).date() - timedelta(days=1)

    async def test_prefixes_list_content_text_block(
        self, manager, mock_conv_repo, mock_memo_repo, mock_tool_cache_repo, mock_session_memory
    ) -> None:
        """content がリストのとき先頭の text ブロックにプレフィックスが付与される。"""
        t = datetime(2026, 4, 2, 10, 0, 0, tzinfo=timezone.utc)
        mock_conv_repo.load_with_time_since.return_value = [
            {
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": "x"}},
                        {"type": "text", "text": "hello"},
                    ],
                },
                "time": t,
            },
        ]
        result = await manager.load_conversation_history_with_timestamps()
        content = result[0]["content"]
        assert content[0]["type"] == "image_url"
        assert content[1]["type"] == "text"
        assert content[1]["text"].endswith("hello")
        assert content[1]["text"].startswith("[")

    async def test_inserts_text_block_when_no_text_present(
        self, manager, mock_conv_repo, mock_memo_repo, mock_tool_cache_repo, mock_session_memory
    ) -> None:
        """text ブロックが存在しないリスト content には新たな text ブロックが挿入される。"""
        t = datetime(2026, 4, 2, 10, 0, 0, tzinfo=timezone.utc)
        mock_conv_repo.load_with_time_since.return_value = [
            {
                "message": {
                    "role": "user",
                    "content": [{"type": "image_url", "image_url": {"url": "x"}}],
                },
                "time": t,
            },
        ]
        result = await manager.load_conversation_history_with_timestamps()
        content = result[0]["content"]
        assert content[0]["type"] == "text"
        assert content[0]["text"].startswith("[")
        assert content[1]["type"] == "image_url"

    async def test_does_not_mutate_original_message(
        self, manager, mock_conv_repo, mock_memo_repo, mock_tool_cache_repo, mock_session_memory
    ) -> None:
        """元の message dict を変更しない。"""
        t = datetime(2026, 4, 2, 10, 0, 0, tzinfo=timezone.utc)
        original = {"role": "user", "content": "hello"}
        mock_conv_repo.load_with_time_since.return_value = [
            {"message": original, "time": t},
        ]
        await manager.load_conversation_history_with_timestamps()
        assert original == {"role": "user", "content": "hello"}


class TestBuildSystemPrompt:
    async def test_contains_base_prompt(self, manager, mock_conv_repo, mock_memo_repo, mock_tool_cache_repo, mock_session_memory) -> None:
        """base_prompt が返り値に含まれる。"""
        result = await manager.build_system_prompt()
        assert result.startswith(_SYSTEM_PROMPT)

    async def test_contains_current_time(self, manager, mock_conv_repo, mock_memo_repo, mock_tool_cache_repo, mock_session_memory) -> None:
        """現在時刻が返り値に含まれる。"""
        result = await manager.build_system_prompt()
        assert "Current time:" in result
        assert ":xx" in result

    async def test_contains_weekday_en(self, manager, mock_conv_repo, mock_memo_repo, mock_tool_cache_repo, mock_session_memory) -> None:
        """曜日が英語で含まれる。"""
        import calendar
        from datetime import datetime
        weekday_en = datetime.now().strftime("%A")
        result = await manager.build_system_prompt()
        assert weekday_en in result

    async def test_no_history_section_when_empty(self, manager, mock_conv_repo, mock_memo_repo, mock_tool_cache_repo, mock_session_memory) -> None:
        """会話履歴が空のとき履歴セクションは含まれない。"""
        mock_conv_repo.load_with_time_since.return_value = []
        result = await manager.build_system_prompt()
        assert "会話履歴" not in result

    async def test_no_history_section_even_when_history_present(
        self, manager, mock_conv_repo, mock_memo_repo, mock_tool_cache_repo, mock_session_memory
    ) -> None:
        """会話履歴があってもシステムプロンプトには履歴テキストが含まれない。"""
        t = datetime(2026, 4, 2, 10, 30, 0, tzinfo=timezone.utc)
        mock_conv_repo.load_with_time_since.return_value = [
            {"message": {"role": "user", "content": "こんにちは"}, "time": t},
            {"message": {"role": "assistant", "content": "こんにちは！"}, "time": t},
        ]
        result = await manager.build_system_prompt()
        assert "会話履歴" not in result
        assert "こんにちは" not in result

    async def test_no_memo_section_when_empty(self, manager, mock_conv_repo, mock_memo_repo, mock_tool_cache_repo, mock_session_memory) -> None:
        """有効なメモがないときメモセクションは含まれない。"""
        mock_memo_repo.get_active.return_value = []
        result = await manager.build_system_prompt()
        assert "User's Instructions" not in result

    async def test_memo_section_included_when_present(
        self, manager, mock_conv_repo, mock_memo_repo, mock_tool_cache_repo, mock_session_memory
    ) -> None:
        """有効なメモがある場合はメモセクションが含まれる。"""
        mock_memo_repo.get_active.return_value = [
            {"content": "夜は23時までに切り上げて", "enabled": True},
        ]
        result = await manager.build_system_prompt()
        assert "User's Instructions" in result
        assert "夜は23時までに切り上げて" in result

    async def test_sections_joined_by_double_newline(
        self, manager, mock_conv_repo, mock_memo_repo, mock_tool_cache_repo, mock_session_memory
    ) -> None:
        """各セクションは二重改行で区切られる。"""
        mock_memo_repo.get_active.return_value = [
            {"content": "テストメモ", "enabled": True},
        ]
        result = await manager.build_system_prompt()
        assert "\n\n" in result

    async def test_discord_client_prompt_included_for_discord(
        self, manager, client_prompt_providers, mock_conv_repo, mock_memo_repo,
        mock_tool_cache_repo, mock_session_memory
    ) -> None:
        """client_type="discord" のとき Discord 固有プロンプトが含まれる。"""
        result = await manager.build_system_prompt(client_type="discord")
        assert _DISCORD_PROMPT in result
        assert _LILLA_CLIENT_PROMPT not in result

    async def test_lilla_client_prompt_included_for_lilla_client(
        self, manager, client_prompt_providers, mock_conv_repo, mock_memo_repo,
        mock_tool_cache_repo, mock_session_memory
    ) -> None:
        """client_type="lilla-client" のとき lilla-client 固有プロンプトが含まれる。"""
        result = await manager.build_system_prompt(client_type="lilla-client")
        assert _LILLA_CLIENT_PROMPT in result
        assert _DISCORD_PROMPT not in result

    async def test_no_client_prompt_when_client_type_empty(
        self, manager, client_prompt_providers, mock_conv_repo, mock_memo_repo,
        mock_tool_cache_repo, mock_session_memory
    ) -> None:
        """client_type が空のときクライアント固有プロンプトは含まれない。"""
        result = await manager.build_system_prompt(client_type="")
        assert _DISCORD_PROMPT not in result
        assert _LILLA_CLIENT_PROMPT not in result

    async def test_client_prompt_placed_after_system_prompt(
        self, manager, client_prompt_providers, mock_conv_repo, mock_memo_repo,
        mock_tool_cache_repo, mock_session_memory
    ) -> None:
        """クライアント固有プロンプトは system_prompt の直後に挿入される。"""
        result = await manager.build_system_prompt(client_type="discord")
        system_pos = result.index(_SYSTEM_PROMPT)
        discord_pos = result.index(_DISCORD_PROMPT)
        assert system_pos < discord_pos

    async def test_discord_prompt_before_extra_prompt(
        self, manager, client_prompt_providers, mock_conv_repo, mock_memo_repo,
        mock_tool_cache_repo, mock_session_memory
    ) -> None:
        """Discord 固有プロンプトは extra_prompt より前に配置される。"""
        extra = "追加プロンプト"
        result = await manager.build_system_prompt(extra_prompt=extra, client_type="discord")
        discord_pos = result.index(_DISCORD_PROMPT)
        extra_pos = result.index(extra)
        assert discord_pos < extra_pos

    async def test_no_client_prompt_for_unregistered_client_type(
        self, manager, client_prompt_providers, mock_conv_repo, mock_memo_repo,
        mock_tool_cache_repo, mock_session_memory
    ) -> None:
        """プロバイダ未登録の client_type では例外を出さず何も付与しない。

        コア単体起動（`LILLA_EXTENSIONS` 未設定）で "lilla-client" の
        プロバイダが登録されていない状況に相当する。
        """
        result = await manager.build_system_prompt(client_type="unknown-client")

        assert _LILLA_CLIENT_PROMPT not in result
        assert _SYSTEM_PROMPT in result

    async def test_client_prompt_provider_is_reevaluated_each_call(
        self, manager, make_extension, use_extensions, mock_conv_repo, mock_memo_repo,
        mock_tool_cache_repo, mock_session_memory
    ) -> None:
        """プロバイダは呼び出しのたびに評価される（値をキャッシュしない）。"""
        provider = MagicMock(side_effect=["1回目のプロンプト", "2回目のプロンプト"])
        use_extensions(make_extension(
            "prompt-pack", client_prompt_providers={"discord": [provider]}
        ))

        first = await manager.build_system_prompt(client_type="discord")
        second = await manager.build_system_prompt(client_type="discord")

        assert "1回目のプロンプト" in first
        assert "2回目のプロンプト" in second
        assert provider.call_count == 2

    async def test_stm_section_not_embedded(
        self, manager, mock_conv_repo, mock_memo_repo, mock_tool_cache_repo, mock_session_memory
    ) -> None:
        """短期記憶（行動履歴）のセクションはシステムプロンプトに出力されない（埋め込み廃止）。"""
        result = await manager.build_system_prompt()
        assert "最近の記憶" not in result
        assert "行動履歴" not in result

    async def test_no_session_memory_section_when_unset(
        self, manager, mock_conv_repo, mock_memo_repo, mock_tool_cache_repo, mock_session_memory
    ) -> None:
        """セッションメモリが未設定のときセッションメモリセクションは含まれない。"""
        mock_session_memory.get.return_value = None
        result = await manager.build_system_prompt()
        assert "SESSION_MEMORY" not in result

    async def test_session_memory_section_included_when_present(
        self, manager, mock_conv_repo, mock_memo_repo, mock_tool_cache_repo, mock_session_memory
    ) -> None:
        """有効なセッションメモリがある場合はブロック形式で含まれる。"""
        mock_session_memory.get.return_value = "作業中: XXXを実装中"
        result = await manager.build_system_prompt()
        assert "---SESSION_MEMORY---" in result
        assert "作業中: XXXを実装中" in result
        assert "---END_SESSION_MEMORY---" in result

    async def test_session_memory_section_exact_block_format(
        self, manager, mock_conv_repo, mock_memo_repo, mock_tool_cache_repo, mock_session_memory
    ) -> None:
        """埋め込み形式は ---SESSION_MEMORY---\\n本文\\n---END_SESSION_MEMORY--- のまま変わらない。

        LLM 出力側は META ブロックの set_session_memory アクションに移行したが、
        システムプロンプトへの埋め込み形式は従来どおりであることを保証する。
        """
        mock_session_memory.get.return_value = "作業中: XXXを実装中"
        result = await manager.build_system_prompt()
        assert (
            "---SESSION_MEMORY---\n作業中: XXXを実装中\n---END_SESSION_MEMORY---" in result
        )

    async def test_session_memory_multiline_block_format(
        self, manager, mock_conv_repo, mock_memo_repo, mock_tool_cache_repo, mock_session_memory
    ) -> None:
        """複数行のセッションメモリもマーカーで挟んだ形式でそのまま埋め込まれる。"""
        mock_session_memory.get.return_value = "1行目\n2行目"
        result = await manager.build_system_prompt()
        assert "---SESSION_MEMORY---\n1行目\n2行目\n---END_SESSION_MEMORY---" in result

    async def test_session_memory_section_after_memo_before_time(
        self, manager, mock_conv_repo, mock_memo_repo, mock_tool_cache_repo, mock_session_memory
    ) -> None:
        """セッションメモリセクションは指示メモの後、現在時刻の前に配置される。"""
        mock_memo_repo.get_active.return_value = [{"content": "メモ1"}]
        mock_session_memory.get.return_value = "作業中: XXXを実装中"
        result = await manager.build_system_prompt()
        memo_pos = result.index("User's Instructions")
        session_pos = result.index("---SESSION_MEMORY---")
        time_pos = result.index("Current time:")
        assert memo_pos < session_pos < time_pos


class TestCachedToolResultsSection:
    async def test_no_cache_section_when_empty(
        self, manager, mock_conv_repo, mock_memo_repo, mock_tool_cache_repo, mock_session_memory
    ) -> None:
        """有効なキャッシュがないとき Cached Tool Results セクションは含まれない。"""
        mock_tool_cache_repo.get_all_valid.return_value = []
        result = await manager.build_system_prompt()
        assert "Cached Tool Results" not in result

    async def test_cache_section_included_when_present(
        self, manager, mock_conv_repo, mock_memo_repo, mock_tool_cache_repo, mock_session_memory
    ) -> None:
        """有効なキャッシュがある場合は Cached Tool Results セクションが含まれる。"""
        mock_tool_cache_repo.get_all_valid.return_value = [
            {
                "tool_name": "llm_personal_info",
                "args_key": '{"info_type": "FAVORITE_FOODS"}',
                "data": "寿司、焼肉、カレー",
            }
        ]
        result = await manager.build_system_prompt()
        assert "## Cached Tool Results" in result
        assert "llm_personal_info" in result
        assert '{"info_type": "FAVORITE_FOODS"}' in result
        assert "寿司、焼肉、カレー" in result


class TestResolveClientPrompt:
    """クライアント固有プロンプトの解決順（拡張 → コア内蔵 → 何も付けない）。"""

    def test_extension_provider_wins(self, mm_module, make_extension, use_extensions) -> None:
        """拡張が "discord" を出していればそれを使う。"""
        use_extensions(make_extension(
            "pack", client_prompt_providers={"discord": [lambda: "custom-discord-prompt"]}
        ))
        assert mm_module._resolve_client_prompt("discord") == "custom-discord-prompt"

    def test_falls_back_to_core_default_for_discord(
        self, mm_module, use_extensions, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """拡張が 0 個でも "discord" にはコア内蔵のプロンプトが付く。"""
        use_extensions()
        cfg = MagicMock()
        cfg.discord_client_prompt = "core-discord-prompt"
        monkeypatch.setattr(mm_module, "get_config", lambda: cfg)
        assert mm_module._resolve_client_prompt("discord") == "core-discord-prompt"

    def test_multiple_providers_are_joined_in_load_order(
        self, mm_module, make_extension, use_extensions
    ) -> None:
        """複数の拡張が同じ client_type に出したプロンプトは、ロード順に空行区切りで連結する。"""
        use_extensions(
            make_extension("a", client_prompt_providers={"discord": [lambda: "first"]}),
            make_extension("b", client_prompt_providers={"discord": [lambda: "", lambda: "third"]}),
        )
        assert mm_module._resolve_client_prompt("discord") == "first\n\nthird"

    def test_all_empty_providers_fall_back_to_nothing_not_core_default(
        self, mm_module, make_extension, use_extensions, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """拡張が "discord" を出していれば、全て空でもコア内蔵へは戻らない。"""
        use_extensions(make_extension("a", client_prompt_providers={"discord": [lambda: ""]}))
        cfg = MagicMock()
        cfg.discord_client_prompt = "core-discord-prompt"
        monkeypatch.setattr(mm_module, "get_config", lambda: cfg)
        assert mm_module._resolve_client_prompt("discord") == ""

    def test_no_prompt_for_other_client_types(self, mm_module, use_extensions) -> None:
        """内蔵デフォルトは "discord" だけで、他の client_type には付かない。"""
        use_extensions()
        assert mm_module._resolve_client_prompt("lilla-client") == ""
        assert mm_module._resolve_client_prompt("") == ""

    def test_extension_provider_for_other_client_type(
        self, mm_module, make_extension, use_extensions
    ) -> None:
        """拡張が増やした client_type も同じ経路で解決できる。"""
        use_extensions(make_extension(
            "pack", client_prompt_providers={"lilla-client": [lambda: "lilla-client-prompt"]}
        ))
        assert mm_module._resolve_client_prompt("lilla-client") == "lilla-client-prompt"

    def test_provider_is_reevaluated_each_call(
        self, mm_module, make_extension, use_extensions
    ) -> None:
        """プロバイダは呼ぶたびに評価される（戻り値をキャッシュしない）。"""
        values = iter(["first", "second"])
        use_extensions(make_extension(
            "pack", client_prompt_providers={"discord": [lambda: next(values)]}
        ))
        assert mm_module._resolve_client_prompt("discord") == "first"
        assert mm_module._resolve_client_prompt("discord") == "second"


class TestGetMemoryManager:
    def test_returns_memory_manager_instance(self, mm_module) -> None:
        """get_memory_manager が MemoryManager インスタンスを返す。"""
        from unittest.mock import patch, MagicMock

        mock_cfg = MagicMock()
        mock_cfg.memory.max_history_turns = 10
        mock_cfg.system_prompt = "prompt"

        mm_module.get_memory_manager.cache_clear()
        with patch.object(mm_module, "get_config", return_value=mock_cfg):
            instance = mm_module.get_memory_manager()
        assert isinstance(instance, mm_module.MemoryManager)

    def test_returns_same_instance_on_repeated_calls(self, mm_module) -> None:
        """get_memory_manager を複数回呼んでも同じインスタンスを返す。"""
        from unittest.mock import patch, MagicMock

        mock_cfg = MagicMock()
        mock_cfg.memory.max_history_turns = 10
        mock_cfg.system_prompt = "prompt"

        mm_module.get_memory_manager.cache_clear()
        with patch.object(mm_module, "get_config", return_value=mock_cfg):
            first = mm_module.get_memory_manager()
            second = mm_module.get_memory_manager()
        assert first is second


class TestRegisteredChannelSection:
    """登録チャンネルでの会話にだけチャンネル名の一節が付くことを検証する。"""

    @pytest.fixture
    def registered_channels(self, mock_cfg: MagicMock):
        """`discord.channels` に 1 件登録された状態の設定モックを返す。"""
        entry = MagicMock()
        entry.name = "dev"
        entry.channel_id = "100"
        entry.mention_optional = True
        mock_cfg.discord.find_channel_by_id = MagicMock(
            side_effect=lambda channel_id: entry if str(channel_id) == "100" else None
        )
        return mock_cfg

    async def test_registered_channel_name_is_included(
        self, manager, registered_channels, mock_conv_repo, mock_memo_repo,
        mock_tool_cache_repo, mock_session_memory,
    ) -> None:
        result = await manager.build_system_prompt(discord_channel_id=100)
        assert "## Current Channel" in result
        assert '"dev"' in result

    async def test_unregistered_channel_has_no_section(
        self, manager, registered_channels, mock_conv_repo, mock_memo_repo,
        mock_tool_cache_repo, mock_session_memory,
    ) -> None:
        result = await manager.build_system_prompt(discord_channel_id=999)
        assert "## Current Channel" not in result

    async def test_no_channel_id_has_no_section(
        self, manager, registered_channels, mock_conv_repo, mock_memo_repo,
        mock_tool_cache_repo, mock_session_memory,
    ) -> None:
        """DM や Discord 以外の呼び出し（`discord_channel_id` 未指定）では出さない。"""
        result = await manager.build_system_prompt()
        assert "## Current Channel" not in result
