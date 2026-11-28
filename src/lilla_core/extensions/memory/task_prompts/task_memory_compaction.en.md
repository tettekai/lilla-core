This is the weekly organization of memory. Review memory/core.md in notes and move what no longer needs to be remembered in every conversation to the monthly archive under memory/journal/.

## Steps
1. Read memory/core.md with llm_notes_read.
2. Pick what no longer needs to be remembered in every conversation, such as finished plans, past deadlines and circumstances that were only temporary. Keep anything you are unsure about. Keep the important facts, policies and promises about the user.
3. If there is anything to move, read {{ journal_path }} with llm_notes_read (create it if it does not exist), and append what you move to the end (llm_notes_write with append). Put today's date (the date of {{ current_hour }}) in the heading.
4. Then read core.md again and overwrite it with llm_notes_write, with only the moved items removed. Do not reword or delete other memories.
5. Do not write anywhere other than memory/core.md and {{ journal_path }}.

## Reply
- If there was nothing to move, reply with NO_NOTIFICATION only.
- If you moved something, briefly report what you moved to {{ journal_path }}.
