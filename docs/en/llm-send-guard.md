# LLM send blocklist

> This page is a translation of the [Japanese original](../ja/llm-send-guard.md). If the two
> differ, the Japanese version is authoritative.

Even when your prompts and tools are designed not to pass personal information, real names
or email addresses can slip into conversation history or tool results. When you set
`llm.send_blocklist_path`, the request body is checked against a host-provided blocklist right
before it is sent to the LLM. A request that matches is not sent to the model.

This is a last safety net just before sending. It does not detect paraphrases,
abbreviations, or text inside images.

## Configuration

```yaml
llm:
  send_blocklist_path: /run/secrets/lilla-blocklist.json
```

- If omitted (the default), nothing is checked and requests are sent as before.
- **Only absolute paths** are accepted. Relative paths, `file:` / `dir:` prefixes and
  `${config_root}` expansion fail at startup. The list is meant to live outside `CONFIG_ROOT`.
- The list itself is not written in `lilla.yaml`.

The list file is a JSON array of strings (no comments).

```json
["Taro Yamada", "Yamada Taro", "090-0000-0000"]
```

## What is checked

- `chat_to_llm` / `chat_to_llm_with_tools` / `chat_to_llm_responses`, including direct
  calls that do not go through the conversation loop.
- Every string in the JSON body being sent (dictionary keys included): system prompt,
  messages, tool results, tool definitions, the provider's `extra_params`, and the Responses
  API `input` / `instructions`.
- Headers such as `Authorization` are not checked, so API keys never appear in the check or
  in logs.
- Image data URLs (`data:...;base64,...`) are skipped.
- Both sides are NFKC-normalized and compared case-insensitively. List entries match as
  substrings. Avoid short entries, which cause false positives.
- Nothing is stopped by guessing shapes such as email addresses. To stop your email
  address, add it to the list.

## On a match, or when the list cannot be read

- The request is not sent. It is not resent with the match masked.
- In regular Discord conversations, only "the message was not sent to the LLM because it may
  contain personal information" is posted to the error channel (`discord.error_channel_id`).
  The matched term is not included. The blocked user message is removed from the
  conversation history, so the next conversation is sent as usual (unless it also matches).
- Logs record only that a request was stopped. Matched terms, request bodies and headers
  are never logged or put in exception messages.
- The list is read once at startup and kept in memory. Restart after editing the file.
- If the path is set but the file cannot be read, is not JSON, is not an array of strings,
  or contains an empty string, the bot does not start (it exits with an error at startup),
  just like other configuration errors. An empty array is not an error; the bot starts with
  nothing to match.

## Out of scope

- Retracting content that has already been sent
- Paraphrases, abbreviations, text inside images
- Preventing tools from reading the list file. Handle that with host mounts and the tools'
  allowed scope
