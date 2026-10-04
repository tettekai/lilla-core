# Scheduled notifications delegated to the LLM

> This page is a translation of the [Japanese original](../ja/scheduled-llm.md). If the
> two differ, the Japanese version is authoritative.

Notifications of the form "look around every 30 minutes and speak up if there is
something to say" are easier to write when the decision itself is delegated to the LLM.
The scheduled task for that ships with the core as a built-in tool, but it is not enabled
by default: put a `task_*.yaml` under `${CONFIG_ROOT}/tools/` to opt in.

```yaml
# ${CONFIG_ROOT}/tools/task_reminder.yaml
type: task_scheduled_llm
schedule: "*/30 * * * *"        # cron, interpreted in ui.timezone
target: dm:{DISCORD_MY_USER_ID} # dm:USER_ID / channel:CHANNEL_ID
llm_provider: reminder          # required; a provider name from llm.providers
prompt: dir:${config_root}/prompts/reminder  # required; file: / dir: (a list works too)
available_tools:                # optional; tools the LLM sees on this run (omit or [] for none)
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
- `available_tools` … optional. The allow-list of tools the LLM sees on this run (see
  below). If the key is missing, the task starts up with no tools. Startup fails if the
  key is present but its value is invalid
- `max_tool_call_iterations` … optional. The tool-call round-trip limit for this task
  only (a positive integer). If omitted, `llm.max_tool_call_iterations` (default 10) is
  used. Startup fails for non-integers, `true` / `false`, or values below 1. Hitting the
  limit does not raise; the task returns the abort message, which is sent to `target`
  like any other reply (it is not `NO_NOTIFICATION`)

## The tool allow-list (`available_tools`)

Each scheduled LLM task has its own tool allow-list. It never silently inherits the
normal conversation's `main` tool set.

- Each entry is a tool YAML stem (such as `llm_weather`) or a
  [tool set](tools.md#tool-sets-toolssets) reference written as `$` followed by the set
  name (such as `$main`)
- `$main` expands in place to the contents of `tools.sets.main`. When `main` is unset it
  is empty (no tools). Other sets such as `$health` expand the same way
- An empty list `[]`, or omitting the key entirely, both mean no tools
  (the `main` set is not consulted)
- Duplicates are removed after expansion. A stem that is not loaded, or a set name that
  is not defined, makes startup fail
- Only the tools in the expanded list whose `supported_client_type` is `task` or `all`
  are actually passed to the LLM. Other tools do not appear on this run even if listed

Examples:

```yaml
# omitting the available_tools key entirely also means no tools (same as below)
available_tools: []            # no tools

available_tools:               # the same set as a normal conversation
  - $main

available_tools:               # the conversation's set plus a tool just for this run
  - $main
  - llm_conversation_get        # type: llm_conversation_get
```

To give a tool to one particular run only, set `supported_client_type: task` in that
tool's YAML and list it only in the `available_tools` of the tasks that use it (do not
put it in the `main` set). A `supported_client_type: task` tool would otherwise
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
