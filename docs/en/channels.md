# Registered channels

> This page is a translation of the [Japanese original](../ja/channels.md). If the two
> differ, the Japanese version is authoritative.

Channels listed under `discord.channels` in `lilla.yaml` can use a different rule for
starting a conversation.

```yaml
discord:
  my_user_id: "XXXXXXXXX"
  channels:
    - name: dev
      channel_id: "123456789012345678"
      mention_optional: true
    - name: lounge
      channel_id: "234567890123456789"
      # mention_optional defaults to false
```

- `name`: an alias used by the configuration. It does not have to match the channel's
  current name on Discord
- `channel_id`: the channel snowflake, as a string (same form as `approval_channel_id`)
- `mention_optional`: when `true`, the bot also replies to the owner's messages in that
  channel without a mention. It defaults to `false`, which keeps the current rule
  (mention or DM)
- Leaving `channels` unset or empty keeps the receiving behaviour exactly as it is
- A duplicated `name` or `channel_id` fails at startup

A conversation in a registered channel gets a short line in the system prompt saying the
bot is currently in that channel (unregistered channels and DMs do not get it). The
conversation history itself still spans every channel and DM, registered or not.

Features built on registered channels:

- [Channel notes (nightly summary)](channel-notes.md)
- [Searching history by room name](history-search.md)
