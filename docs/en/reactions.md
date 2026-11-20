# Reactions on messages

> This page is a translation of the [Japanese original](../ja/reactions.md). If the two
> differ, the Japanese version is authoritative.

Emoji that users attach to messages — Discord reactions, stamps in a custom client, and so on —
are stored linked to the conversation history (`conversations`), so the agent can tell in its next
reply which of its messages resonated with the user.

## Storage format

Reactions go into the `reactions` array of each conversation history document. A message with no
reactions has no such field at all (when the last one is removed, the field is removed too).

```json
{
  "message": {"role": "assistant", "content": "..."},
  "time": "...",
  "reactions": [
    {"emoji": "❤️", "source": "discord", "reacted_at": "2026-11-10T07:25:00Z"},
    {"emoji": "lilla_smile", "emoji_id": 123456789012345678, "source": "discord", "reacted_at": "..."}
  ]
}
```

- `emoji`: for a standard emoji, the character itself (`❤️`); for a Discord custom emoji, its
  name. Shortcodes such as `:heart:` are an input notation and are not stored
- `emoji_id`: the ID of a custom emoji (standard emoji have none)
- `source`: the route it was added through (`discord` / `client`, etc.)
- `reacted_at`: when it was added (UTC)

The same emoji can appear at most once on a message (standard emoji are compared by character,
custom emoji by ID). Adding a duplicate does nothing, and removing removes it if present. An
emoji is removed whichever route added it.

The server does not treat any emoji specially. Variants such as `❤` and `❤️` are stored as
different emoji, and no meaning is attached to individual emoji (leave that to the consumer).
Reaction information is never mixed into `message.content`.

In environments where `memory.conversation_ttl_hours` is enabled, when a message expires by TTL
its reactions disappear with it.

## Shared add / remove logic and hooks

Route-independent add / remove lives in `lilla_core.services.reactions`. Discord and any custom
client an extension has should go through the same code.

```python
from lilla_core.services.reactions import ReactionEmoji, add_reaction, remove_reaction

await add_reaction(conversation_id, ReactionEmoji("❤️"), "client")
await remove_reaction(conversation_id, ReactionEmoji("lilla_smile", 1234), "client")
```

`conversation_id` is the same value as `_id` in `ConversationRepository.list_for_client()`.
The return value says whether anything actually changed (`False` for adding a duplicate or
removing an emoji that is not there). If the target conversation history does not exist it raises
`ConversationNotFoundError` (`lilla_core.core.exceptions`; also importable from
`lilla_core.services.reactions`), so a custom client can return an error for a "nonexistent
message" and treat `False` (no change) as normal.

When something actually changed, hooks registered with `register_reaction_hook()` receive a
`ReactionChange` (`action` (`added` / `removed`), `conversation_id`, `emoji`, `source`, `time`,
`reactions`). `reactions` is the message's whole reaction list after the change (the same shape
as `reactions` in `list_for_client()`; an empty list after the last one is removed), read
atomically with the update. So that several clients and Discord adding and removing at the same
time cannot drift apart, we recommend sending this list as is rather than accumulating the
deltas (`action`). Notifying clients connected over WebSocket and the like should be done by
registering a hook in the extension's `setup()` (sync or async functions both work; a hook's
failure only logs a WARNING and does not stop saving or the other hooks).

```python
from lilla_core.services.reactions import register_reaction_hook

async def notify(change):
    ...  # send to connected clients

register_reaction_hook(notify)
```

## Discord

Reactions added to or removed from the bot's messages are received through the raw events
(`on_raw_reaction_add` / `on_raw_reaction_remove`) and stored. Old messages that are not in the
cache and DMs are picked up too.

- The target message is identified from the stored `discord_message_ids` (a reply sent in
  several parts has several). If there is no matching conversation history, nothing happens
- Reactions added by the bot itself are ignored
- As with normal conversation, only reactions from the owner (`discord.my_user_id`) are accepted

For the required intents see [Discord bot setup](discord-bot-setup.md) (they are not privileged
intents, so nothing needs to be set in the Developer Portal).

## Retrieval

Use the methods of `ConversationRepository` (`lilla_core.repository.conversation_repository`).
Exposing them as an HTTP API or similar is up to the consuming application.

- `list_for_client()`: includes `reactions` in each element (an empty array when there are
  none). The shape of the existing items (`_id`, `time`, `message.role`, `message.content`) does
  not change
- `list_reacted(limit, offset, emojis=None, after_id=None, since=None)`: returns only messages
  that have reactions, in the same shape, order and continuation as `list_for_client()`. Giving
  several `emojis` (a character for standard emoji, a name for custom emoji) narrows to messages
  that have any of them
- `count_reactions()`: counts per emoji over all time and returns `{"emoji", "count"}` (plus
  `emoji_id` for custom emoji) in descending order of count. It also picks up old emoji that do
  not appear in the current list
- `get_reactions(conversation_id)`: returns the current `reactions` of one conversation history
  entry in the same element shape as `list_for_client()` (an empty list if none). Use it when you
  want to return the current state after an operation that does not call the hooks, such as
  adding a duplicate or removing an emoji that is not there. It raises
  `ConversationNotFoundError` if the conversation history does not exist, and `ValueError` if
  `conversation_id` is not a valid ObjectId

Messages with reactions are a tiny fraction of the whole, so a partial index
(`reactions_time_id`) covering only documents that have `reactions` is created at startup, and
`list_reacted()` and `count_reactions()` use it.

## How it is passed to the LLM

Reactions are not mixed into each message of the conversation history. Instead a "Recent
Reactions" section (`## Recent Reactions`) is added at the end of the system prompt, listing only
reactions on messages included in the conversation history sent that turn.

```
## Recent Reactions
...
- ❤️ on your message [Nov 10 16:22] "今日の予定をまとめると…" (reacted Nov 10 16:25)
```

- Each line has the emoji (`:name:` for custom emoji), the speaker of the target message, a
  timestamp in the same format as the history, an excerpt (one line, up to 80 characters) and the
  time it was added. Message IDs and numbers are not passed
- Up to 20 entries are listed, newest reaction time first
- If there are no applicable reactions, the section is not emitted at all
- The section changes when a reaction is added or the history range moves, so it is placed at the
  very end of the system prompt to break the prefix cache as little as possible
