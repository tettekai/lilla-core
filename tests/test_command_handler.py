"""command_handler.py のテスト（コマンドのディスパッチと抽出）。

個別コマンドのロジックは `src/commands/` 配下にあり、
それぞれ `tests/test_command_*.py` でテストする。
"""
from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from lilla_core.commands import load_all_commands, registry
from lilla_core.commands.context import CommandContext
from lilla_core.handlers import command_handler
from lilla_core.services.skills import format_skill_instruction

_UUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

# ディスパッチ・抽出はレジストリの内容に依存するため、実コマンドを読み込んでおく
load_all_commands()


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


def _make_message() -> MagicMock:
    msg = MagicMock()
    msg.reply = AsyncMock()
    return msg


@pytest.fixture()
def isolated_registry(monkeypatch: pytest.MonkeyPatch):
    """レジストリをコピーに差し替え、テスト内の登録が他テストへ漏れないようにする。

    `register_command` は同名の別ハンドラを拒否するため、実コマンド名を
    モックへ差し替えるテストは戻り値のヘルパーで直接差し込む。
    """
    commands = dict(registry._KNOWN_COMMANDS)
    monkeypatch.setattr(registry, "_KNOWN_COMMANDS", commands)
    monkeypatch.setattr(
        registry, "_DISCORD_ONLY_COMMANDS", set(registry._DISCORD_ONLY_COMMANDS)
    )

    def override(name: str, handler):
        commands[name] = handler
        return handler

    return override


