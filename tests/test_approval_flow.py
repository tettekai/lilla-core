"""handlers/approval_flow.py のテスト。

承認依頼の作成時に「実行される内容の全文」を承認依頼メッセージ自体へ載せ、
承認時はそのメッセージ以外を参照しないこと（TOCTOU 対策）を検証する。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from lilla_core.commands import attachment_body
import lilla_core.handlers.command_handler
from lilla_core.handlers import approval_flow

# 添付モックの URL -> 中身。`fake_download` が実ネットワークの代わりに参照する
_ATTACHMENT_CONTENTS: dict[str, bytes] = {}


def _make_attachment(
    content: str = '{"collection": "asken_daily"}',
    filename: str = "body.json",
    content_type: str = "application/json",
) -> MagicMock:
    """テキストとして読める Discord 添付ファイルのモックを返す。

    中身は `_ATTACHMENT_CONTENTS` に登録し、ダウンロード時に取り出せるようにする。
    """
    attachment = MagicMock()
    attachment.filename = filename
    attachment.content_type = content_type
    attachment.size = len(content.encode("utf-8"))
    attachment.url = f"https://cdn.discordapp.com/{len(_ATTACHMENT_CONTENTS)}/{filename}"
    _ATTACHMENT_CONTENTS[attachment.url] = content.encode("utf-8")
    return attachment


def _make_external_message(content: str, attachments: list | None = None) -> MagicMock:
    """外部ユーザーからの元メッセージのモックを返す。"""
    message = MagicMock()
    message.content = content
    message.attachments = attachments if attachments is not None else []
    message.author.display_name = "外部エージェント"
    message.channel.name = "bot-channel"
    message.channel.id = 222
    message.guild.id = 111
    message.id = 333
    return message


def _make_approval_message(content: str, attachments: list | None = None) -> MagicMock:
    """承認依頼メッセージ（bot 自身の投稿）のモックを返す。"""
    message = MagicMock()
    message.content = content
    message.attachments = attachments if attachments is not None else []
    return message


def _make_interaction(approval_message: MagicMock) -> MagicMock:
    """承認ボタン押下時のインタラクションのモックを返す。"""
    interaction = MagicMock()
    interaction.message = approval_message
    interaction.response = MagicMock()
    interaction.response.edit_message = AsyncMock()
    interaction.response.send_message = AsyncMock()
    return interaction


@pytest.fixture(autouse=True)
def mock_config(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """承認フローが参照する設定をモックに差し替える。"""
    cfg = MagicMock()
    cfg.discord.approval_channel_id = "999"
    cfg.discord.my_user_id = "99999"
    monkeypatch.setattr(approval_flow, "_config", cfg)
    return cfg


@pytest.fixture(autouse=True)
def mock_attachment_download(monkeypatch: pytest.MonkeyPatch) -> None:
    """添付ダウンロードとプロキシ解決を、実ネットワークに出ない形へ差し替える。

    本物の `resolve_command_body` を通したまま検証できるよう、ダウンロードだけを
    `_ATTACHMENT_CONTENTS` の参照に置き換える。
    """
    _ATTACHMENT_CONTENTS.clear()

    async def fake_download(url: str, proxy, proxy_headers) -> bytes:
        return _ATTACHMENT_CONTENTS[url]

    monkeypatch.setattr(attachment_body, "download_attachment_bytes", fake_download)
    monkeypatch.setattr(attachment_body, "resolve_proxy_settings", lambda: (None, None))


@pytest.fixture(autouse=True)
def mock_notify_error(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """BODY 解決失敗時のエラー通知をモックに差し替える。"""
    mock = AsyncMock()
    monkeypatch.setattr(attachment_body, "notify_error", mock)
    return mock


@pytest.fixture
def approval_channel() -> MagicMock:
    """承認チャンネルのモックを返す。"""
    channel = MagicMock()
    channel.send = AsyncMock()
    return channel


@pytest.fixture
def bot(approval_channel: MagicMock) -> MagicMock:
    """承認チャンネルを ID で解決できる Discord クライアントのモックを返す。"""
    mock_bot = MagicMock()
    mock_bot.get_channel.return_value = approval_channel
    return mock_bot


def _sent_text(approval_channel: MagicMock) -> str:
    """承認依頼として送信されたメッセージ本文を返す。"""
    return approval_channel.send.call_args.kwargs["content"]


def _sent_file_text(approval_channel: MagicMock) -> str:
    """承認依頼に添付されたファイルの内容を返す。"""
    file = approval_channel.send.call_args.kwargs["file"]
    return file.fp.getvalue().decode("utf-8")


class TestResolveFullCommand:
    """BODY 込みのコマンド全文への正規化の検証。"""

    async def test_inlines_text_body_for_mongodata(self, bot: MagicMock) -> None:
        """テキスト BODY の !mongodata はそのまま全文として組み立てる。"""
        message = _make_external_message('!mongodata {"collection": "asken_daily"}')

        result = await approval_flow.resolve_full_command(
            message, '!mongodata {"collection": "asken_daily"}', bot
        )

        assert result == '!mongodata\n{"collection": "asken_daily"}'

    async def test_inlines_attachment_body_for_mongodata(self, bot: MagicMock) -> None:
        """添付 BODY の !mongodata は添付の中身をインライン化する。"""
        attachment = _make_attachment('{"collection": "asken_daily", "payload": {}}')
        message = _make_external_message("!mongodata", [attachment])

        result = await approval_flow.resolve_full_command(message, "!mongodata", bot)

        assert result == '!mongodata\n{"collection": "asken_daily", "payload": {}}'

    async def test_inlines_text_body_for_toolresult(self, bot: MagicMock) -> None:
        """テキスト BODY の !toolresult は correlation_id と本文を保つ。"""
        content = "!toolresult abc-123\n検索結果です"

        result = await approval_flow.resolve_full_command(
            _make_external_message(content), content, bot
        )

        assert result == "!toolresult abc-123\n検索結果です"

    async def test_inlines_attachment_body_for_toolresult(self, bot: MagicMock) -> None:
        """添付 BODY の !toolresult は添付の中身を本文として組み立てる。"""
        attachment = _make_attachment("添付された結果本文", "result.txt", "text/plain")
        message = _make_external_message("!toolresult abc-123", [attachment])

        result = await approval_flow.resolve_full_command(
            message, "!toolresult abc-123", bot
        )

        assert result == "!toolresult abc-123\n添付された結果本文"

    async def test_returns_content_as_is_for_other_commands(self, bot: MagicMock) -> None:
        """添付 BODY を受け取らないコマンドは content をそのまま返す。"""
        message = _make_external_message("!runtask daily_summary")

        result = await approval_flow.resolve_full_command(
            message, "!runtask daily_summary", bot
        )

        assert result == "!runtask daily_summary"

    async def test_returns_none_when_body_missing(
        self, bot: MagicMock, mock_notify_error: AsyncMock
    ) -> None:
        """BODY を解決できない場合は None を返し、エラー通知が行われる。"""
        message = _make_external_message("!mongodata")

        result = await approval_flow.resolve_full_command(message, "!mongodata", bot)

        assert result is None
        mock_notify_error.assert_awaited_once()


class TestSendApprovalRequest:
    """承認依頼メッセージの作成の検証。"""

    async def test_writes_full_command_inline_when_short(
        self, bot: MagicMock, approval_channel: MagicMock
    ) -> None:
        """全文が上限以内なら本文へ全文を書き、添付ファイルは付けない。"""
        content = '!mongodata {"collection": "asken_daily"}'
        message = _make_external_message(content)

        await approval_flow.send_approval_request(bot, message, content)

        text = _sent_text(approval_channel)
        assert approval_flow.command_marker() in text
        assert text.endswith('!mongodata\n{"collection": "asken_daily"}')
        assert "file" not in approval_channel.send.call_args.kwargs

    async def test_reflects_attachment_body_in_approval_text(
        self, bot: MagicMock, approval_channel: MagicMock
    ) -> None:
        """添付ファイルで届いた BODY も承認依頼の本文に反映する（可視性の担保）。"""
        attachment = _make_attachment('{"collection": "asken_daily"}')
        message = _make_external_message("!mongodata", [attachment])

        await approval_flow.send_approval_request(bot, message, "!mongodata")

        text = _sent_text(approval_channel)
        assert '{"collection": "asken_daily"}' in text

    async def test_attaches_full_command_when_too_long(
        self, bot: MagicMock, approval_channel: MagicMock
    ) -> None:
        """全文が上限を超える場合はプレビュー＋新規作成した message.txt を添付する。"""
        body = "x" * 500
        attachment = _make_attachment(body, "body.txt", "text/plain")
        message = _make_external_message("!mongodata", [attachment])

        await approval_flow.send_approval_request(bot, message, "!mongodata")

        text = _sent_text(approval_channel)
        assert approval_flow.FULL_COMMAND_FILENAME in text
        assert body not in text
        file = approval_channel.send.call_args.kwargs["file"]
        assert file.filename == approval_flow.FULL_COMMAND_FILENAME
        assert _sent_file_text(approval_channel) == f"!mongodata\n{body}"

    async def test_does_not_send_when_body_unresolved(
        self, bot: MagicMock, approval_channel: MagicMock, mock_notify_error: AsyncMock
    ) -> None:
        """BODY を解決できない場合は承認依頼を作らず、通知のみ行う。"""
        message = _make_external_message("!mongodata")

        await approval_flow.send_approval_request(bot, message, "!mongodata")

        approval_channel.send.assert_not_called()
        mock_notify_error.assert_awaited_once()

    async def test_skips_when_approval_channel_missing(self, bot: MagicMock) -> None:
        """承認チャンネルが ID で見つからない場合は何もしない。"""
        bot.get_channel.return_value = None
        message = _make_external_message("!runtask x")

        await approval_flow.send_approval_request(bot, message, "!runtask x")

        # 例外を投げずに終了すること以外に副作用が無いことを確認する
        bot.get_channel.assert_called_once_with(999)

    async def test_skips_when_approval_channel_id_not_configured(
        self, bot: MagicMock, mock_config: MagicMock
    ) -> None:
        """承認チャンネル ID が未設定の場合は何もしない。"""
        mock_config.discord.approval_channel_id = None
        message = _make_external_message("!runtask x")

        await approval_flow.send_approval_request(bot, message, "!runtask x")

        bot.get_channel.assert_not_called()

    async def test_marks_jump_url_as_reference_only(
        self, bot: MagicMock, approval_channel: MagicMock
    ) -> None:
        """元メッセージへのリンクが参考用であることを本文に明記する。"""
        message = _make_external_message("!runtask x")

        await approval_flow.send_approval_request(bot, message, "!runtask x")

        text = _sent_text(approval_channel)
        assert "https://discord.com/channels/111/222/333" in text
        assert "確認用" in text


class TestHandleApproveInteraction:
    """承認ボタン押下時の実行内容の復元の検証。"""

    @pytest.fixture(autouse=True)
    def mock_handle_command(self, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
        """コマンド実行をモックに差し替える。"""
        mock = AsyncMock()
        monkeypatch.setattr(lilla_core.handlers.command_handler, "handle_command", mock)
        return mock

    async def test_restores_command_from_message_content(
        self, bot: MagicMock, mock_handle_command: AsyncMock
    ) -> None:
        """本文の区切り行より後ろを実行内容として復元する。"""
        approval_message = _make_approval_message(
            f"ヘッダー\n{approval_flow.command_marker()}\n!runtask daily_summary"
        )
        interaction = _make_interaction(approval_message)

        await approval_flow.handle_approve_interaction(
            bot, {"t": 1}, interaction, "approve:222:333"
        )

        passed_message, content, tools, passed_bot = mock_handle_command.call_args[0]
        assert content == "!runtask daily_summary"
        assert passed_message.content == "!runtask daily_summary"
        assert passed_message.attachments == []
        assert tools == {"t": 1}
        assert passed_bot is bot
        interaction.response.edit_message.assert_awaited_once()

    async def test_restores_command_from_attachment(
        self, bot: MagicMock, mock_handle_command: AsyncMock
    ) -> None:
        """添付ファイルがある場合はその内容を実行内容として復元する。"""
        full_command = "!mongodata\n" + "y" * 400
        attachment = _make_attachment(full_command, "message.txt", "text/plain")
        approval_message = _make_approval_message(
            f"ヘッダー\n{approval_flow.command_marker()}\nプレビュー", [attachment]
        )
        interaction = _make_interaction(approval_message)

        await approval_flow.handle_approve_interaction(
            bot, {}, interaction, "approve:222:333"
        )

        _, content, _, _ = mock_handle_command.call_args[0]
        assert content == full_command

    async def test_never_fetches_original_message(
        self, bot: MagicMock, mock_handle_command: AsyncMock
    ) -> None:
        """元メッセージの再取得（fetch_message）を一切行わない。

        `custom_id` から解決するのは返信先チャンネルだけで、そのチャンネルに対する
        `fetch_message()` は行わない。
        """
        original_channel = MagicMock()
        bot.get_channel.return_value = original_channel
        approval_message = _make_approval_message(
            f"{approval_flow.command_marker()}\n!runtask x"
        )
        interaction = _make_interaction(approval_message)

        await approval_flow.handle_approve_interaction(
            bot, {}, interaction, "approve:222:333"
        )

        bot.fetch_channel.assert_not_called()
        original_channel.fetch_message.assert_not_called()

    async def test_replies_go_to_original_channel(
        self, bot: MagicMock, mock_handle_command: AsyncMock
    ) -> None:
        """返信先チャンネルは custom_id の channel_id から解決した元チャンネルにする。"""
        original_channel = MagicMock()
        bot.get_channel.return_value = original_channel
        interaction = _make_interaction(
            _make_approval_message(f"{approval_flow.command_marker()}\n!runtask x")
        )

        await approval_flow.handle_approve_interaction(
            bot, {}, interaction, "approve:222:333"
        )

        bot.get_channel.assert_called_once_with(222)
        passed_message = mock_handle_command.call_args[0][0]
        assert passed_message.channel is original_channel

    async def test_falls_back_to_approval_channel_when_unresolvable(
        self, bot: MagicMock, mock_handle_command: AsyncMock
    ) -> None:
        """元チャンネルを解決できない場合は承認依頼メッセージのチャンネルへフォールバックする。"""
        bot.get_channel.return_value = None
        approval_message = _make_approval_message(
            f"{approval_flow.command_marker()}\n!runtask x"
        )
        interaction = _make_interaction(approval_message)

        await approval_flow.handle_approve_interaction(
            bot, {}, interaction, "approve:222:333"
        )

        passed_message = mock_handle_command.call_args[0][0]
        assert passed_message.channel is approval_message.channel

    async def test_skips_execution_when_marker_missing(
        self, bot: MagicMock, mock_handle_command: AsyncMock
    ) -> None:
        """区切り行が無い場合は実行せず、エラー表示のみ行う。"""
        interaction = _make_interaction(_make_approval_message("区切りのない本文"))

        await approval_flow.handle_approve_interaction(
            bot, {}, interaction, "approve:222:333"
        )

        mock_handle_command.assert_not_called()
        interaction.response.edit_message.assert_not_called()
        interaction.response.send_message.assert_awaited_once()
        assert interaction.response.send_message.call_args.kwargs["ephemeral"] is True

    async def test_skips_execution_when_attachment_unreadable(
        self, bot: MagicMock, mock_handle_command: AsyncMock
    ) -> None:
        """添付ファイルを読めない場合は実行しない。"""
        attachment = _make_attachment("dummy", "message.bin", "application/octet-stream")
        approval_message = _make_approval_message(
            f"{approval_flow.command_marker()}\nプレビュー", [attachment]
        )
        interaction = _make_interaction(approval_message)

        await approval_flow.handle_approve_interaction(
            bot, {}, interaction, "approve:222:333"
        )

        mock_handle_command.assert_not_called()
        interaction.response.send_message.assert_awaited_once()


class TestApprovalRoundTrip:
    """承認依頼の作成 → 承認実行の往復で、実行内容が保たれることの検証。"""

    @pytest.fixture(autouse=True)
    def mock_handle_command(self, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
        """コマンド実行をモックに差し替える。"""
        mock = AsyncMock()
        monkeypatch.setattr(lilla_core.handlers.command_handler, "handle_command", mock)
        return mock

    async def _approve_sent_request(
        self, bot: MagicMock, approval_channel: MagicMock
    ) -> MagicMock:
        """送信済みの承認依頼をそのまま承認し、インタラクションを返す。"""
        kwargs = approval_channel.send.call_args.kwargs
        attachments = []
        if "file" in kwargs:
            attachments = [
                _make_attachment(
                    kwargs["file"].fp.getvalue().decode("utf-8"),
                    approval_flow.FULL_COMMAND_FILENAME,
                    "text/plain",
                )
            ]
        interaction = _make_interaction(
            _make_approval_message(kwargs["content"], attachments)
        )
        await approval_flow.handle_approve_interaction(
            bot, {}, interaction, "approve:222:333"
        )
        return interaction

    async def test_short_text_body_is_executed_as_requested(
        self, bot: MagicMock, approval_channel: MagicMock, mock_handle_command: AsyncMock
    ) -> None:
        """300文字以内のテキスト BODY は、承認依頼時の内容がそのまま実行される。"""
        content = '!mongodata {"collection": "asken_daily", "payload": {"date": "2026-01-01"}}'
        message = _make_external_message(content)

        await approval_flow.send_approval_request(bot, message, content)
        # 承認前に元メッセージが別の内容へ編集されても影響しないことを表現する
        message.content = "!toolresult 悪意ある内容"
        message.attachments = [_make_attachment("悪意ある BODY")]
        await self._approve_sent_request(bot, approval_channel)

        _, executed, _, _ = mock_handle_command.call_args[0]
        assert executed == (
            '!mongodata\n{"collection": "asken_daily", "payload": {"date": "2026-01-01"}}'
        )

    async def test_long_attachment_body_is_executed_as_requested(
        self, bot: MagicMock, approval_channel: MagicMock, mock_handle_command: AsyncMock
    ) -> None:
        """300文字を超える添付 BODY も、承認依頼時の全文がそのまま実行される。"""
        body = "z" * 800
        attachment = _make_attachment(body, "body.txt", "text/plain")
        message = _make_external_message("!toolresult abc-123", [attachment])

        await approval_flow.send_approval_request(bot, message, "!toolresult abc-123")
        message.attachments = [_make_attachment("差し替えられた BODY", "body.txt", "text/plain")]
        await self._approve_sent_request(bot, approval_channel)

        _, executed, _, _ = mock_handle_command.call_args[0]
        assert executed == f"!toolresult abc-123\n{body}"


class TestApprovedMessage:
    """コマンドハンドラへ渡すメッセージ代理オブジェクトの検証。"""

    def test_exposes_restored_content_without_attachments(self) -> None:
        """復元した実行内容を content として持ち、添付は空にする。"""
        approval_message = _make_approval_message("承認依頼", [_make_attachment("x")])

        proxy = approval_flow.ApprovedMessage(approval_message, "!runtask x")

        assert proxy.content == "!runtask x"
        assert proxy.attachments == []

    def test_delegates_other_attributes(self) -> None:
        """未定義の属性は承認依頼メッセージへ委譲する（`reply` は自前なので対象外）。"""
        approval_message = _make_approval_message("承認依頼")
        approval_message.author.display_name = "リラ"

        proxy = approval_flow.ApprovedMessage(approval_message, "!runtask x")

        assert proxy.author.display_name == "リラ"

    def test_prefers_original_channel(self) -> None:
        """元チャンネルを渡した場合は返信先としてそちらを使う。"""
        approval_message = _make_approval_message("承認依頼")
        original_channel = MagicMock()

        proxy = approval_flow.ApprovedMessage(
            approval_message, "!runtask x", original_channel
        )

        assert proxy.channel is original_channel

    def test_falls_back_to_approval_channel(self) -> None:
        """元チャンネルが None の場合は承認依頼メッセージのチャンネルを使う。"""
        approval_message = _make_approval_message("承認依頼")

        proxy = approval_flow.ApprovedMessage(approval_message, "!runtask x", None)

        assert proxy.channel is approval_message.channel

    async def test_reply_sends_to_original_channel_without_reference(self) -> None:
        """`reply` は元チャンネルへ `send` し、承認依頼メッセージへは返信しない。"""
        approval_message = _make_approval_message("承認依頼")
        approval_message.reply = AsyncMock()
        original_channel = MagicMock()
        original_channel.send = AsyncMock()

        proxy = approval_flow.ApprovedMessage(
            approval_message, "!model", original_channel
        )
        await proxy.reply("切り替えました")

        original_channel.send.assert_awaited_once_with("切り替えました")
        approval_message.reply.assert_not_called()
        approval_message.channel.send.assert_not_called()

    async def test_reply_falls_back_to_approval_channel(self) -> None:
        """元チャンネルを解決できなかった場合は承認チャンネルへ送る。"""
        approval_message = _make_approval_message("承認依頼")
        approval_message.reply = AsyncMock()
        approval_message.channel.send = AsyncMock()

        proxy = approval_flow.ApprovedMessage(approval_message, "!model", None)
        await proxy.reply("切り替えました")

        approval_message.channel.send.assert_awaited_once_with("切り替えました")
        approval_message.reply.assert_not_called()

    async def test_reply_passes_through_send_kwargs(self) -> None:
        """`send()` の追加引数（添付など）はそのまま素通しする。"""
        approval_message = _make_approval_message("承認依頼")
        original_channel = MagicMock()
        original_channel.send = AsyncMock()
        file_obj = MagicMock()

        proxy = approval_flow.ApprovedMessage(
            approval_message, "!selftest", original_channel
        )
        await proxy.reply("結果", file=file_obj)

        original_channel.send.assert_awaited_once_with("結果", file=file_obj)


_SPLIT_UUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


def _frontmatter(body: str = "", correlation_id: str = _SPLIT_UUID) -> str:
    """外部エージェントからの FrontMatter 付きメッセージ本文を組み立てる。"""
    return f"---\ncorrelation_id: {correlation_id}\n---\n{body}"


class _History:
    """`channel.history(limit=1, before=...)` の戻り値（非同期イテレータ）の代わり。"""

    def __init__(self, messages: list) -> None:
        """返すメッセージの一覧を受け取る。"""
        self._messages = list(messages)

    def __aiter__(self):
        """非同期イテレータとして自身を返す。"""
        return self

    async def __anext__(self):
        """次のメッセージを返す。尽きたら StopAsyncIteration を投げる。"""
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)


def _with_previous(message: MagicMock, previous: MagicMock | None) -> MagicMock:
    """`message` のチャンネル履歴の直前 1 件を `previous` にする。"""
    message.channel.history = MagicMock(
        return_value=_History([previous] if previous is not None else [])
    )
    return message


def _author(user_id: int) -> MagicMock:
    """送信者のモックを返す。"""
    author = MagicMock()
    author.id = user_id
    author.display_name = "外部エージェント"
    return author


@pytest.fixture
def pending_repo(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """pending_tool_calls リポジトリをモックに差し替える（既定は pending あり）。"""
    repo = MagicMock()
    repo.exists_pending = AsyncMock(return_value=True)
    monkeypatch.setattr(approval_flow, "get_pending_tool_calls_repo", lambda: repo)
    return repo


@pytest.fixture
def split_messages() -> tuple[MagicMock, MagicMock]:
    """FrontMatter だけの 1 通目と、本文なしのテキスト添付だけの 2 通目を返す。"""
    previous = _make_external_message(_frontmatter())
    previous.author = _author(12345)
    attachment = _make_attachment("分割で届いた結果本文", "result.txt", "text/plain")
    message = _make_external_message("", [attachment])
    message.author = _author(12345)
    _with_previous(message, previous)
    return previous, message


class TestExtractApprovableCommandFromMessage:
    """FrontMatter と添付を 2 通に分けた結果メッセージの扱いの検証。"""

    async def test_frontmatter_only_message_is_ignored_silently(
        self, pending_repo: MagicMock, mock_notify_error: AsyncMock
    ) -> None:
        """FrontMatter だけのメッセージは何もしない（照会もエラー通知もしない）。"""
        message = _make_external_message(_frontmatter())

        result = await approval_flow.extract_approvable_command_from_message(message)

        assert result is None
        pending_repo.exists_pending.assert_not_called()
        mock_notify_error.assert_not_called()

    async def test_frontmatter_with_body_is_converted_as_before(
        self, pending_repo: MagicMock
    ) -> None:
        """閉じ `---` のあとに本文がある場合は今どおり変換する。"""
        message = _make_external_message(_frontmatter("検索結果です"))

        result = await approval_flow.extract_approvable_command_from_message(message)

        assert result == f"!toolresult {_SPLIT_UUID}\n検索結果です"

    async def test_frontmatter_with_attachment_is_converted_as_before(
        self, pending_repo: MagicMock
    ) -> None:
        """同じメッセージに FrontMatter と添付がある場合は今どおり（履歴は見ない）。"""
        attachment = _make_attachment("結果", "result.txt", "text/plain")
        message = _make_external_message(_frontmatter(), [attachment])
        message.channel.history = MagicMock()

        result = await approval_flow.extract_approvable_command_from_message(message)

        assert result == f"!toolresult {_SPLIT_UUID}\n"
        message.channel.history.assert_not_called()

    async def test_known_command_with_attachment_is_unchanged(
        self, pending_repo: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """既知コマンドと添付が同じメッセージにある場合は今どおり。"""
        # このモジュールではコマンドのレジストリを読み込まないため、判定だけ差し替える
        monkeypatch.setattr(
            approval_flow.command_handler,
            "extract_command_content",
            lambda content: content if content.startswith("!toolresult") else None,
        )
        attachment = _make_attachment("結果", "result.txt", "text/plain")
        message = _make_external_message(f"!toolresult {_SPLIT_UUID}", [attachment])
        message.channel.history = MagicMock()

        result = await approval_flow.extract_approvable_command_from_message(message)

        assert result == f"!toolresult {_SPLIT_UUID}"
        message.channel.history.assert_not_called()

    async def test_attachment_only_message_combines_with_previous_frontmatter(
        self, pending_repo: MagicMock, split_messages
    ) -> None:
        """直前の FrontMatter だけのメッセージと組み合わせて !toolresult にする。"""
        _, message = split_messages

        result = await approval_flow.extract_approvable_command_from_message(message)

        assert result == f"!toolresult {_SPLIT_UUID}\n"
        pending_repo.exists_pending.assert_awaited_once_with(_SPLIT_UUID)
        message.channel.history.assert_called_once_with(limit=1, before=message)

    async def test_combined_command_resolves_body_from_attachment(
        self, pending_repo: MagicMock, split_messages, bot: MagicMock
    ) -> None:
        """組み合わせたコマンドは、今のメッセージの添付を BODY として全文になる。"""
        _, message = split_messages

        command = await approval_flow.extract_approvable_command_from_message(message)
        full = await approval_flow.resolve_full_command(message, command, bot)

        assert full == f"!toolresult {_SPLIT_UUID}\n分割で届いた結果本文"

    async def test_ignored_when_previous_author_differs(
        self, pending_repo: MagicMock, split_messages
    ) -> None:
        """直前が別の送信者なら無視する。"""
        previous, message = split_messages
        previous.author = _author(67890)

        assert await approval_flow.extract_approvable_command_from_message(message) is None
        pending_repo.exists_pending.assert_not_called()

    async def test_ignored_when_previous_has_body(
        self, pending_repo: MagicMock, split_messages
    ) -> None:
        """直前の FrontMatter に本文があれば（1 通で完結済みなので）無視する。"""
        previous, message = split_messages
        previous.content = _frontmatter("検索結果です")

        assert await approval_flow.extract_approvable_command_from_message(message) is None
        pending_repo.exists_pending.assert_not_called()

    async def test_ignored_when_previous_has_attachment(
        self, pending_repo: MagicMock, split_messages
    ) -> None:
        """直前が添付つき（その 1 通で処理済み）なら無視する。"""
        previous, message = split_messages
        previous.attachments = [_make_attachment("結果", "result.txt", "text/plain")]

        assert await approval_flow.extract_approvable_command_from_message(message) is None
        pending_repo.exists_pending.assert_not_called()

    async def test_ignored_when_previous_has_no_frontmatter(
        self, pending_repo: MagicMock, split_messages
    ) -> None:
        """直前が FrontMatter でなければ無視する。"""
        previous, message = split_messages
        previous.content = "こんにちは"

        assert await approval_flow.extract_approvable_command_from_message(message) is None
        pending_repo.exists_pending.assert_not_called()

    async def test_ignored_when_correlation_id_not_pending(
        self, pending_repo: MagicMock, split_messages
    ) -> None:
        """correlation_id が pending として実在しなければ無視する。"""
        _, message = split_messages
        pending_repo.exists_pending.return_value = False

        assert await approval_flow.extract_approvable_command_from_message(message) is None

    async def test_ignored_when_pending_query_fails(
        self, pending_repo: MagicMock, split_messages
    ) -> None:
        """pending の照会に失敗したら無視する。"""
        _, message = split_messages
        pending_repo.exists_pending.side_effect = Exception("db down")

        assert await approval_flow.extract_approvable_command_from_message(message) is None

    async def test_ignored_when_no_previous_message(
        self, pending_repo: MagicMock, split_messages
    ) -> None:
        """直前のメッセージが無ければ無視する。"""
        _, message = split_messages
        _with_previous(message, None)

        assert await approval_flow.extract_approvable_command_from_message(message) is None

    async def test_ignored_when_history_fetch_fails(
        self, pending_repo: MagicMock, split_messages
    ) -> None:
        """履歴の取得に失敗したら無視する（例外は外へ出さない）。"""
        _, message = split_messages
        message.channel.history = MagicMock(side_effect=Exception("forbidden"))

        assert await approval_flow.extract_approvable_command_from_message(message) is None

    async def test_ignored_when_attachment_is_not_text(
        self, pending_repo: MagicMock, split_messages, mock_notify_error: AsyncMock
    ) -> None:
        """先頭の添付がテキストでなければ履歴も見ずに無視する（エラー通知もしない）。"""
        _, message = split_messages
        message.attachments = [_make_attachment("binary", "image.png", "image/png")]

        assert await approval_flow.extract_approvable_command_from_message(message) is None
        message.channel.history.assert_not_called()
        mock_notify_error.assert_not_called()

    async def test_ignored_when_message_has_text(
        self, pending_repo: MagicMock, split_messages
    ) -> None:
        """本文のある添付つきメッセージは直前を見ない。"""
        _, message = split_messages
        message.content = "ファイルを送ります"

        assert await approval_flow.extract_approvable_command_from_message(message) is None
        message.channel.history.assert_not_called()

    async def test_message_without_attachment_does_not_read_history(
        self, pending_repo: MagicMock
    ) -> None:
        """本文も添付も無いメッセージは直前を見ない。"""
        message = _make_external_message("")
        message.channel.history = MagicMock()

        assert await approval_flow.extract_approvable_command_from_message(message) is None
        message.channel.history.assert_not_called()
