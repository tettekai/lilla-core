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
- [Scheduled notifications delegated to the LLM](scheduled-llm.md)
- [Timezone](timezone.md) — `ui.timezone`
- [Choosing the LLM provider per turn](llm-resolver.md) — `type: resolver` in `llm.providers`
- [Per-provider system prompt additions](llm-provider-prompt.md) — `llm.providers.<key>.prompt`
- [Rewriting the tool loop's final reply](llm-final-reply.md) — `llm.providers.<key>.final_reply_provider`
- [Summary when the tool call limit is reached](tool-call-limit.md) — `llm.on_tool_call_limit`
- [How long Ollama keeps a model loaded](llm-keep-alive.md) — `llm.providers.<key>.keep_alive`
- [LLM send blocklist](llm-send-guard.md) — `llm.send_blocklist_path`
- [Stripping prefixes from LLM responses](llm-response-prefix.md) — `llm.response_prefix_strip_patterns`
- [Recording LLM token usage](llm-usage.md) — `llm.usage_log` and the dashboard's Usage page
- [The observability dashboard](dashboard.md) — including the exposure warning
- [Skills](skills.md) — `!skill` and the Skills page of the dashboard
- [The shared HTTP server](http-server.md) — `http:`, Bearer tokens, adding routes from extensions
- [Tool contracts](tools.md) — LLM tools, task tools, built-in tools
- [Decision helper (Jev)](jev.md) — the optional `lilla_core.tool_support.jev`
- [Google OAuth / Calendar / Drive (official extension pack)](google.md) — extensions bundled in `lilla_core.extensions`
- [Local file read / write (official extension pack)](files.md) — `lilla_core.extensions.files`

## Extensions

- [Extension basics](extensions.md) — method table, collision rules, locale catalogs, dependencies, compatibility
- [Writing commands and running them outside Discord](commands.md) — `CommandContext` and `dispatch_command`
- [Config composition](extension-config.md) — `config_model()` / `env_fields()` / `get_section()`
- [Dashboard contributions](extension-dashboard.md)
- [Testing an extension](extension-testing.md) — `lilla_core.testing`

## Repository

- [Directory layout (overview)](directory-layout.md)
- [Contributing](../../CONTRIBUTING.md)
- [Changelog](../../CHANGELOG.md)
- [Security policy](../../SECURITY.md)
