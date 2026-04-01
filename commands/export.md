---
description: Batch export Claude Code sessions to Obsidian markdown
user_invocable: true
---

Batch export Claude Code sessions. Pass the user's arguments to the script.

Examples:
```bash
# Export today's sessions
uv run ${CLAUDE_PLUGIN_ROOT}/skills/session-export/scripts/claude-session-export.py export --today

# Export all sessions
uv run ${CLAUDE_PLUGIN_ROOT}/skills/session-export/scripts/claude-session-export.py export --all

# Export by project
uv run ${CLAUDE_PLUGIN_ROOT}/skills/session-export/scripts/claude-session-export.py export --project PROJECT_NAME

# Export specific file
uv run ${CLAUDE_PLUGIN_ROOT}/skills/session-export/scripts/claude-session-export.py export /path/to/file.jsonl
```

Report the result to the user.
