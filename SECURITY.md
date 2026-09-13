# Security Policy

## Supported versions

Security fixes are accepted against the default branch (`main`). There is no long-term support window yet while the project is in early `0.x`.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security problems.

Prefer one of these:

1. [GitHub private vulnerability reporting](https://github.com/tettekai/lilla-core/security/advisories/new) for this repository (enable "Private vulnerability reporting" in repo settings if the link is unavailable)
2. Contact the maintainer via GitHub: [@tettekai](https://github.com/tettekai)

Include enough detail to reproduce the issue (affected version or commit, steps, impact). We will aim to respond within a reasonable time and coordinate a fix before any public disclosure.

## Scope notes

`lilla-core` talks to Discord and LLMs and can run tools configured by the host application. Treat bot tokens, API keys, and MongoDB URIs as secrets. Never commit real credentials. Prompt-injection and tool-abuse risks depend heavily on how the host wires tools and permissions; reports in that area are welcome.

`CONFIG_ROOT` and every tool directory (`paths.tool_root`, each extension's `tool_roots()`, and `${CONFIG_ROOT}/tools`) are trusted at the same level as the extension modules listed in `LILLA_EXTENSIONS`: anyone who can write there can run arbitrary code in the bot process. There is no allow-list for tool paths on top of that; the loaders only check that a resolved tool file still lies under one of those directories. Protect those directories with file-system permissions. A tool referenced by import path (`type: some_package.tools.x`) is loaded from the installed package with a regular `import`, so installed packages are trusted at the same level as extension modules.
