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
| Description | Shown in the list (optional; planned as a hint for the LLM to choose skills) |
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

Anything after the skill name (from the first space on) is appended as additional
instructions after the expanded block. For example, calling a skill `proofread` whose
prompt is "Proofread the following text" with `!skill proofread It was a nice day today.`
sends the LLM this user message:

```
---SKILL: proofread---
Proofread the following text
---END_SKILL---

It was a nice day today.
```

- The expanded content is saved as-is in the conversation history as the user message
- Replies are delivered the same way as a normal conversation (typing indicator, split
  messages, saving the reply to history). If a reply is in progress in the same channel,
  it is cancelled first, just like a normal conversation
- No mention is needed (same as other `!` commands)
- If the skill does not exist or is disabled, an error is sent back to the original
  channel without calling the LLM
- `!skill` from anyone other than the owner goes through the approval flow, like other
  commands (it runs once approved in the approval channel)
