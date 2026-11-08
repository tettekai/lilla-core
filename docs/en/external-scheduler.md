# Integrating an external scheduler

> This page is a translation of the [Japanese original](../ja/external-scheduler.md). If the two
> differ, the Japanese version is authoritative.

By default, scheduled tasks are started by the APScheduler inside the core. When you want to
hand time management to an external scheduler (an OS task scheduler, Hangfire, and so on),
for instance because the runtime's clock cannot be trusted, combine these two:

- Set `scheduler.enabled: false` in `lilla.yaml` to stop only the internal periodic start
- Have the external scheduler fetch the task definitions with `GET /api/tasks` and call
  `POST /api/runtask` when the time comes

Task times and enabled / disabled state live only on the lilla side (the tool YAML and the
[task overrides](extension-task-override.md) edited on the dashboard); the external side does
not manage them a second time.

## The switch for the whole internal scheduler (`scheduler.enabled`)

```yaml
scheduler:
  enabled: false   # defaults to true (the whole section may be omitted)
```

- Setting it to `false` does not start APScheduler. Task loading, the shared HTTP server,
  `GET /api/tasks`, `POST /api/runtask` and `!runtask` keep working
- It is read only at startup. A change takes effect after a restart
- Only booleans are accepted (a string such as `"false"`, or `0` / `1`, is a startup
  configuration error)
- It is separate from each task's `enabled` (the override). Even with the whole thing set to
  `false`, each task's `enabled` appears in `GET /api/tasks` as is

When the whole thing is `false`, saving a task override on the dashboard gives the apply
result `scheduler_disabled`. The save itself succeeded, and the new values are returned from
the next `GET /api/tasks`.

## `GET /api/tasks`

A route of the [shared HTTP server](http-server.md); it requires a Bearer token.

```console
$ curl -sS -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/tasks
```

```json
{
  "scheduler_enabled": false,
  "cron_format": "apscheduler3",
  "tasks": [
    {"name": "task_example", "schedule": "0 22 * * *", "timezone": "Asia/Tokyo", "enabled": true},
    {"name": "task_other", "schedule": "30 3 * * *", "timezone": "Asia/Tokyo", "enabled": false}
  ]
}
```

| Field | Meaning |
|------|------|
| `scheduler_enabled` | The value of the setting (`scheduler.enabled`). It is not a runtime state such as "not started yet because Discord is not connected". The external side can use it to avoid running at the same time as the internal scheduler |
| `cron_format` | The format of `schedule`. Currently always `apscheduler3` (see "What the cron means" below). Included so the external side can notice if the format changes in the future |
| `tasks[].name` | The tool name. Pass it as is as `tool` of `POST /api/runtask` |
| `tasks[].schedule` | A 5-field crontab. The effective value: the default with the extension's override layered on top |
| `tasks[].timezone` | The timezone in which `schedule` is interpreted (see "Timezone" below) |
| `tasks[].enabled` | Whether it starts periodically. A task stopped by an override stays in the list as `false`, so the external side can remove its registration |

How the targets and values are decided:

- The targets are all loaded **scheduled tasks** (task tools that have a `schedule`). This
  includes not only those bundled by extensions but also the core's built-ins and those a
  user placed in `${CONFIG_ROOT}/tools`. Tasks without a `schedule` are not included
- A task set to `enabled: false` in its tool YAML is not loaded, so it does not appear in the
  list. The same goes for a task whose YAML was deleted
- Overrides apply only to tasks bundled by an extension
  ([Overriding an extension's tasks](extension-task-override.md)). Overrides are read from
  MongoDB on every call, so the latest values are returned even when the internal scheduler
  is disabled or not started
- The effective values are decided by the same shared process as the internal scheduler and
  the dashboard. An override with an invalid type, or a `schedule` override that cannot be
  interpreted as a crontab, is ignored and the default is used
- A per-task `enabled: false` only stops the periodic start; it does not stop a manual run
  with `POST /api/runtask` (the same goes for the global `scheduler.enabled: false`)
- Only the fields above are returned; tool settings such as prompts, providers and
  destinations are not

Errors (neither is returned as an empty list):

| Status | Body | Meaning |
|------------|------|------|
| `401` | `{"error": "unauthorized"}` | The token is missing or wrong |
| `503` | `{"error": "overrides_unavailable"}` | The overrides could not be read from MongoDB. It does not fill in defaults and report success |
| `500` | `{"error": "invalid_schedule", "task": "<tool name>"}` | The default crontab of that task cannot be interpreted, so the effective schedule cannot be decided (a mistake in the tool YAML) |

On an error, the external side should keep running with the list it fetched last and not
delete jobs. A `200` with an empty `tasks` is the normal result "there are no scheduled
tasks at all".

## What the cron means

`schedule` is a 5-field string interpreted as is by APScheduler 3's
`CronTrigger.from_crontab()`, and does not necessarily mean the same as an ordinary cron.
Converting it to the form the external side uses is the external side's responsibility.

- The order is `minute hour day month day-of-week`
- **The day-of-week numbers start with 0 as Monday** (`0`=Mon … `6`=Sun). This is off by one
  day from an ordinary cron (where 0 is Sunday). English names such as `mon` / `sun` mean
  the same weekday as in an ordinary cron
- Specifying both day and day-of-week is an **AND** (days satisfying both). This differs
  from an ordinary cron's OR (`0 9 1 * mon` means "the 1st and a Monday")
- Range and interval notations such as `*/5` follow APScheduler's specification

## Timezone

`timezone` is decided by the same rules as [Timezone](timezone.md).

- If `ui.timezone` is set, that IANA name (such as `Asia/Tokyo`)
- If unset, the OS local timezone, but since it has no IANA name, the current UTC offset is
  returned in the form `+09:00` (an abbreviation such as `JST` is not returned). The
  internal scheduler interprets it with the same fixed offset. In regions with daylight
  saving time the value changes with the season, so setting `ui.timezone` is recommended
  when integrating with an external scheduler

## Assumptions for operating the external side

- At startup, and periodically afterwards (for example hourly), fetch the whole list again
  with `GET /api/tasks`, and remove from its registrations the tasks no longer in the list
  and those with `enabled: false`
- Running the internal scheduler (`scheduler.enabled: true`) and the external one at the same
  time starts the same task twice
- A `202` from `POST /api/runtask` means "accepted", not "completed". Resending when no
  response arrived can run it twice. Be careful with automatic retries on the external side
  (not resending is recommended)
- The assumption is that runs missed while stopped are not run retroactively (it resumes from
  the next scheduled time). The core has no run history or retry queue
