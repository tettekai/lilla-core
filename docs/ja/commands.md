# コマンドの実装と、Discord 以外からの実行

`!model` や `!skill` のような `!` コマンドは、Discord のメッセージに直接依存しない
**コマンドコンテキスト**（`lilla_core.commands.context.CommandContext`）を受け取って動きます。
Discord からの実行はコアが面倒を見ますが、拡張が自分のクライアント（WebSocket や HTTP で
つながるものなど）向けのコンテキストを用意すれば、同じコマンドをそこからも実行できます。

## コマンドを足す

コマンドは 1 コマンド 1 ファイルで書き、`@register_command("コマンド名")` で登録します。
拡張のコマンドは `Extension.command_packages()` が返すパッケージに置きます
（[拡張の基本](extensions.md)）。

```python
from lilla_core.commands.registry import register_command


@register_command("hello")
async def handle_hello(ctx, arg: str, tools: dict, bot) -> None:
    """`!hello [名前]` の処理。"""
    name = arg.strip()
    if not name:
        await ctx.reply_error("使い方: !hello <名前>")
        return
    await ctx.reply(f"こんにちは、{name}")
```

ハンドラの引数は `(ctx, arg, tools, bot)` です。

| 引数 | 内容 |
|------|------|
| `ctx` | 呼び出し元のコマンドコンテキスト |
| `arg` | コマンド名の後ろに続く引数文字列（最初の空白で 1 回だけ分割。無ければ空文字列） |
| `tools` | task ツールのレジストリ |
| `bot` | Discord クライアント。Discord 以外から呼ばれたときも渡る（エラー通知などに使う） |

コンテキストから使えるものは次のとおりです。

| 名前 | 内容 |
|------|------|
| `await ctx.reply(text)` | 呼び出し元と同じ場所へ結果を返す |
| `await ctx.reply_error(text)` | 入力ミスなど、呼び出し元へ返すべきエラーを返す（実行結果は error になる） |
| `await ctx.start_conversation(content)` | 呼び出し元の通常の会話フローで、`content` を最新の user メッセージとして 1 ターン始める |
| `ctx.mark_failed()` | 呼び出し元へは何も返さず、実行結果だけを error にする |
| `ctx.client_type` | 呼び出し元の種別（Discord は `discord`） |
| `ctx.author_id` / `ctx.is_owner` | 呼び出し元の識別情報 |
| `ctx.attachments` | 添付ファイル（無ければ空） |
| `ctx.discord_message` | Discord から呼ばれたときだけ、元の Discord メッセージ（それ以外は `None`） |

Discord では `reply` / `reply_error` はどちらも元メッセージへの返信になります。
1 メッセージの上限を超える文言は行の境界で分割され、2 通目以降は同じチャンネルへ続けて
送られるので、コマンド側で分割する必要はありません。

### Discord 専用のコマンド

ボタン付きメッセージの書き換えや添付ファイル付きの送信など、Discord の機能が必須の
コマンドは `discord_only=True` で登録し、`ctx.discord_message` を使います。
Discord 以外から呼ばれると、ハンドラは実行されず
「`!名前` は Discord からのみ実行できます。」が `reply_error` で返ります。

```python
@register_command("refresh_panel", discord_only=True)
async def handle_refresh_panel(ctx, arg: str, tools: dict, bot) -> None:
    """ボタン付きのパネルを書き換える。"""
    await ctx.discord_message.edit(content="更新しました")
```

コアの `!selftest` も、詳細を添付ファイルで返すため Discord 専用です（Discord 以外からは
共有 HTTP サーバーの `GET /api/selftest` を使ってください）。

## Discord 以外から実行する

拡張は `CommandContext` を継承し、送り方の本体である 3 つのメソッドを実装します。
そのうえで `dispatch_command(text, ctx)` を呼びます。

```python
from lilla_core.commands.context import CommandContext
from lilla_core.handlers.command_handler import dispatch_command


class MyClientCommandContext(CommandContext):
    def __init__(self, connection):
        super().__init__(client_type="my-client", is_owner=True)
        self._connection = connection

    async def deliver_reply(self, text: str) -> None:
        await self._connection.send_json({"type": "command_result", "status": "ok", "content": text})

    async def deliver_error(self, text: str) -> None:
        await self._connection.send_json({"type": "command_result", "status": "error", "content": text})

    async def deliver_conversation(self, content: str) -> None:
        # このクライアントの通常の会話と同じ処理で 1 ターン始める
        # （会話履歴への保存・run_conversation・返答の送信）
        start_my_conversation_turn(self._connection, content)


result = await dispatch_command("!model", MyClientCommandContext(connection))
```

- コンテキストは 1 回の実行ごとに新しく作ってください（返信や失敗の記録が次の実行に持ち越されます）。
- `text` は `!` で始まる文字列です（前後の空白は無視します）。`!` で始まらなければ
  `ValueError` になるので、コマンドかどうかは呼び出し側で判定してから渡します。
- `tools` と `bot` は省略でき、コアが起動時に登録したもの（`set_command_runtime()`）が使われます。
- `deliver_conversation` の完了を待つ必要はありません（会話をタスクとして起こして戻ってかまいません）。

### 実行結果

`dispatch_command` は `CommandResult` を返します。

| フィールド | 内容 |
|------------|------|
| `status` | `"ok"` または `"error"`（`result.ok` でも判定できる） |
| `replied` | コマンドが `reply` / `reply_error` で返信したか |
| `conversation_started` | コマンドが `start_conversation` で会話を始めたか |

コマンドが何も返信しなかった場合でも、`status` で完了・失敗を判別できます。たとえば
`!runtask` は返信せず `ok`（失敗すれば `error`）を返すので、クライアント側で固定の完了
メッセージを表示できます。

### エラーの扱い

| 状況 | 呼び出し元へ | ログ・エラー通知チャンネル | `status` |
|------|--------------|----------------------------|----------|
| 入力ミス（`!` だけ、`!model` の未定義名、存在しない・無効なスキルなど） | `reply_error` の文言 | なし | `error` |
| Discord 専用コマンドを Discord 以外から呼んだ | `reply_error` の文言 | なし | `error` |
| `notify_error` だけで報告する入力エラー（`!runtask` のツール名不正、`!mongodata` の JSON 不正など） | 何も返らない | `notify_error` | `error` |
| 未知のコマンド・ハンドラ内の例外 | 何も返らない | `notify_error` | `error` |

例外の文字列は `CommandResult` に載らず、呼び出し元へ渡りません。`status` が `error` で
返信も無いときは、内部情報を含まない汎用のエラーメッセージを表示してください（詳細は
ログとエラー通知チャンネルで確認できます）。

`!runtask` のタスクの出力（タスクツールが Discord へ送るもの）の送り先は、どこから
実行しても変わりません。

## オーナーと承認

`dispatch_command` は承認判定を行いません。**呼び出し元が認証済みのオーナーとして
コマンドを実行する入口**です。

- Discord では、オーナー以外からのコマンドは従来どおり承認フローを経てから実行されます。
- Discord 以外のクライアントでは、拡張がクライアントトークン（`verify_client_token`）などで
  認証した接続についてのみ、`is_owner=True` のコンテキストで呼んでください。
  `is_owner` が `False` のコンテキストでは、コアは何も実行せず `error` を返します。

コマンドには会話履歴や MongoDB を書き換えるもの（`!cleardirty` / `!mongodata`）や
タスクを動かすもの（`!runtask`）が含まれます。未認証の接続からの文字列をそのまま
`dispatch_command` に渡さないでください。
