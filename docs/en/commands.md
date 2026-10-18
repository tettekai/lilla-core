# Writing commands and running them outside Discord

> This page is a translation of the [Japanese original](../ja/commands.md). If the two
> differ, the Japanese version is authoritative.

`!` commands such as `!model` and `!skill` work on a **command context**
(`lilla_core.commands.context.CommandContext`) instead of depending on a Discord message.
The core takes care of running them from Discord; if an extension provides a context for its
own client (one connected over WebSocket or HTTP, for example), the same commands can be run
from there too.

## Adding a command

Write one command per file and register it with `@register_command("name")`.
Commands that belong to an extension live in a package returned by
`Extension.command_packages()` (see [Extension basics](extensions.md)).

```python
from lilla_core.commands.registry import register_command


@register_command("hello")
async def handle_hello(ctx, arg: str, tools: dict, bot) -> None:
    """Handle `!hello [name]`."""
    name = arg.strip()
    if not name:
        await ctx.reply_error("Usage: !hello <name>")
        return
    await ctx.reply(f"Hello, {name}")
```

A handler takes `(ctx, arg, tools, bot)`.

| Argument | Meaning |
|----------|---------|
| `ctx` | The caller's command context |
| `arg` | The argument string after the command name (split once at the first whitespace; empty if none) |
| `tools` | The task tool registry |
| `bot` | The Discord client. Passed even when called from outside Discord (used for error notifications and the like) |

The context provides:

| Name | Meaning |
|------|---------|
| `await ctx.reply(text)` | Return the result to the same place the command came from |
| `await ctx.reply_error(text)` | Return an error the caller should see, such as a typo (the result becomes error) |
| `await ctx.start_conversation(content)` | Start one turn of the caller's normal conversation flow with `content` as the latest user message |
| `ctx.mark_failed()` | Mark the result as error without sending anything to the caller |
| `ctx.client_type` | The caller's type (`discord` for Discord) |
| `ctx.author_id` / `ctx.is_owner` | Who the caller is |
| `ctx.attachments` | Attached files (empty if none) |
| `ctx.discord_message` | The original Discord message, only when called from Discord (otherwise `None`) |

On Discord, both `reply` and `reply_error` reply to the original message. Text longer than
one message is split at line boundaries and the rest is sent to the same channel, so commands
do not need to split it themselves.

### Discord-only commands

Commands that need Discord features, such as editing a message with buttons or sending an
attachment, are registered with `discord_only=True` and use `ctx.discord_message`.
When called from outside Discord, the handler is not run and
"`!name` can only be run from Discord." is returned through `reply_error`.

```python
@register_command("refresh_panel", discord_only=True)
async def handle_refresh_panel(ctx, arg: str, tools: dict, bot) -> None:
    """Rewrite the panel that has buttons."""
    await ctx.discord_message.edit(content="Updated")
```

The core's `!selftest` is Discord-only as well, because it returns the details as an
attachment (from outside Discord, use `GET /api/selftest` on the shared HTTP server).

## Running commands outside Discord

An extension subclasses `CommandContext`, implements the three methods that do the actual
sending, and calls `dispatch_command(text, ctx)`.

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
        # Start one turn the same way as this client's normal conversation
        # (save to history, run_conversation, send the reply)
        start_my_conversation_turn(self._connection, content)


result = await dispatch_command("!model", MyClientCommandContext(connection))
```

- Create a new context for every run (otherwise replies and failures recorded for one run
  carry over to the next).
- `text` must start with `!` (surrounding whitespace is ignored). Anything else raises
  `ValueError`, so decide whether the input is a command before passing it.
- `tools` and `bot` can be omitted; the core uses what it registered at startup
  (`set_command_runtime()`).
- `deliver_conversation` does not need to wait for the turn to finish (it may start the
  conversation as a task and return).

### Result

`dispatch_command` returns a `CommandResult`.

| Field | Meaning |
|-------|---------|
| `status` | `"ok"` or `"error"` (`result.ok` works too) |
| `replied` | Whether the command replied with `reply` / `reply_error` |
| `conversation_started` | Whether the command started a conversation with `start_conversation` |

Even when a command sends nothing back, `status` tells you whether it finished or failed.
For example, `!runtask` does not reply and returns `ok` (or `error` on failure), so the client
can show a fixed completion message.

### Errors

| Situation | To the caller | Log / error channel | `status` |
|-----------|---------------|---------------------|----------|
| Input mistakes (a bare `!`, an undefined name for `!model`, a missing or disabled skill, ...) | The `reply_error` text | None | `error` |
| A Discord-only command called from outside Discord | The `reply_error` text | None | `error` |
| Input errors reported only through `notify_error` (an invalid tool name for `!runtask`, invalid JSON for `!mongodata`, ...) | Nothing | `notify_error` | `error` |
| Unknown commands and exceptions in a handler | Nothing | `notify_error` | `error` |

Exception messages are never put in the `CommandResult` and never reach the caller. When
`status` is `error` and nothing was replied, show a generic error message that contains no
internal details (the details are in the log and the error channel).

Where the output of a `!runtask` task goes (whatever the task tool sends to Discord) does not
depend on where the command was run from.

## Owner and approval

`dispatch_command` does not check approval. **It is the entry point for a caller that is
already authenticated as the owner.**

- On Discord, commands from anyone other than the owner still go through the approval flow
  before they run.
- For other clients, call it with an `is_owner=True` context only for connections the
  extension has authenticated, for example with a client token (`verify_client_token`).
  For a context whose `is_owner` is `False`, the core runs nothing and returns `error`.

Some commands rewrite the conversation history or MongoDB (`!cleardirty` / `!mongodata`) or
run tasks (`!runtask`). Never pass strings from an unauthenticated connection straight to
`dispatch_command`.
