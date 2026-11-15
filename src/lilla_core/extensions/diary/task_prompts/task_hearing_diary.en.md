It is now {{ current_hour }}. It is time to ask the user about today's diary.

1. Decide the diary date. Before the day rollover time (default 04:00) use the previous day; otherwise use that day's date.
2. Call llm_diary_draft_get to fetch that day's draft, and check what is already written and which <<name>> holes are still empty.
3. Write a short, gentle message that asks one or two questions about what happened that day and how the user felt, focusing on the holes that are still empty. You may refer to what is already written. If every hole is filled, briefly ask whether there is anything the user would like to add.

- Do not call llm_diary_draft_set in this task (the reply arrives in normal conversation, and the draft is updated then).
- Never guess moodscore. If you ask about it, ask the user.
- Output only the body of the message to send to the user.
