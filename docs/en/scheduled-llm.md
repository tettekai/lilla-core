# Scheduled notifications delegated to the LLM

> This page is a translation of the [Japanese original](../ja/scheduled-llm.md). If the
> two differ, the Japanese version is authoritative.

Notifications of the form "look around every 30 minutes and speak up if there is
something to say" are easier to write when the decision itself is delegated to the LLM.
The scheduled task for that ships with the core as a built-in tool, but it is not enabled
by default: put a `task_*.yaml` under `${CONFIG_ROOT}/tools/` to opt in.

```yaml
# ${CONFIG_ROOT}/tools/task_reminder.yaml
type: lilla_core.builtin_tools.task_scheduled_llm
schedule: "*/30 * * * *"        # cron, interpreted in ui.timezone
target: dm:{DISCORD_MY_USER_ID} # dm:USER_ID / channel:CHANNEL_ID
llm_provider: reminder          # required; a provider name from llm.providers
prompt: dir:${config_root}/prompts/reminder  # required; file: / dir: (a list works too)
```

The YAML file name (stem) becomes the tool name, so you can line up as many tasks as you
like with the same `type` but different `schedule` / `target` / `prompt`.

## The settings

- `schedule` … a cron expression. If omitted, the task is not registered with the
  scheduler and can only be run by hand with `!runtask <tool name>`
- `target` … where to notify. Writing `{DISCORD_MY_USER_ID}` substitutes
  `discord.my_user_id`. When it is unset the task returns without calling the LLM (there
  is nowhere to send to)
- `llm_provider` … **required**. A provider name from `llm.providers`. Startup fails if
  it is missing
- `prompt` … **required**. Passed straight through as a
  [`file:` / `dir:` source spec](tools.md). `dir:` concatenates the `.md` / `.txt` files
  in that directory in ascending file-name order. Startup fails if it is missing

## What happens on each run

1. `prompt` is loaded and every `{{now}}` in it is replaced with the run time (a
   `YYYY-MM-DD HH:MM` timestamp in `ui.timezone`)
2. One LLM turn runs as a conversation with `client_type="task"`. Conversation history,
   user memos and the LLM tools are the same ones a normal conversation sees
3. If the reply is `NO_NOTIFICATION` (surrounding whitespace, and `*`-wrapped forms such
   as `**NO_NOTIFICATION**`, count as the same) or empty, **nothing is stored in the
   conversation history and nothing is sent to Discord**
4. Any other reply is appended to the conversation history as an assistant message and
   sent to `target`
5. Exceptions during a run are logged at ERROR and never escape (the next run happens as
   usual)

Because the LLM itself decides that "there is nothing this time", waking the task up on a
short interval does not mean more notifications. Write the prompt so that it replies with
just `NO_NOTIFICATION` when no notification is needed.

For how task tools work in general, see [the tool contract](tools.md).
