# Rewriting the tool loop's final reply (`llm.providers.<key>.final_reply_provider`)

> This page is a translation of the [Japanese original](../ja/llm-final-reply.md). If
> the two differ, the Japanese version is authoritative.

If you hand the tool loop over to a cheaper model partway through (such as a local
Ollama model), the final reply may not match the main model's tone or quality. When a
concrete provider entry (`type: openai_compat` / `type: ollama`) sets
`final_reply_provider`, and that provider returns the final reply of the tool loop, the
reply text is discarded and the specified provider rewrites it.

```yaml
llm:
  providers:
    deepseek:
      type: openai_compat
      url: https://api.example.invalid/v1
      model: example-main
      api_key_env: EXAMPLE_API_KEY
    local:
      type: ollama
      url: http://localhost:11434
      model: example-local
      final_reply_provider: deepseek
```

When to hand over to `local` (only after a tool call, only while the local LLM is up,
time of day, and so on) is decided by your [resolver script](llm-resolver.md).
`final_reply_provider` only handles the rewrite.

## Behavior

- In the tool loop of `run_conversation`, when the concrete provider that returned the
  response without tool_calls (the final reply) has `final_reply_provider`, that final
  reply text is discarded.
- From then on, the specified provider handles the reply. It is called with the same
  tools and system prompt as the original tool loop (if the target has a `prompt`, its
  addition is appended as usual).
- The input is the message list without the discarded final reply (conversation history,
  tool calls, tool results), as is. No rewrite instruction is added.
- When the target returns a response without tool_calls, that becomes the final reply. If
  it does not call any tools, this takes a single call.
- When the target calls tools, they are executed the same way as in the original tool loop
  (including the error result for broken argument JSON, re-authentication notices, and
  tool call notifications), and the target is called again with the tool results added.
  During this, the [resolver script](llm-resolver.md) is not consulted again, and the
  original provider is never used again.
- The number of tool call iterations is counted together with the original tool loop, up
  to `llm.max_tool_call_iterations` (or the limit passed by the caller). When the limit is
  reached, the same abort message as the normal tool loop is returned.
- The target's own `final_reply_provider` is not consulted. The rewrite is a single step
  and never chains.
- META blocks (such as `set_session_memory`) are processed on the rewritten text.
- If the rewrite call fails, it is handled like any other LLM call failure (the discarded
  text is not used as a fallback).

The rewrite does not happen on these paths:

- Conversations without tools (no tools, during `!disable_tools`, an empty allow list, etc.)
- The inner loop of `llm_expert`, and tasks or commands that call `chat_to_llm` directly
- Turns aborted because the tool call iteration limit was reached

## Configuration

- The value is the name of a concrete provider in `llm.providers`.
- The following fail at startup (fail-fast):
  - pointing to a name that is not defined
  - pointing to a resolver
  - setting `final_reply_provider` on a resolver entry
- If unset, no rewrite happens (unchanged behavior).

The rewrite adds one LLM call (more if the target calls tools). Each call is recorded as
one entry for the target provider in [LLM token usage](llm-usage.md).
