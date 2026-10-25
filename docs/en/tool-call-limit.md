# Summary when the tool call limit is reached (`llm.on_tool_call_limit`)

> This page is a translation of the [Japanese original](../ja/tool-call-limit.md). If
> the two differ, the Japanese version is authoritative.

When the number of tool loop round trips reaches the limit (`llm.max_tool_call_iterations`,
or the YAML `max_tool_call_iterations` for scheduled tasks), processing stops there.
`llm.on_tool_call_limit` chooses what is returned at that point.

```yaml
llm:
  max_tool_call_iterations: 10
  on_tool_call_limit: summarize   # default. Set to message to return only the old fixed text
```

| Value | Behavior |
|-------|----------|
| `summarize` (**default**) | Calls the LLM once more without tools, asks it to summarize the results so far, and returns that text |
| `message` | Returns only the fixed text from the locale catalog (`conversation.tool_call_limit`; inside `llm_expert`, `tool.llm_expert.tool_call_limit`) — the previous behavior |

Any other value makes startup fail.

## How `summarize` works

- An instruction is appended as a user message to the message list at the moment the
  limit is reached (conversation history, tool calls, tool results), and the LLM is called
  **once, without any tools**. The instruction is a fixed English text in the code and asks
  for the following:
  - processing was stopped partway because the tool call limit was reached, and no more
    tools can be used
  - the reply must start by telling the user that it stopped partway because of the limit
  - briefly summarize, kept separate, what was found out, what was done, and what is not done
    yet, in the usual voice from the system prompt and in the conversation's language
  - never describe unfinished work as completed, and do not change facts from tool results
  - mention that the remaining work can be continued if the user asks
- No fixed text is appended after the summary. Telling the user about the stop is left to
  the LLM's reply.
- META blocks (such as `set_session_memory`) and re-authentication notices are handled the
  same as for a normal final reply. For scheduled tasks (`task_scheduled_llm`), a summary
  that returns `NO_NOTIFICATION` is not sent, just like a normal reply.
- If the summary call fails or the reply is empty, a WARNING is logged and the same fixed
  text as `message` is returned (no exception). However, if the send was stopped by the
  [LLM send blocklist](llm-send-guard.md), it is handled like any other LLM call.

## Which provider writes the summary

- In normal conversations (`run_conversation`), the concrete provider used in the last loop
  writes it. If that provider has a [`final_reply_provider`](llm-final-reply.md), the
  summary counts as the final reply and the target writes it. The target's own
  `final_reply_provider` is not consulted.
- If the limit is reached while rewriting with `final_reply_provider`, the rewrite target
  writes the summary itself (rewrites are not stacked).
- When the limit is reached inside `llm_expert`'s inner loop, a summary is returned the same
  way, but it is written by the expert's `llm_provider` (the expert's reply is not the final
  reply to the user, so `final_reply_provider` is not consulted).

## Notes

- A turn that hits the limit makes one more LLM call (just one, without tools). It also
  leaves one record in the [LLM token usage log](llm-usage.md).
- Because no fixed text is appended, the LLM may still fail to mention the stop or may
  describe unfinished work as completed. If that is a concern, switch back to `message`.