@pytest.fixture()
def mock_notify_error(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """エラー通知（ERROR ログ + error_channel_id）をモックに差し替える。"""
    mock = AsyncMock()
    monkeypatch.setattr(command_handler, "notify_error", mock)
    return mock


class _DummyContext(CommandContext):
    """Discord 以外のクライアントを模したテスト用のコマンドコンテキスト。"""

    def __init__(
        self,
        *,
        client_type: str = "test-client",
        is_owner: bool = True,
        attachments: list | None = None,
    ) -> None:
        super().__init__(
            client_type=client_type,
            author_id="user-1",
            is_owner=is_owner,
            attachments=attachments,
        )
        self.replies: list[str] = []
        self.errors: list[str] = []
        self.conversations: list[str] = []

    async def deliver_reply(self, text: str) -> None:
        self.replies.append(text)

    async def deliver_error(self, text: str) -> None:
        self.errors.append(text)

    async def deliver_conversation(self, content: str) -> None:
        self.conversations.append(content)


# ---------------------------------------------------------------------------
# TestHandleCommand
# ---------------------------------------------------------------------------


class TestHandleCommand:
    async def test_empty_command_replies_error(self) -> None:
        """コマンド未入力時の案内はエラーではないので従来通り reply する。"""
        msg = _make_message()
        result = await command_handler.handle_command(msg, "!", {}, MagicMock())
        msg.reply.assert_called_once()
        assert "コマンド" in msg.reply.call_args[0][0]
        assert result.status == "error"

    async def test_dispatches_to_registered_handler(self, isolated_registry) -> None:
        """コマンド名の完全一致でレジストリのハンドラへ委譲する。"""
        handler = isolated_registry("runtask", AsyncMock())
        msg = _make_message()
        tools = {"task_tool": {"trigger": "task"}}
        bot = MagicMock()

        result = await command_handler.handle_command(msg, "!runtask my_tool", tools, bot)

        handler.assert_called_once()
        ctx, arg, passed_tools, passed_bot = handler.call_args[0]
        assert isinstance(ctx, command_handler.DiscordCommandContext)
        assert ctx.discord_message is msg
        assert ctx.client_type == "discord"
        assert (arg, passed_tools, passed_bot) == ("my_tool", tools, bot)
        assert result.status == "ok"

    async def test_passes_empty_arg_without_argument(self, isolated_registry) -> None:
        """引数のないコマンドには空文字列が渡る。"""
        handler = isolated_registry("cleardirty", AsyncMock())
        msg = _make_message()
        bot = MagicMock()

        await command_handler.handle_command(msg, "!cleardirty", {}, bot)

        assert handler.call_args[0][1:] == ("", {}, bot)

    async def test_passes_multiline_arg(self, isolated_registry) -> None:
        """2 行目以降も引数としてそのまま渡る（toolresult の本文など）。"""
        handler = isolated_registry("toolresult", AsyncMock())
        msg = _make_message()

        await command_handler.handle_command(msg, f"!toolresult {_UUID}\n本文", {}, MagicMock())

        assert handler.call_args[0][1] == f"{_UUID}\n本文"

    async def test_splits_only_once(self, isolated_registry) -> None:
        """分割は最初の空白 1 回だけで、引数内の空白は保持される。"""
        handler = isolated_registry("mongodata", AsyncMock())
        msg = _make_message()

        await command_handler.handle_command(msg, '!mongodata {"a": 1, "b": 2}', {}, MagicMock())

        assert handler.call_args[0][1] == '{"a": 1, "b": 2}'

    async def test_old_colon_format_is_unknown_command(
        self, mock_notify_error: AsyncMock
    ) -> None:
        """廃止したコロン付き形式は未知コマンドとして扱われる。"""
        msg = _make_message()
        await command_handler.handle_command(msg, "!runtask:my_tool", {}, MagicMock())
        mock_notify_error.assert_called_once()
        assert "未知" in mock_notify_error.call_args[0][2]
        msg.reply.assert_not_called()

    async def test_unknown_command_notifies_error(self, mock_notify_error: AsyncMock) -> None:
        """未知コマンドは元チャンネルに返信せず、エラー通知のみ行う。"""
        msg = _make_message()
        bot = MagicMock()
        await command_handler.handle_command(msg, "!unknown", {}, bot)
        mock_notify_error.assert_called_once()
        assert mock_notify_error.call_args[0][0] is bot
        assert "未知" in mock_notify_error.call_args[0][2]
        msg.reply.assert_not_called()

    async def test_unknown_command_lists_registered_commands(
        self, mock_notify_error: AsyncMock
    ) -> None:
        """案内文（2 行目）には登録済みコマンドが並ぶ。"""
        msg = _make_message()
        await command_handler.handle_command(msg, "!cleardm", {}, MagicMock())
        supported = mock_notify_error.call_args[0][2].split("\n")[1]
        for name in registry.known_command_names():
            assert f"!{name}" in supported
        assert "!cleardm" not in supported

    async def test_exception_in_handler_notifies_error(
        self, isolated_registry, mock_notify_error: AsyncMock
    ) -> None:
        """ハンドラ内の例外も元チャンネルに返信せず、エラー通知のみ行う。"""
        error = Exception("boom")
        isolated_registry("runtask", AsyncMock(side_effect=error))
        msg = _make_message()
        bot = MagicMock()
        await command_handler.handle_command(msg, "!runtask x", {}, bot)
        mock_notify_error.assert_called_once_with(
            bot, "コマンド処理中にエラーが発生しました", error
        )
        msg.reply.assert_not_called()


# ---------------------------------------------------------------------------
# TestDispatchCommand（Discord 以外のコンテキストから呼ぶ）
# ---------------------------------------------------------------------------


@pytest.fixture()
def model_config(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """`!model` が読む設定を差し替え、上書き状態をテストの前後で初期化する。"""
    model_command = importlib.import_module("lilla_core.commands.model")
    runtime_state = importlib.import_module("lilla_core.core.runtime_state")
    config = MagicMock()
    config.llm.default = "default-llm"
    config.llm.providers = {"default-llm": MagicMock(), "other-llm": MagicMock()}
    monkeypatch.setattr(model_command, "get_config", lambda: config)
    runtime_state.reset_active_llm_name()
    yield config
    runtime_state.reset_active_llm_name()


@pytest.fixture()
def skill_repo(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """`!skill` が引くスキルリポジトリを差し替える。"""
    repo = MagicMock()
    repo.find_by_name = AsyncMock(return_value=None)
    repo.get_enabled = AsyncMock(return_value=[])
    repo_module = MagicMock()
    repo_module.get_skill_repo = MagicMock(return_value=repo)
    monkeypatch.setitem(sys.modules, "lilla_core.repository.skill_repository", repo_module)
    return repo


class TestDispatchCommand:
    async def test_model_lists_providers_via_reply(self, model_config: MagicMock) -> None:
        """引数なしの `!model` は一覧を `reply` で返す。"""
        ctx = _DummyContext()

        result = await command_handler.dispatch_command("!model", ctx, tools={}, bot=MagicMock())

        assert result.status == "ok"
        assert result.replied
        assert len(ctx.replies) == 1
        assert "default-llm" in ctx.replies[0]
        assert "other-llm" in ctx.replies[0]
        assert ctx.errors == []

    async def test_model_switches_via_reply(self, model_config: MagicMock) -> None:
        """定義済みの名前なら切り替えて `reply` で返す。"""
        runtime_state = importlib.import_module("lilla_core.core.runtime_state")
        ctx = _DummyContext()

        result = await command_handler.dispatch_command(
            "!model other-llm", ctx, tools={}, bot=MagicMock()
        )

        assert result.status == "ok"
        assert runtime_state.get_active_llm_name() == "other-llm"
        assert ctx.replies == ["モデルを other-llm に切り替えました"]

    async def test_model_unknown_name_via_reply_error(self, model_config: MagicMock) -> None:
        """未定義の名前は `reply_error` に返り、結果は error になる。"""
        runtime_state = importlib.import_module("lilla_core.core.runtime_state")
        ctx = _DummyContext()

        result = await command_handler.dispatch_command(
            "!model missing", ctx, tools={}, bot=MagicMock()
        )

        assert result.status == "error"
        assert result.replied
        assert ctx.replies == []
        assert len(ctx.errors) == 1
        assert "missing" in ctx.errors[0]
        assert runtime_state.get_active_llm_name() is None

    async def test_skill_starts_conversation_with_expanded_content(
        self, skill_repo: MagicMock
    ) -> None:
        """`!skill` は展開した内容で `start_conversation` を呼ぶ。"""
        skill_repo.find_by_name.return_value = {
            "name": "review", "prompt": "レビューして", "enabled": True,
        }
        ctx = _DummyContext()

        result = await command_handler.dispatch_command(
            "!skill review この PR", ctx, tools={}, bot=MagicMock()
        )

        assert result.status == "ok"
        assert result.conversation_started
        assert not result.replied
        assert ctx.conversations == [
            format_skill_instruction("review", "レビューして", "この PR")
        ]

    async def test_skill_not_found_via_reply_error(self, skill_repo: MagicMock) -> None:
        """存在しないスキルは `reply_error` に返り、会話を始めない。"""
        ctx = _DummyContext()

        result = await command_handler.dispatch_command(
            "!skill missing", ctx, tools={}, bot=MagicMock()
        )

        assert result.status == "error"
        assert not result.conversation_started
        assert ctx.errors == ["スキル 'missing' は見つかりません。"]
        assert ctx.conversations == []

    async def test_unknown_command_is_error_without_reply(
        self, mock_notify_error: AsyncMock
    ) -> None:
        """未知のコマンドは `notify_error` に出し、呼び出し元へは何も返さず error にする。"""
        ctx = _DummyContext()
        bot = MagicMock()

        result = await command_handler.dispatch_command("!nosuch", ctx, tools={}, bot=bot)

        assert result.status == "error"
        assert not result.replied
        mock_notify_error.assert_called_once()
        assert mock_notify_error.call_args[0][0] is bot
        assert "nosuch" in mock_notify_error.call_args[0][2]
        assert ctx.replies == [] and ctx.errors == []

    async def test_exception_is_error_without_leaking_message(
        self, isolated_registry, mock_notify_error: AsyncMock
    ) -> None:
        """ハンドラ内の例外は `notify_error` に出し、例外の文字列を呼び出し元へ渡さない。"""
        error = RuntimeError("secret internal detail")
        isolated_registry("boom", AsyncMock(side_effect=error))
        ctx = _DummyContext()
        bot = MagicMock()

        result = await command_handler.dispatch_command("!boom", ctx, tools={}, bot=bot)

        assert result.status == "error"
        mock_notify_error.assert_called_once_with(
            bot, "コマンド処理中にエラーが発生しました", error
        )
        assert ctx.replies == [] and ctx.errors == []
        assert "secret internal detail" not in repr(result)

    async def test_discord_only_command_is_rejected_outside_discord(
        self, isolated_registry
    ) -> None:
        """Discord 専用コマンドは Discord 以外から呼ぶと実行せず、非対応のエラーを返す。"""
        handler = AsyncMock()
        registry.register_command("discord_thing", discord_only=True)(handler)
        ctx = _DummyContext()

        result = await command_handler.dispatch_command(
            "!discord_thing", ctx, tools={}, bot=MagicMock()
        )

        handler.assert_not_called()
        assert result.status == "error"
        assert ctx.errors == ["!discord_thing は Discord からのみ実行できます。"]

    async def test_discord_only_command_runs_from_discord(self, isolated_registry) -> None:
        """Discord 専用コマンドでも `client_type` が discord なら実行する。"""
        handler = AsyncMock()
        registry.register_command("discord_thing", discord_only=True)(handler)
        ctx = _DummyContext(client_type="discord")

        result = await command_handler.dispatch_command(
            "!discord_thing", ctx, tools={}, bot=MagicMock()
        )

        handler.assert_awaited_once()
        assert result.status == "ok"

    async def test_selftest_is_rejected_outside_discord(self) -> None:
        """添付付きで送る `!selftest` は Discord 以外から実行できない。"""
        ctx = _DummyContext()

        result = await command_handler.dispatch_command(
            "!selftest", ctx, tools={}, bot=MagicMock()
        )

        assert result.status == "error"
        assert ctx.errors == ["!selftest は Discord からのみ実行できます。"]

    async def test_runtask_reports_ok_without_reply(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`!runtask` は返信せず、実行できれば ok を返す。"""
        runtask = importlib.import_module("lilla_core.commands.runtask")
        loader = MagicMock()
        loader.get_llm_tools = lambda: {}
        monkeypatch.setitem(sys.modules, "lilla_core.loaders.llm_tool_loader", loader)
        tool = MagicMock()
        tool.execute = AsyncMock()
        tools = {"task_tool": {"instance": tool, "trigger": "task"}}
        monkeypatch.setattr(runtask, "notify_error", AsyncMock())
        ctx = _DummyContext()

        result = await command_handler.dispatch_command(
            "!runtask task_tool", ctx, tools=tools, bot=MagicMock()
        )

        tool.execute.assert_awaited_once()
        assert result.status == "ok"
        assert not result.replied

    async def test_runtask_unknown_tool_is_error_without_reply(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`!runtask` の入力エラーは `notify_error` のみで、結果は error になる。"""
        runtask = importlib.import_module("lilla_core.commands.runtask")
        notify = AsyncMock()
        monkeypatch.setattr(runtask, "notify_error", notify)
        ctx = _DummyContext()

        result = await command_handler.dispatch_command(
            "!runtask nosuch", ctx, tools={}, bot=MagicMock()
        )

        notify.assert_awaited_once()
        assert result.status == "error"
        assert not result.replied

    async def test_empty_command_is_reply_error(self) -> None:
        """`!` だけなら案内を `reply_error` で返す。"""
        ctx = _DummyContext()

        result = await command_handler.dispatch_command("  !  ", ctx, tools={}, bot=MagicMock())

        assert result.status == "error"
        assert ctx.errors == ["コマンドを入力してください。"]

    async def test_surrounding_whitespace_is_ignored(self, isolated_registry) -> None:
        """前後の空白を除いてから解釈する。"""
        handler = isolated_registry("cleardirty", AsyncMock())
        ctx = _DummyContext()

        await command_handler.dispatch_command("  !cleardirty  \n", ctx, tools={}, bot=None)

        assert handler.call_args[0][:2] == (ctx, "")

    async def test_non_owner_context_is_refused(self, isolated_registry) -> None:
        """オーナーとして扱わないコンテキストでは何も実行せず error を返す。"""
        handler = isolated_registry("cleardirty", AsyncMock())
        ctx = _DummyContext(is_owner=False)

        result = await command_handler.dispatch_command(
            "!cleardirty", ctx, tools={}, bot=MagicMock()
        )

        handler.assert_not_called()
        assert result.status == "error"
        assert ctx.replies == [] and ctx.errors == []

    async def test_text_without_prefix_raises(self) -> None:
        """`!` で始まらない文字列は呼び出し元の誤りとして ValueError にする。"""
        with pytest.raises(ValueError):
            await command_handler.dispatch_command("model", _DummyContext())

    async def test_uses_registered_runtime_by_default(
        self, isolated_registry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """tools / bot を省略すると `set_command_runtime()` で登録した値を使う。"""
        monkeypatch.setattr(command_handler, "_runtime_tools", {})
        monkeypatch.setattr(command_handler, "_runtime_bot", None)
        handler = isolated_registry("cleardirty", AsyncMock())
        tools = {"t": {}}
        bot = MagicMock()
        command_handler.set_command_runtime(tools, bot)
        ctx = _DummyContext()

        await command_handler.dispatch_command("!cleardirty", ctx)

        handler.assert_awaited_once_with(ctx, "", tools, bot)


# ---------------------------------------------------------------------------
# TestDiscordCommandContext
# ---------------------------------------------------------------------------


class TestDiscordCommandContext:
    def test_exposes_message_metadata(self) -> None:
        """client_type・author_id・添付・元メッセージを Discord のメッセージから取る。"""
        msg = _make_message()
        msg.author.id = 1234
        msg.attachments = ["a"]

        ctx = command_handler.DiscordCommandContext(msg, MagicMock())

        assert ctx.client_type == "discord"
        assert ctx.author_id == "1234"
        assert ctx.is_owner is True
        assert ctx.attachments == ["a"]
        assert ctx.discord_message is msg

    async def test_reply_uses_message_reply(self) -> None:
        """返信は元メッセージへの `reply` で送る。"""
        msg = _make_message()
        msg.channel.send = AsyncMock()
        ctx = command_handler.DiscordCommandContext(msg, MagicMock())

        await ctx.reply("結果")
        await ctx.reply_error("エラー")

        assert [c.args[0] for c in msg.reply.await_args_list] == ["結果", "エラー"]
        msg.channel.send.assert_not_awaited()

    async def test_long_reply_is_split_on_line_boundaries(self) -> None:
        """上限を超える文言は 1 通目を返信、2 通目以降を同じチャンネルへ送る。"""
        msg = _make_message()
        msg.channel.send = AsyncMock()
        ctx = command_handler.DiscordCommandContext(msg, MagicMock())
        lines = [f"{i:03d}" + "x" * 96 for i in range(40)]

        await ctx.reply("\n".join(lines))

        sent = [msg.reply.await_args.args[0]] + [
            c.args[0] for c in msg.channel.send.await_args_list
        ]
        assert len(sent) > 1
        assert all(len(chunk) <= 2000 for chunk in sent)
        assert [line for chunk in sent for line in chunk.split("\n")] == lines

    async def test_approved_message_reply_goes_to_original_channel(self) -> None:
        """承認済みコマンドは元チャンネルへ参照なしで送る（`ApprovedMessage.reply`）。"""
        from lilla_core.handlers.approval_flow import ApprovedMessage

        approval_message = MagicMock()
        approval_message.reply = AsyncMock()
        original_channel = MagicMock()
        original_channel.send = AsyncMock()
        approved = ApprovedMessage(approval_message, "!model", original_channel)
        ctx = command_handler.DiscordCommandContext(approved, MagicMock())

        await ctx.reply("結果")

        original_channel.send.assert_awaited_once_with("結果")
        approval_message.reply.assert_not_called()
        assert ctx.attachments == []

    async def test_start_conversation_uses_discord_flow(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """会話は Discord の `start_conversation` へ元メッセージと bot を渡して始める。"""
        handler_module = MagicMock()
        monkeypatch.setitem(sys.modules, "lilla_core.handlers.message_handler", handler_module)
        loader_module = MagicMock()
        loader_module.get_llm_tools = MagicMock(return_value={"tool": {}})
        monkeypatch.setitem(sys.modules, "lilla_core.loaders.llm_tool_loader", loader_module)
        msg = _make_message()
        bot = MagicMock()
        ctx = command_handler.DiscordCommandContext(msg, bot)

        await ctx.start_conversation("内容")

        handler_module.start_conversation.assert_called_once_with(
            msg, bot, {"tool": {}}, "内容"
        )
        assert ctx.build_result().conversation_started


# ---------------------------------------------------------------------------
# TestExtractCommandContent
# ---------------------------------------------------------------------------


class TestExtractCommandContent:
    def test_extracts_from_hermes_header(self) -> None:
        content = (
            "Cronjob Response: askendaily\n"
            "(job_id: 9466f746a7fe)\n"
            "-------------\n"
            '!mongodata\n'
            '{ "collection": "x" }'
        )
        result = command_handler.extract_command_content(content)
        assert result == '!mongodata\n{ "collection": "x" }'

    def test_plain_command_without_header(self) -> None:
        content = "!runtask foo"
        assert command_handler.extract_command_content(content) == "!runtask foo"

    def test_normal_message_with_exclamation_returns_none(self) -> None:
        content = "やったー！すごい！"
        assert command_handler.extract_command_content(content) is None

    def test_no_known_command_returns_none(self) -> None:
        content = "!unknown command here"
        assert command_handler.extract_command_content(content) is None

    def test_bare_exclamation_returns_none(self) -> None:
        """`!` だけの行はコマンド名が空なので既知コマンドにならない。"""
        assert command_handler.extract_command_content("!") is None

    def test_old_colon_format_returns_none(self) -> None:
        """廃止したコロン付き形式は既知コマンドとして抽出されない。"""
        assert command_handler.extract_command_content("!runtask: foo") is None

    def test_cleardirty_command(self) -> None:
        assert command_handler.extract_command_content("!cleardirty") == "!cleardirty"

    def test_cleardm_is_not_a_known_command(self) -> None:
        """廃止した !cleardm は既知コマンドとして抽出されない。"""
        assert command_handler.extract_command_content("!cleardm") is None

    def test_toolresult_command_keeps_body(self) -> None:
        """!toolresult は 1 行目以降の本文も含めて抽出される。"""
        content = f"ヘッダー\n!toolresult {_UUID}\n結果本文\n2行目"
        assert command_handler.extract_command_content(content) == (
            f"!toolresult {_UUID}\n結果本文\n2行目"
        )

    def test_uses_registry_as_source_of_truth(self, isolated_registry) -> None:
        """既知コマンド判定はレジストリを参照する（定数リストを持たない）。"""
        registry.register_command("newcommand")(AsyncMock())
        assert command_handler.extract_command_content("!newcommand 引数") == "!newcommand 引数"
