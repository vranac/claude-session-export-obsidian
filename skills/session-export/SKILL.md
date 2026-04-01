---
description: Export Claude Code sessions to Obsidian markdown. Sync current session, batch export, or add notes.
---

# Session Export

Export Claude Code conversations to Obsidian-compatible markdown with full user/assistant conversation, project detection, and YAML frontmatter.

## Usage

When the user asks to sync or export sessions, run the appropriate command:

### Sync current session
```bash
uv run ${CLAUDE_PLUGIN_ROOT}/skills/session-export/scripts/claude-session-export.py sync --session-id ${CLAUDE_SESSION_ID}
```

### Export sessions
```bash
# Today's sessions
uv run ${CLAUDE_PLUGIN_ROOT}/skills/session-export/scripts/claude-session-export.py export --today

# All sessions
uv run ${CLAUDE_PLUGIN_ROOT}/skills/session-export/scripts/claude-session-export.py export --all

# By project
uv run ${CLAUDE_PLUGIN_ROOT}/skills/session-export/scripts/claude-session-export.py export --project PROJECT_NAME
```

### Add a note to current session
```bash
uv run ${CLAUDE_PLUGIN_ROOT}/skills/session-export/scripts/claude-session-export.py note "your note here" --session-id ${CLAUDE_SESSION_ID}
```

## Configuration

Requires `VAULT_DIR` environment variable pointing to the Obsidian vault. Run `/session-export:setup` for interactive configuration.
