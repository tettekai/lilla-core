# Timezone

> This page is a translation of the [Japanese original](../ja/timezone.md). If the two
> differ, the Japanese version is authoritative.

`ui.timezone` in `lilla.yaml` decides the clock the bot treats as "now" and "today"
for humans. The crontab expressions of scheduled tasks, the current time embedded in
the system prompt, the conversation-history window, and relative date ranges such as
`today` all follow it.

```yaml
ui:
  timezone: Asia/Tokyo
```

- Omitted (or YAML `null`): the OS local timezone of the process.
- An IANA name such as `Asia/Tokyo`: that timezone only. The OS timezone is ignored.
- An empty string, or a name `zoneinfo` does not accept: startup fails. There is no
  fallback, so a typo cannot silently shift every date by a day.

In `utils/datetime_utils.py`, `local_timezone()`, `local_now()`, `to_jst_date()` and
`jst_day_end_utc()` all resolve to that same timezone on every call, so the wall clock
and the calendar date never disagree. The `JST` constant is the one exception: it stays
at UTC+9 no matter what `ui.timezone` says, for code that needs Japan time explicitly.

> **Note:** container images usually run with their OS timezone set to UTC. If you
> leave `ui.timezone` unset there, cron schedules and "today" are UTC as well. Set it
> explicitly whenever the dates matter.
