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
available_tools:                # required; tools the LLM sees on this run ([] for none)
  - $main
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
- `available_tools` … **required**. The allow-list of tools the LLM sees on this run
  (see below). Startup fails if the key is missing

## The tool allow-list (`available_tools`)

Each scheduled LLM task has its own tool allow-list. It never silently inherits the
normal conversation's `tools.main_available_tools` (so that "no tools" and a forgotten
key can be told apart).

- Each entry is a tool YAML stem (such as `llm_weather`) or the token `$main`
- `$main` expands in place to the contents of `tools.main_available_tools`. When
  `main_available_tools` is unset (no filtering), it means every loaded LLM tool
- An empty list `[]` means no tools (`main_available_tools` is not consulted)
- Duplicates are removed after expansion. A stem that is not loaded, or any token other
  than `$main`, makes startup fail
- Only the tools in the expanded list whose `supported_client_type` is `task` or `all`
  are actually passed to the LLM. Other tools do not appear on this run even if listed

Examples:

```yaml
available_tools: []            # no tools

available_tools:               # the same set as a normal conversation
  - $main

available_tools:               # the conversation's set plus a tool just for this run
  - $main
  - llm_diary_draft_faircopy_get
```

To give a tool to one particular run only, set `supported_client_type: task` in that
tool's YAML and list it only in the `available_tools` of the tasks that use it (do not
put it in `main_available_tools`). A `supported_client_type: task` tool would otherwise
be usable by every task run; this setting is what limits it to the tasks that list it.

## What happens on each run

1. `prompt` is loaded and every `{{now}}` in it is replaced with the run time (a
   `YYYY-MM-DD HH:MM` timestamp in `ui.timezone`)
2. One LLM turn runs as a conversation with `client_type="task"`. Conversation history
   and user memos are the same ones a normal conversation sees; only the LLM tools
   allowed by `available_tools` are passed
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
