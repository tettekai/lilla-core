# Skills

> This page is a translation of the [Japanese original](../ja/skills.md). If the two
> differ, the Japanese version is authoritative.

Skills let you save reusable instruction prompts under a name and call them with
`!skill <skill name>`. When called, the prompt is expanded as the **latest user message**
of that turn and handed to the LLM through the same flow as a normal conversation (it is
not added to the system prompt).

For now only explicit command calls are supported. Letting the LLM pick skills on its own
from their descriptions is planned as a future extension.

## Registering

Skills are managed on the **Skills** page of the observability dashboard
([The observability dashboard](dashboard.md)). You can list, add, edit, delete, and
enable / disable them.

| Field | Meaning |
|-------|---------|
| Name | The name used to call the skill (required) |
| Description | Shown in the list (optional; shown in the dashboard list and in the `!skill` list; planned as a hint for the LLM to choose skills) |
| Prompt | The prompt that is expanded (required) |
| Enabled | Disabled skills cannot be called (new skills start enabled) |

They are stored in the MongoDB `skills` collection (with a unique index on `name`).

### Naming rules

- Only ASCII letters and digits, underscore (`_`) and hyphen (`-`) are allowed, up to 64
  characters. Spaces are not allowed (command arguments are separated by spaces)
- Names are case-insensitive. They are normalized to lowercase both when saved and when
  called. You cannot register `Review` and `review` separately, and `!skill Review` calls
  `review`
- A name that breaks the rules, or that collides with an existing name, is rejected when
  adding or editing

## Calling

```
!skill <skill name> [additional instructions]
```

The skill prompt is wrapped in a start line, a preamble and an end line, so that the LLM
reads it as a **behavior instruction from the system** rather than something the user
said. Anything after the skill name (from the first space on) is trimmed and appended
after the wrapper as additional instructions, with a label (only the first line gets the
label if it spans several lines). For example, with `ui.locale: en`, calling a skill
`proofread` whose prompt is "Proofread the following text" with
`!skill proofread It was a nice day today.` sends the LLM this user message:

```text
[System instruction: skill "proofread"]
The following is not something the user said; it is a behavior instruction from the system. Follow this instruction when responding.

Proofread the following text
[/System instruction]

Additional instructions: It was a nice day today.
```

When there are no additional instructions (empty or only whitespace), the label line is
omitted and only the wrapper is sent:

```text
[System instruction: skill "proofread"]
The following is not something the user said; it is a behavior instruction from the system. Follow this instruction when responding.

Proofread the following text
[/System instruction]
```

The wrapper text and the label follow `ui.locale` (with `ja` they are
`[システム指示: スキル「proofread」]` / `[/システム指示]` / `追加指示: `).

- The expanded content is saved as-is in the conversation history as the user message.
  Editing or deleting the skill later does not change how past history reads; it keeps
  the content as it was when saved
- History saved in the earlier format (the `---SKILL: <skill name>---` …
  `---END_SKILL---` delimiter markers) is not migrated and stays as it is
- Replies are delivered the same way as a normal conversation (typing indicator, split
  messages, saving the reply to history). If a reply is in progress in the same channel,
  it is cancelled first, just like a normal conversation
- No mention is needed (same as other `!` commands)
- If the skill does not exist or is disabled, an error is sent back to the original
  channel without calling the LLM
- `!skill` from anyone other than the owner goes through the approval flow, like other
  commands (it runs once approved in the approval channel)

### Listing skills

Sending `!skill` without a skill name (an argument of only whitespace counts the same)
replies with the list of skills you can call, in the same channel, without calling the
LLM. With `ui.locale: en`:

```text
Available skills:
- `proofread`: Proofread text
- `review`
Usage: `!skill <skill name> [additional instructions]`
```

- Only **enabled skills** are listed, sorted by skill name in ascending order. Disabled
  skills cannot be called, so they are not listed
- A skill with a description is shown as `name: description`, one without as just the
  name. Line breaks in the description are replaced with spaces to keep it on one line
  (long descriptions are not truncated)
- If there are no enabled skills, you get "No skills are available" instead of a list,
  together with a note that you can register them on the Skills page of the dashboard
- If the list does not fit in one Discord message, it is split at line boundaries and sent
  as several consecutive messages
- Listing from anyone other than the owner also goes through the approval flow; once
  approved, the list is sent back to the original channel
