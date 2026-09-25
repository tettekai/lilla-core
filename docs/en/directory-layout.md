# Directory layout (overview)

> This page is a translation of the [Japanese original](../ja/directory-layout.md). If
> the two differ, the Japanese version is authoritative.

```
src/lilla_core/
├── bot.py                # Discord bot entry point
├── bot_client.py         # Shared module holding the commands.Bot instance
├── core/                 # Config management, the Extension base class, shared utilities
├── commands/             # `!command` implementations (one file per command)
├── handlers/             # Dispatch for Discord events/commands, scheduled tasks, dashboard server
├── services/             # tool_call loop, conversation history, system prompt build
├── dashboard/            # SPA for the observability dashboard (static files)
├── ui/ locales/          # Catalogs of user-facing Discord text (ja / en)
├── utils/                # Shared utilities for dates, paths, hashing, etc.
├── loaders/              # Dynamic loading of tools (llm_*.yaml / task_*.yaml)
├── api/                  # LLM client (Ollama / OpenAI-compatible)
├── repository/           # Data persistence to MongoDB
├── builtin_tools/        # Built-in tools shipped with the core (opt-in)
├── extensions/           # Official packs (Google OAuth / Calendar; opt in via LILLA_EXTENSIONS)
├── tool_support/         # Opt-in helpers for tool implementations
└── testing/              # Test helpers for extension repositories (opt-in)
tests/                    # Unit tests (pytest)
config.example/           # Sample lilla.yaml / logging.yaml
docs/                     # Documentation (ja is authoritative, en is a translation)
```

The role of each file and the detailed design are documented in
[`CLAUDE.md`](../../CLAUDE.md).
