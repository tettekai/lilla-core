# Install and run

> This page is a translation of the [Japanese original](../ja/getting-started.md). If the
> two differ, the Japanese version is authoritative.

## Requirements

- Python 3.12 or later
- MongoDB (used to persist conversation history, user memos, etc.)
- A Discord bot token (see [Discord bot setup](discord-bot-setup.md) for the Discord side)

## Running

```bash
pip install lilla-core

export CONFIG_ROOT=/path/to/config
export DISCORD_TOKEN=...
export MONGODB_URI=mongodb://xxx

python -m lilla_core.bot
```

When developing from a clone of the repository, install with `pip install -e ".[dev]"`
and set `CONFIG_ROOT=./config.example` to try a start with the sample config (see
[`CONTRIBUTING.md`](../../CONTRIBUTING.md) for the development workflow).

## Config files (`CONFIG_ROOT`)

The directory pointed to by `CONFIG_ROOT` should contain `lilla.yaml` (non-secret
structural config) and, if needed, `logging.yaml`. See
[`config.example/`](../../config.example/) for a sample.

`CONFIG_ROOT` may be set either as an OS environment variable or in `.env` (the OS
environment variable wins). If neither sets it, `/app/config` is used.

`lilla.yaml` must define at least one entry under `llm.providers`, and `llm.default`
must match one of those provider names — otherwise startup fails with a
`ValidationError`.

> **Privacy note:** `config.example/logging.yaml` sets the root logger to `DEBUG`.
> At that level, `core/http_util.py` logs request/response bodies to stdout, which
> can include LLM request bodies (i.e. the system prompt and conversation history).
> Keep this in mind when running with the example logging config, especially if
> stdout is collected somewhere.

## Secrets (environment variables and `.env`)

Secrets (tokens, connection strings, etc.) are read from environment variables or
`.env`. OS environment variables take precedence over `.env`.

| Variable | Required | Meaning |
|----------|----------|---------|
| `DISCORD_TOKEN` | Yes | The Discord bot token |
| `MONGODB_URI` | No | MongoDB connection string (default `mongodb://localhost:27017`) |
| `CONFIG_ROOT` | No | The directory holding `lilla.yaml` (default `/app/config`) |
| `HTTP_PROXY_USER` / `HTTP_PROXY_PASS` | No | Credentials for the HTTP proxy |

For local development, values in `.env` are also written into the process
environment at startup (any variable you already export takes precedence). This
covers LLM provider API keys as well (e.g. `GROK_API_KEY`, or whatever name you set
via `api_key_env` in `lilla.yaml`), which are read directly from the environment by
`api/llm_client.py` rather than through `AppConfig`.

## Loading extensions

To start with extensions loaded, set the `LILLA_EXTENSIONS` environment variable to a
comma-separated list of module import paths (omit it, or leave it empty, to start with
the core alone). Modules are loaded in the order given.

```bash
export LILLA_EXTENSIONS=my_extension_package,another_pack
python -m lilla_core.bot
```

See [Extension basics](extensions.md) for how to write one.

## Testing

When developing from a clone of the repository, run the tests with:

```bash
pytest tests/
```
