# Prompt templates (Jinja2)

> This page is a translation of the [Japanese original](../ja/prompt-template.md). If the two
> differ, the Japanese version is authoritative.

The body of a prompt written by the operator is expanded as a Jinja2 template each time it is
used, and then passed to the LLM. There is a single expansion routine
(`lilla_core.services.prompt_template.render_template()`), so every prompt is expanded by the
same rules.

## What is expanded and what is not

Only prompts written by the operator are expanded.

| Prompt | Variables passed |
|--------|------------------|
| `prompt.system` / `prompt.conversation` / `prompt.discord` | `current_hour` |
| An LLM provider's `prompt` ([Per-provider additions](llm-provider-prompt.md)) | `current_hour` |
| `prompt` of `llm_expert` ([Tool contracts](tools.md)) | `current_hour` |
| `prompt` of `task_scheduled_llm` ([Scheduled runs](scheduled-llm.md)) | `current_hour` |
| The text returned by an extension's `client_prompt_providers()` | `current_hour` |
| A skill's body (`!skill` and `llm_load_skill`; [Skills](skills.md)) | `current_hour` |
| An extension's prompt ([Defaults and overrides](extension-prompt.md)) | `current_hour`, `settings` |

The following are data and are not expanded (a `{{` inside them is passed through as it is).

- Conversation history, user memos, channel notes, tool results, and replies from external agents
- The additional instruction of `!skill` (user input), and skill names and descriptions (the skill list)
- Fixed wording written in code

## Common variables

| Variable | Contents |
|----------|----------|
| `current_hour` | The current date and time in `ui.timezone` ([Timezone](timezone.md)) as `YYYY-MM-DD HH:xx` (e.g. `2026-11-14 20:xx`). No weekday is added |

Minutes are hidden and only the hour is shown so that the system prompt changes only once an hour,
letting the LLM side's prompt cache work (the same idea as the current time in the conversation
history). In `task_scheduled_llm`, the reference time is that task's run time.

```markdown
It is now {{ current_hour }}. Let me know if there is anything to finish today.
```

## Writing rules

- You can use normal Jinja2 syntax (`{{ name }}`, `{% if %}`, `{% for %}`, filters, and so on).
  Expansion runs in a sandbox, and a template cannot reach Python attributes or modules. Since
  the output is not HTML, autoescaping is off
- An undefined name (a typo, or the retired `{{now}}`, etc.) is not turned into an empty string;
  it fails
- A trailing newline of the body is kept (a body with no template syntax is unchanged by
  expansion)
- To write `{{` / `{%` / `{#` literally, wrap it in `{% raw %}…{% endraw %}`

## When it fails

When expansion fails (a syntax error, an undefined name, etc.), a WARNING log that includes which
prompt it was is written, and the **body before expansion is used as it is**. The conversation or
task does not stop.

Only for skills, saving on the dashboard's Skills page trial-expands with the current time and
refuses to save with a 400 if it cannot be expanded (a trial expansion can only check the
branches it takes). For the save-time check of an extension prompt override, see
[Extension default prompts and overrides](extension-prompt.md). In these two input fields of the
dashboard, you can check the result of the same trial expansion while typing
([Jinja2 template input field](dashboard.md#jinja2-template-input-field)). Prompts read from
files have no save-time check, so check the WARNING in the log after rewriting them.

## Migrating from `{{now}}`

The former `task_scheduled_llm` replaced `{{now}}` in the prompt with the run time as
`YYYY-MM-DD HH:MM`. That replacement is gone, and `{{now}}` now fails expansion as an undefined
name (a WARNING is logged and the prompt is passed to the LLM with `{{now}}` left in).

Rewrite `{{now}}` to `{{ current_hour }}`. Minutes are no longer included. Also check whether
existing prompts contain `{{` / `{%` / `{#` as literal text, and if so wrap them in `{% raw %}` or
change the wording.

```sh
grep -rnE '\{\{|\{%|\{#' "$CONFIG_ROOT/prompt" "$CONFIG_ROOT/tools"
```
