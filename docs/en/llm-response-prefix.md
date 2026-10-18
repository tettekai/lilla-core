# Stripping prefixes from LLM responses

> This page is a translation of the [Japanese original](../ja/llm-response-prefix.md). If the two
> differ, the Japanese version is authoritative.

LLMs sometimes put a timestamp-like prefix at the start of a reply on their own
(e.g. `[19:14] I see…`). The system adds timestamps to user messages in the conversation
history, and the model may imitate them; suppressing this through the prompt is not reliable.

When replying on Discord, lilla-core splits a response into several messages (blocks) at
`---SPLIT---`, blank lines and timestamp boundaries. The regular expressions in
`llm.response_prefix_strip_patterns` are removed, in order, from the **start of each block**.

## Configuration

```yaml
llm:
  response_prefix_strip_patterns:
    - '\[(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{1,2} \d{2}:\d{2}\]\s*'
    - '\[\d{1,2}:\d{2}\]\s*'
```

- If omitted (or the key is present with an empty value), the two patterns above are the
  default: the `[Apr 28 11:22] ` form the system adds to history, and a plain `[HH:MM]` form.
- An empty list (`[]`) strips nothing.
- To add to the defaults, list the two defaults as well (your list replaces the defaults).
- An invalid regular expression fails at startup.

## Rules

- Each pattern only matches at the start of a block (like `re.match`). You do not need to
  write `^`. Matches in the middle or at the end are kept.
- Patterns are applied once each, in the order written. Each pattern is tried against the start
  of the block left by the previous one (with surrounding whitespace trimmed).
- The matched part is replaced with an empty string (removal only; no replacement string).
- A block that becomes empty, or contains no meaningful characters, is not sent.
- Only Discord replies in normal conversation are affected. The conversation history keeps the
  blocks as sent, i.e. after stripping. Paths that do not split the response, such as scheduled
  task notifications, are not affected.

## Writing patterns in YAML

Backslashes are not escapes inside YAML single quotes (`'...'`), so write each regex `\` once
(as in the example above). Inside double quotes (`"..."`) you need to double them as `\\`.
