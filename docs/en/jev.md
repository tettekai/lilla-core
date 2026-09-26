# Decision helper (Jev)

> This page is a translation of the [Japanese original](../ja/jev.md). If the two
> differ, the Japanese version is authoritative.

`lilla_core.tool_support.jev` is an optional helper for calling TypeSafe's System One model
(Jev). Jev does not write text: it takes a state and typed questions and returns
Choice / Score / Noul answers with probabilities.

## Scope

- **Opt-in**: nothing in the core imports it. The conversation loop and startup never touch
  Jev; only code that imports the helper and calls `ask_jev()` reaches the network
- **No dependency**: neither the official TypeSafe SDK nor any new package is a core
  dependency. HTTP is a single POST through `send_http_request` in `core/http_util.py`
  (proxy settings included)
- **Decisions stay outside**: mapping answers to provider names or choosing confidence
  thresholds belongs to the caller (a tool, or an LLM resolver script)
- No caching, no process-wide client, no YAML config section

## Usage

```python
from lilla_core.tool_support.jev import (
    DIFFICULTY_SCORE, NEEDS_TOOL_NOUL, JevError, ask_jev, choice_question,
)

try:
    result = await ask_jev(
        {"message": latest_text},
        {
            "difficulty": DIFFICULTY_SCORE,   # Score from 0 to 2
            "needs_tool": NEEDS_TOOL_NOUL,    # yes / no
            "tone": choice_question({"casual": None, "formal": None}),
        },
    )
except JevError:
    return None  # fall back to the default branch

if result.answers["difficulty"].score >= 1.5:
    ...
```

- `state`: the input to judge (any JSON value: string, dict, list, ...)
- `questions`: question name → question. Build them with `choice_question()` /
  `score_question()` / `noul_question()`, or pass dicts of the same shape
  (`{"type": "noul", "instructions": ...}` and so on)
- `DIFFICULTY_SCORE` / `NEEDS_TOOL_NOUL` are examples of common questions. Use them as they are
  or replace them with your own (questions are copied before sending, so the constants never change)
- Keyword arguments: `api_key` (defaults to the `TYPESAFE_API_KEY` environment variable),
  `url` (defaults to `https://api.typesafe.ai/v1/systemone`), `model` (defaults to
  `jev-latest`), `timeout` (defaults to 10 seconds)

The returned `JevResult.answers` uses the same keys as the API's `answers` (the question names
you sent). Values are `ChoiceAnswer` (`choice` / `confidence` / `probabilities`), `ScoreAnswer`
(`score` / `confidence` / `legend` / `probabilities`, keyed by integer level) or `NoulAnswer` (`noul`).

## Errors

Every failure is raised as a subclass of `JevError`. Messages are always in English.

| Exception | When |
|-----------|------|
| `JevConfigError` | No API key, `state` is `None`, or a malformed question (detected before sending) |
| `JevRequestError` | An HTTP error response (with a `status` attribute), a connection failure, or a timeout (`status` is `None`) |
| `JevResponseError` | The body is not JSON, has the wrong shape, or lacks an answer for a question you sent |

Do not put the API key in YAML; pass it through `.env` / OS environment variables or the argument.
