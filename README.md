<img src="https://raw.githubusercontent.com/tettekai/lilla-core/main/docs/lilla-logo.png" alt="Lilla" width="160" />

# lilla-core

> This README is a translation of the Japanese original,
> [README.ja.md](https://github.com/tettekai/lilla-core/blob/main/README.ja.md). If the two
> differ, the Japanese version is authoritative.

A core runtime for AI agents: it runs as a Discord bot and handles the tool_call loop
with an LLM (Ollama / OpenAI-compatible), command processing, and replies.

> **Note:** Conversation history, the active LLM provider, and session memory are **shared
> process-wide**, not split per channel or per user. lilla-core assumes a single owner
> talking to one bot process; it is not built for multi-user or multi-tenant use.

## Requirements

- Python 3.12 or later
- MongoDB
- A Discord bot token

## Install and run

```bash
pip install lilla-core

export CONFIG_ROOT=/path/to/config   # the directory holding lilla.yaml
export DISCORD_TOKEN=...
export MONGODB_URI=mongodb://...

python -m lilla_core.bot
```

Put `lilla.yaml` in the `CONFIG_ROOT` directory. See the documentation for how to write
it and how to set up the bot on the Discord side.

## Extensions

List module import paths, comma-separated, in the `LILLA_EXTENSIONS` environment variable
and they are loaded as extensions at startup. Leave it unset and the core runs on its own
as a Discord bot.

An extension subclasses `lilla_core.core.extension.Extension`, overrides only the methods
it needs, and exposes itself from its module as `extension = MyExtension()`.

Google OAuth (`lilla_core.extensions.google_oauth`) and Google Calendar
(`lilla_core.extensions.google_calendar`) ship with the core as official packs. Each is
loaded only when listed in `LILLA_EXTENSIONS`.

## Documentation

- [Documentation index](https://github.com/tettekai/lilla-core/blob/main/docs/en/README.md)
- [Issues](https://github.com/tettekai/lilla-core/issues)

## License

MIT
