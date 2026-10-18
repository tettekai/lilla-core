# Skills

> This page is a translation of the [Japanese original](../ja/skills.md). If the two
> differ, the Japanese version is authoritative.

Skills let you save reusable instruction prompts under a name and call them with
`!skill <skill name>`. When called, the prompt is expanded as the **latest user message**
of that turn and handed to the LLM through the same flow as a normal conversation (it is
not added to the system prompt).

If you enable the built-in tool `llm_load_skill`, the LLM can also load the skills it needs
on its own, judging from their descriptions and the flow of the conversation
([Letting the LLM load skills itself](#letting-the-llm-load-skills-itself)).

## Registering

Skills are managed on the **Skills** page of the observability dashboard
([The observability dashboard](dashboard.md)). You can list, add, edit, delete, and
enable / disable them.

| Field | Meaning |
|-------|---------|
| Name | The name used to call the skill (required) |
| Description | Shown in the list (optional; shown in the dashboard list and in the `!skill` list; also listed in the system prompt's skill list in conversations where `llm_load_skill` is enabled, where it helps the LLM decide whether to load the skill) |
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

## Letting the LLM load skills itself

When the built-in tool `llm_load_skill` is enabled, the LLM sees a list of enabled skills
and can load one by name when it judges it necessary.

### Enabling it

Like other built-in tools it is opt-in. It is handed to the LLM only when you put
`${CONFIG_ROOT}/tools/llm_load_skill.yaml` and include it in `tools.sets` (`main`, etc.).

```yaml
type: llm_load_skill
# Limits of the skill list in the system prompt (defaults when omitted)
max_skills: 50              # number of skills listed
max_description_chars: 200  # maximum description length per skill
max_list_chars: 4000        # total characters of the list lines
```

```yaml
# lilla.yaml
tools:
  sets:
    main:
      - llm_load_skill
```

Each limit must be a positive integer; zero, negative or non-numeric values fail at startup.

### Arguments and result

| Argument | Description |
|----------|-------------|
| `name` | Skill name to load (required). Validated with the same rules as `!skill`, case-insensitive |
| `reason` | Why the skill is loaded or what will be done with it (optional). Not used in the result; it only stays as an argument of the tool call |

On success, the tool result is the skill body wrapped in the same "instruction from the
system" wrapper as `!skill` (without additional instructions). A name that breaks the rules,
a missing skill and a disabled skill each return an error (missing and disabled skills use
different messages).

The result is not cached; every call reads the `skills` collection. Editing, enabling or
disabling a skill in the dashboard takes effect from the next call.

### The list in the system prompt

Only when `llm_load_skill` is handed to the LLM in that conversation, a list of the
**names and descriptions** of enabled skills is added to the system prompt (bodies are not
included). It is not added when the tool is not in the set used, while `!disable_tools` is
on, or when the LLM is called without tools.

```text
## Available skills
You can load the body of the skills below by passing a name to the `llm_load_skill` tool. Check the names and descriptions and load a skill only when you judge it necessary.
- proofread: Proofread a text
- review
```

- Skills are sorted by name. A skill without a description shows only its name; line breaks
  in descriptions are replaced with spaces
- A description longer than `max_description_chars` is cut and ends with `…`
- Lines are added from the top until either the count limit (`max_skills`) or the total
  character limit of the lines (`max_list_chars`; the heading and intro are not counted) is
  reached. If some skills were left out, a final line says how many and that they can still
  be loaded by name. Enabled skills that are not listed can still be loaded with
  `llm_load_skill`
- If there is no enabled skill, the section is not added at all
- The list is not cached either; it is fetched every time the system prompt is built

Since the description is what the LLM uses to decide whether to load a skill, write it so
that it is clear what the skill does.
