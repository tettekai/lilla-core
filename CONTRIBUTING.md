# Contributing to lilla-core

Thanks for your interest in contributing. This guide is for humans. For deeper design notes and AI-assisted development rules, see [`CLAUDE.md`](./CLAUDE.md).

日本語版は下の [日本語](#日本語) を参照してください。

## Development setup

Requirements: Python 3.12+, MongoDB (for a full bot run), a Discord bot token.

```bash
pip install -e ".[dev]"

export CONFIG_ROOT=./config.example
export DISCORD_TOKEN=...
export MONGODB_URI=mongodb://xxx

python -m lilla_core.bot
```

Copy `.env.example` and `config.example/` as needed. Secrets belong in environment variables or `.env`, never in committed files.

## Tests

```bash
pytest tests/
```

Please run the suite before opening a PR when you change code. Docs-only changes do not need a green local run if nothing under `src/` or `tests/` changed, but say so in the PR.

## Branch and pull requests

- Do **not** push directly to `main`.
- Branch from `main`, open a PR into `main`.
- Suggested branch name: `feature/#{issue}-{summary}` when an issue exists.

PR title and body should be in English and short:

```markdown
## Summary
- what changed, in one or two bullets

## Test plan
- [x] pytest tests/
```

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/) in English:

```text
type: short summary
```

Allowed types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`.
Add a scope when it helps (`feat(config): ...`).

- imperative mood (`add`, `remove`, `fix`)
- no trailing period
- one logical change per commit

## Scope of this repository

`lilla-core` is a general-purpose agent runtime. Keep character settings, domain-specific integrations, and purpose-specific HTTP/dashboard servers out of this repo. Extend via the extension points documented in the README and `CLAUDE.md`.

## Questions

Open a GitHub issue if something is unclear. Prefer a small, focused PR over a large mixed one.

---

## 日本語

コントリビュートありがとうございます。この文書は人向けの短い案内です。設計の詳細や開発ルールは [`CLAUDE.md`](./CLAUDE.md) を見てください。

### セットアップ

Python 3.12 以上。ボットを動かす場合は MongoDB と Discord Bot トークンも必要です。

```bash
pip install -e ".[dev]"

export CONFIG_ROOT=./config.example
export DISCORD_TOKEN=...
export MONGODB_URI=mongodb://xxx

python -m lilla_core.bot
```

秘匿情報は環境変数または `.env` に置き、リポジトリにはコミットしないでください。

### テスト

```bash
pytest tests/
```

コードを変えた PR では実行してください。ドキュメントのみの変更なら、その旨を PR に書いてください。

### ブランチと PR

- `main` への直接 push は禁止です。
- `main` からブランチを切り、`main` 向け PR を作ってください。
- Issue がある場合の目安: `feature/#{issue}-{概要}`

PR のタイトルと本文は英語・短めでお願いします（Summary / Test plan）。

### コミットメッセージ

Conventional Commits（英語）。型は `feat` / `fix` / `refactor` / `test` / `docs` / `chore`。

### このリポジトリの範囲

汎用のエージェント基盤だけを扱います。キャラクター設定や特定ドメイン連携などは拡張ポイント経由で外側に置いてください。
