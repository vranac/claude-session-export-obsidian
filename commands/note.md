---
description: Add a timestamped note to the current session's My Notes section
user_invocable: true
---

Add a timestamped note to the current session's exported markdown file.

Run with the user's note text:
```bash
uv run ${CLAUDE_PLUGIN_ROOT}/skills/session-export/scripts/claude-session-export.py note "USER_NOTE_TEXT" --session-id ${CLAUDE_SESSION_ID}
```

Replace `USER_NOTE_TEXT` with the actual note the user wants to add.

Report the result to the user.
