# Overriding an extension's tasks

> This page is a translation of the [Japanese original](../ja/extension-task-override.md). If the two
> differ, the Japanese version is authoritative.

For the scheduled tasks an extension bundles (the `task_*.yaml` shipped through
`tool_config_roots()`), you can override the run schedule (crontab) and enabled / disabled
state with a single MongoDB document per extension, without editing the extension's code or
the tool YAML. Overrides are written in the per-extension document of the same
`extension_overrides` collection used by
[Extension default prompts and overrides](extension-prompt.md).

## The override document

Add a top-level key `tasks` to the per-extension document. Its keys are the tasks' tool
names (the YAML file-name stem, the same as the scheduler's job ID).

```json
{
  "extension": "diary",
  "prompt": "(optional. A prompt override)",
  "tasks": {
    "task_diary_digest": { "schedule": "30 21 * * *" },
    "task_diary_cleanup": { "enabled": false }
  },
  "updated_at": { "$date": "2026-10-28T00:00:00Z" }
}
```

```javascript
db.extension_overrides.updateOne(
  { extension: "diary" },
  { $set: { "tasks.task_diary_digest.schedule": "30 21 * * *", updated_at: new Date() } },
  { upsert: true },
)
```

You normally edit it on the observability dashboard's **Extensions** page (admin menu,
`#/admin/extensions`; [The observability dashboard](dashboard.md#extensions)). You can also
set it directly with `mongosh` and the like as above, but then restart, or use "Re-apply" on
the screen, for it to take effect.

## What can be overridden

| Field | Type | Meaning |
|------|----|------|
| `schedule` | string (crontab) | The run schedule. Interpreted, as before, in `ui.timezone` (the OS local timezone if unset) |
| `enabled` | boolean | If `false`, the job is not registered with the scheduler |

- Overrides apply per field. Fields you do not write use the defaults (the tool's
  `schedule`, enabled).
- A document without a `tasks` key, or an extension with no document, uses the defaults for
  every task.
- Whether `tasks` exists or what it contains has no effect on the `prompt` override (and vice
  versa).
- The task's content and the task's prompt cannot be overridden. Write those in the tool YAML
  or the task implementation.

## Which tasks are eligible

- Only tasks the extension bundled through `tool_config_roots()` can be overridden.
- If a user placed a YAML with the same stem in `${CONFIG_ROOT}/tools` to replace the
  bundled one, that task is still treated as the extension's. The replacement YAML's content
  is taken as the default, and the override is layered on top.
- Tasks that do not belong to an extension (core built-in tasks, or tasks from YAML only the
  user placed) are out of scope. Their schedule is decided by the user's YAML.

## What `enabled` means

`enabled: false` in the tool YAML and `enabled: false` in an override mean different things.

| Where it is written | Meaning | Manual run with `!runtask` |
|----------|------|------|
| `enabled: false` in the tool YAML | The tool is **not loaded** | Not possible |
| `enabled: false` in an override | The tool stays loaded, but **no periodic job is registered** | Possible |

A task that is not loaded because its YAML says `enabled: false` is not enabled even by an
override with `enabled: true` (a WARNING is logged and it is ignored).

## Handling invalid values

In the following cases, a WARNING including the extension name and tool name is logged, and
that task (or that field) falls back to the default. Startup is not stopped.

- `tasks` is not an object
- A per-task value is not an object
- `schedule` is not a string, or cannot be interpreted as a crontab
- `enabled` is not a boolean
- The tool name written is one the extension did not bundle (or is not loaded, or has no
  schedule)

If loading from MongoDB itself fails, a WARNING is logged and every task is registered with
its defaults.

## When it is applied

Overrides are read at startup (when the scheduler is started upon the Discord connection
completing). They are not cached.

When you save on the dashboard's Extensions page, the change is applied to the running
scheduler after saving, without a restart (`lilla_core.handlers.task_handler.apply_task_overrides(extension_name,
strict=True)`). It does not watch for changes or poll, so after rewriting directly with
`mongosh` and the like, restart or press "Re-apply" on the screen.

- The screen shows "saved" and "applied" separately. If saving succeeded but applying
  failed, it does not report success, and "Re-apply" retries (the saved values remain as
  they are).
- If the scheduler is not started yet (before the Discord connection), only saving is done
  and it is applied at startup.
- When the internal scheduler is not used because of `scheduler.enabled: false`, only saving
  is done and the apply result is `scheduler_disabled`. The saved values are returned from
  the next `GET /api/tasks` ([Integrating an external scheduler](external-scheduler.md)).
- If saving fails, nothing is applied either.
- Registration at startup goes through the same apply process.
- When applying, a changed schedule is swapped in, `enabled: false` removes the job, and if
  there is no job but the task is enabled, it is registered again with the default job
  function. Deleting an override and applying returns to the default.
- Running jobs are not stopped. The swap takes effect from the next run.
- If loading fails during applying, an apply from the screen (`strict=True`) returns the
  failure without changing any job. A call without `strict` returns that extension's tasks to
  their defaults, as at startup.

Whether an override took effect can be checked in the INFO log `[TASK] Next run of
<tool name>: <next run time>` output at startup and when applying.

## Notes

- **The tool name and the extension's `name` are the override's identifiers.** If the
  extension changes the tool name (the YAML stem) or `Extension.name`, existing overrides
  stop applying.
- Fields you have not overridden follow the extension's defaults.

## Related pages

- [Extension basics](extensions.md)
- [Extension default prompts and overrides](extension-prompt.md)
- [Tool contracts](tools.md)
