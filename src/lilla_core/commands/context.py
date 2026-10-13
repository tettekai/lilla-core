"""コマンドコンテキスト（コマンドの呼び出し元と返信先の抽象）。

`!` コマンドのハンドラは Discord のメッセージを直接触らず、このモジュールの
`CommandContext` を受け取って `reply` / `reply_error` / `start_conversation` で
呼び出し元へ結果を返す。これにより、同じコマンドを Discord 以外のクライアント
（拡張が WebSocket や HTTP で受けるものなど）からも実行できる。

Discord 用の実装（`DiscordCommandContext`）はコアの `handlers/command_handler.py` に
あり、Discord 以外の実装は拡張が `CommandContext` を継承して用意する。実行の入口は
同じモジュールの `dispatch_command(text, ctx)`。

コマンドを登録しないモジュールなので `load_all_commands()` から import されても
レジストリには影響しない。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

#: Discord からの呼び出しを表す `client_type`。`CommandContext.client_type` が
#: これ以外のときは Discord 専用コマンド（`register_command(..., discord_only=True)`）を
#: 実行しない。
DISCORD_CLIENT_TYPE = "discord"

#: `CommandResult.status` の値。
COMMAND_STATUS_OK = "ok"
COMMAND_STATUS_ERROR = "error"

CommandStatus = Literal["ok", "error"]


@dataclass(frozen=True)
class CommandResult:
    """`dispatch_command` の実行結果。

    コマンドがコンテキストへ何も返信しなかった場合（`!runtask` の完了、未知の
    コマンド、ハンドラ内の例外など）でも、呼び出し元が完了・失敗を判別できるように
    返す。例外の内容は載せない（呼び出し元へ内部情報を渡さないため。詳細は
    `notify_error` によりログとエラー通知チャンネルに出る）。
    """

    #: `"ok"` または `"error"`。
    status: CommandStatus
    #: コマンドが `reply` / `reply_error` で 1 回以上返信したか。
    replied: bool = False
    #: コマンドが `start_conversation` で会話を始めたか。
    conversation_started: bool = False

    @property
    def ok(self) -> bool:
        """成功（`status == "ok"`）なら True。"""
        return self.status == COMMAND_STATUS_OK


class CommandContext(ABC):
    """コマンド 1 回分の呼び出し元を表す抽象。

    コマンドハンドラは `reply` / `reply_error` / `start_conversation` を呼ぶ。
    実装側（Discord・拡張のクライアント）は送り方の本体である `deliver_reply` /
    `deliver_error` / `deliver_conversation` を実装する。公開メソッドは呼ばれたことを
    記録してから実装へ委ねるため、`dispatch_command` は実装に依らず結果
    （`CommandResult`）を組み立てられる。

    1 回の `dispatch_command` につき 1 つ作り、使い回さない（返信や失敗の記録が
    次の実行へ持ち越されるため）。
    """

    def __init__(
        self,
        *,
        client_type: str,
        author_id: str | None = None,
        is_owner: bool = False,
        attachments: list | None = None,
    ) -> None:
        """コンテキストを作る。

        Args:
            client_type: 呼び出し元の種別（`"discord"` や拡張が増やす種別）。
            author_id: 呼び出し元のユーザーを表す ID（分からなければ None）。
            is_owner: 呼び出し元をオーナーとして扱うか。`dispatch_command` は
                False のコンテキストではコマンドを実行しない。
            attachments: コマンドに添えられた添付ファイル（Discord の
                `Attachment` 互換）。無ければ空。
        """
        self.client_type = client_type
        self.author_id = author_id
        self.is_owner = is_owner
        self.attachments: list = list(attachments or [])
        self._replied = False
        self._failed = False
        self._conversation_started = False

    @property
    def discord_message(self) -> Any:
        """呼び出し元の Discord メッセージ。Discord 以外の呼び出しでは None。

        ボタン付きメッセージの書き換えなど、Discord 固有の操作が必須のコマンドだけが
        使う（そうしたコマンドは `discord_only=True` で登録する）。
        """
        return None

    async def reply(self, text: str) -> None:
        """呼び出し元と同じ場所へコマンドの結果を返す。

        Args:
            text: 返す文言。長さの制限（Discord の 1 メッセージ上限など）は
                実装側で扱う。
        """
        self._replied = True
        await self.deliver_reply(text)

    async def reply_error(self, text: str) -> None:
        """入力ミスなど、呼び出し元へ返すべきエラーを返す。

        呼ぶと `dispatch_command` の結果は error になる。

        Args:
            text: 返すエラーの文言（内部情報を含めないこと）。
        """
        self._replied = True
        self._failed = True
        await self.deliver_error(text)

    async def start_conversation(self, content: str) -> None:
        """呼び出し元の通常の会話フローで、`content` を最新の user メッセージとして 1 ターン始める。

        会話の完了は待たない（実装は会話をタスクとして起こして戻ってよい）。

        Args:
            content: 最新の user メッセージとして会話履歴に残し、LLM へ渡す内容。
        """
        self._conversation_started = True
        await self.deliver_conversation(content)

    def mark_failed(self) -> None:
        """コマンドが失敗したことを記録する（呼び出し元へは何も送らない）。

        `notify_error` だけで報告している入力エラーなど、呼び出し元への返信は
        しないが `dispatch_command` の結果は error にしたいときに使う。
        """
        self._failed = True

    def build_result(self) -> CommandResult:
        """ここまでの記録から `CommandResult` を組み立てる。"""
        return CommandResult(
            status=COMMAND_STATUS_ERROR if self._failed else COMMAND_STATUS_OK,
            replied=self._replied,
            conversation_started=self._conversation_started,
        )

    @abstractmethod
    async def deliver_reply(self, text: str) -> None:
        """`reply` の送信本体。実装側で呼び出し元へ届ける。"""

    @abstractmethod
    async def deliver_error(self, text: str) -> None:
        """`reply_error` の送信本体。実装側で呼び出し元へ届ける。"""

    @abstractmethod
    async def deliver_conversation(self, content: str) -> None:
        """`start_conversation` の本体。実装側の会話フローで 1 ターン始める。"""
