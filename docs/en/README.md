# lilla-core documentation

> This page is a translation of the [Japanese original](../ja/README.md). If the two
> differ, the Japanese version is authoritative.

The index of the lilla-core user documentation.

## Getting started

- [Overview and design](overview.md) — what belongs in the core and what does not, constraints, features
- [Install and run](getting-started.md) — startup, `lilla.yaml`, secrets, loading extensions
- [Discord bot setup](discord-bot-setup.md) — intents and invite permissions

## Features

- [Registered channels](channels.md) — `discord.channels`
- [Channel notes (nightly summary)](channel-notes.md)
- [Searching history by room name](history-search.md)
- [Timezone](timezone.md) — `ui.timezone`
- [Choosing the LLM provider per turn](llm-resolver.md) — `type: resolver` in `llm.providers`
- [The observability dashboard](dashboard.md) — including the exposure warning
- [Tool contracts](tools.md) — LLM tools, task tools, built-in tools
- [Decision helper (Jev)](jev.md) — the optional `lilla_core.tool_support.jev`
- [Google OAuth / Google Calendar (official extension pack)](google.md) — extensions bundled in `lilla_core.extensions`

## Extensions

- [Extension basics](extensions.md) — method table, collision rules, locale catalogs, dependencies, compatibility
- [Config composition](extension-config.md) — `config_model()` / `env_fields()` / `get_section()`
- [Dashboard contributions](extension-dashboard.md)
- [Testing an extension](extension-testing.md) — `lilla_core.testing`

## Repository

- [Directory layout (overview)](directory-layout.md)
- [Contributing](../../CONTRIBUTING.md)
- [Changelog](../../CHANGELOG.md)
- [Security policy](../../SECURITY.md)
