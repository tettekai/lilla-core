# Channel notes (nightly summary)

> This page is a translation of the [Japanese original](../ja/channel-notes.md). If the
> two differ, the Japanese version is authoritative.

A [registered channel](channels.md) can keep a short "note of the room": a factual
summary of what was said there on a given day, which outlives the TTL of the
conversation history. The core ships the batch that writes it as a built-in task tool,
disabled until you opt in by adding this YAML to
`${CONFIG_ROOT}/tools/task_channel_summary.yaml`:

```yaml
type: lilla_core.builtin_tools.task_channel_summary
# schedule: "0 2 * * *"      # default; interpreted in ui.timezone
# llm_name: summarizer       # default: llm.default
# max_turns: 500             # messages read per channel
# max_transcript_chars: 20000
```

- On each run it loops over `discord.channels` and summarizes **yesterday** (the
  calendar day in `ui.timezone`), so a 2 a.m. run does not summarize a day that only
  holds 00:00–02:00
- Only messages stored with a `discord_channel_id` are read; existing rows without one
  are left alone (no backfill)
- If a channel has nothing on the target day, its existing note is kept as is
- The summary is produced with a short fact-extraction prompt, not the bot's character
  prompt, and the transcript is passed as data inside `<channel_transcript>` tags
- Notes are stored in the `channel_summaries` collection, one document per channel
  (`discord_channel_id` is unique, and each run upserts it)
- With `discord.channels` empty, or without that YAML, nothing changes

When a conversation happens in a registered channel that has a note, the note is
inserted into the system prompt together with its `summary_date`, wrapped in
`<channel_note>` tags as untrusted context — past information, never instructions.
Unregistered channels and DMs never get it.

See [Tool contracts](tools.md) for how task tools work in general.
