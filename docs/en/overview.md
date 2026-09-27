# Overview and design

> This page is a translation of the [Japanese original](../ja/overview.md). If the two
> differ, the Japanese version is authoritative.

lilla-core is the core runtime of Lilla: a general-purpose foundation library for
building AI agents.

It runs as a Discord bot, receives messages, calls an LLM (Ollama / OpenAI-compatible),
and provides the "skeleton of an agent": the tool_call loop, command processing, and
replies.

## What belongs in the core

Character settings, domain-specific tools (integrations with external services, etc.),
and purpose-specific HTTP servers (a machine-facing Bearer API, say) — anything that
varies per user — are kept out of the core. Such elements are added from the outside by
subclassing `Extension` (`core/extension.py`) and listing the module in the
`LILLA_EXTENSIONS` environment variable, which is loaded at startup
([Extension basics](extensions.md)).

The observability dashboard is the one exception: the core owns it, because everything
it shows (conversation history, user memos, logs) is core state. Extensions add tabs and
HTTP routes to it through their `dashboard_*()` declarations
([The observability dashboard](dashboard.md)).

lilla-core is designed to be able to start as a standalone Discord bot with zero
extensions loaded. Every `Extension` method has a safe default that contributes
nothing, so the core never depends on the presence of extensions.

## Constraints

- Conversation history, `runtime_state` (the active LLM provider, tools-disabled flag),
  and session memory are **all shared process-wide**, not scoped per channel or per
  user. `repository/conversation_repository.py` does not separate history by channel
  or user, so conversations from different channels are mixed into a single history.
  lilla-core is built around a single owner talking to one bot process; it is not
  designed for multi-user or multi-tenant use.

## Features

- Runs as a Discord bot out of the box (works with no extensions)
- Switch between multiple LLM providers via config: Ollama (with Wake-on-LAN support)
  and OpenAI-compatible APIs
- tool_call loop, plus management of conversation history, user memos, and session
  memory
- Plugin-style `!` commands (just drop a file into `commands/`)
- Scheduled tasks via APScheduler (`task_*.yaml`)
- Approval flow (commands from non-owners can be approved/rejected on Discord)
- `Extension` adapter class for customization without modifying the core, with any
  number of extensions loaded per process (extra repositories, message hooks, startup
  work, result delivery, client-specific prompts, conversation-start hooks, tool
  execution context, extra tool roots, and extra command packages)
- User-facing Discord text is pulled from locale catalogs (`ja` / `en`); extensions can
  ship their own catalogs under their own name
- One configurable timezone (`ui.timezone`) for schedules, "today", and the
  current time shown to the LLM
