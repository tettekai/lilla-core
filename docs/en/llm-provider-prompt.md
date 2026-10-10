# Per-provider system prompt additions (`llm.providers.<key>.prompt`)

> This page is a translation of the [Japanese original](../ja/llm-provider-prompt.md). If
> the two differ, the Japanese version is authoritative.

The system prompt (`prompt.system` and friends) is shared by the whole conversation.
Different models respond differently to the same instructions, so you can set `prompt`
on a concrete provider entry in `llm.providers` (`type: openai_compat` /
`type: ollama`) to add a short note to the end of the system prompt, only on turns
sent to that provider. Keep the shared persona in `prompt.system` and put only the
per-model adjustments here.

```yaml
llm:
  providers:
    mimo:
      type: openai_compat
      url: https://api.example.invalid/v1
      model: example-model
      api_key_env: EXAMPLE_API_KEY
      prompt: file:prompts/mimo.md
```

## How to write it

- The value is a source spec, the same format as `prompt.system`. It starts with `file:`
  (a single file) or `dir:` (`.md` / `.txt` files in file-name order), and you may list
  several. `${config_root}` is expanded and relative paths resolve against `CONFIG_ROOT`.
- You cannot write the text itself in YAML. A value that does not start with `file:` /
  `dir:` (inline text, or a path without a prefix) and an empty value both fail at
  startup with a `ValidationError`.
- If you leave it out, nothing is added (the previous behavior).
- Like the other prompts, the content is read from disk on every call, so edits take
  effect without a restart. A missing file means nothing is added.

## Where it goes

The addition is appended, not substituted. It goes after the shared system prompt,
separated by a blank line. If there is a per-`client_type` addition (`prompt.discord` or
an extension's `client_prompt_providers()`), the provider's addition comes after the
system prompt that already includes it.

```
(shared system prompt)
(per-client_type addition)

(prompt of the concrete provider actually used)
```

## Which provider's addition is used

The lookup is by the key in `llm.providers`, not by the `model` string. Once the
concrete provider for a call is decided, that entry's `prompt` is read. Keys are
decided as before:

- an explicit `llm_name` (in a task YAML, for example) or a key pinned with `!model`
  uses that key's `prompt`
- a `type: resolver` key uses the `prompt` of the key returned by the script
  ([Choosing the LLM provider per turn](llm-resolver.md)); a call that falls back uses
  the `fallback` key's `prompt`
- passing a resolver name directly to `chat_to_llm` / `chat_to_llm_with_tools` /
  `chat_to_llm_responses` (outside `run_conversation`) also uses the `fallback` key's
  `prompt`

If the resolver picks a different concrete provider partway through the tool loop, each
LLM call uses the addition of the provider it actually calls (the previous call's
addition is not carried over).

When a direct caller passes `messages` that already contain a system message, the
addition is appended to the first system message whose content is a string, again
separated by a blank line. With the Responses API (`chat_to_llm_responses`) it is
appended to `instructions`.

## On a resolver entry

Writing `prompt` on a `type: resolver` entry does not fail startup; it is **ignored**
(its format is not checked either). A resolver never sends anything to an LLM, so it
does not own an addition. To add a note on resolver-routed turns, set `prompt` on each
concrete provider entry the resolver can pick.
