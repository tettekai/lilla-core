# Discord bot setup

> This page is a translation of the [Japanese original](../ja/discord-bot-setup.md). If
> the two differ, the Japanese version is authoritative.

In the [Discord Developer Portal](https://discord.com/developers/applications), under
your application's **Bot** page, configure the following before inviting the bot.

## Privileged Gateway Intents

- **Message Content Intent** — turn this ON. `bot_client.py` requests
  `intents.message_content = True`; without it, Discord delivers messages with an empty
  body, so the bot can't read what was said and never responds.

No other privileged intent (Server Members / Presence) is required — the core doesn't
use guild member or presence data.

## Permissions for the bot invite URL

When generating the invite URL (OAuth2 URL Generator, `bot` scope), grant at least:

- **View Channels** — to see the channels it needs to read and post in
- **Send Messages** — replies, command output, and approval requests
  (`message.reply()` / `channel.send()` throughout `commands/` and `handlers/`)
- **Read Message History** — `cleardirty` resolves prior messages with
  `channel.fetch_message()`
- **Attach Files** — `selftest` and the approval flow send file attachments with
  `discord.File`

## Troubleshooting

If Message Content Intent is left off, the bot will appear to receive messages but
`message.content` will be empty, so it silently fails to respond. When Discord rejects the
intent at startup, the bot logs the cause at ERROR and exits.

If any of the above permissions are missing, expect send/reply failures or the bot being
unable to see the channels it needs.
