# Claude Session Export Obsidian

Export Claude Code conversations to Obsidian-compatible markdown with full user/assistant conversation, automatic project detection, and clean YAML frontmatter.

## Installation

```bash
/plugin marketplace add vranac/claude-session-export-obsidian
```

Then run the interactive setup:

```
/session-export:setup
```

## Quick Start

1. Install the plugin (above)
2. Run `/session-export:setup` to configure your vault path, hooks, and project map
3. Sessions auto-sync on context compaction and session end
4. For batch export: `cse export --today`

## Commands

| Command | Description |
|---------|-------------|
| `/session-export:setup` | Interactive onboarding — vault path, hook scope, project map, shell alias |
| `/session-export:sync` | Sync current session to Obsidian markdown |
| `/session-export:export` | Batch export: `--today`, `--all`, `--project NAME`, `--memory`, or specific file |
| `/session-export:note` | Append timestamped note to current session |

## CLI Usage

For batch export outside of Claude Code, add a shell alias to `~/.zshrc`:

```bash
alias cse="uv run ~/.claude/plugins/marketplaces/claude-session-export-obsidian/skills/session-export/scripts/claude-session-export.py"
```

Then:

```bash
cse export --today              # export today's sessions + memories
cse export --all                # export all sessions + memories
cse export --project my-project   # export by project + memories
cse export --memory             # export memories only (no sessions)
cse export --memory --project my-project   # memories for one project
cse sync --session-id UUID      # sync a specific session + its memories
cse note "some note" --session-id UUID  # add a note
```

## Configuration

### VAULT_DIR (required)

Path to your Obsidian vault(s). The script resolves it in this order:

1. `VAULT_DIR` environment variable (shell profile, Claude Code settings, or parent process)
2. `.env` file in current working directory
3. Error with setup instructions

Set it in your shell profile:

```bash
# ~/.zshrc
export VAULT_DIR="$HOME/obsidian-vault"
```

Or create a `.env` file:

```
VAULT_DIR=/Users/you/obsidian-vault
```

For per-project overrides (e.g., client vault):

```json
// .claude/settings.local.json
{ "env": { "VAULT_DIR": "/path/to/client-vault" } }
```

#### Multi-vault

Comma-separated paths export to multiple vaults simultaneously. Directory paths cannot contain commas, so commas are a safe delimiter:

```bash
VAULT_DIR="/Users/you/obsidian-personal,/Users/you/obsidian-client"
```

Each vault has its own `project-map.yaml`, so the same session can have different project names or thinking settings per vault. JSONL files are parsed once and written to all vaults. Output is prefixed with the vault name:

```
[obsidian-personal] Synced: .../my-project/2026-03-27-1030-abc12345.md
[obsidian-client] Synced: .../my-project/2026-03-27-1030-abc12345.md
```

If a vault's config is missing or broken, it's skipped with a warning — other vaults continue.

### Project Map (required)

Create `$VAULT_DIR/project-map.yaml` to configure output and map encoded directory names to project names:

```yaml
output_dir: Claude-Sessions    # top-level folder name in vault (default: Claude-Sessions)

projects:
  my-project:
    - "-Users-you-dev-my-project"
    - "-Users-you-dev-my-project-*"
  another-project:
    - "-Users-you-dev-another-project-*"
```

Matching rules:
- Exact match first (highest priority)
- Glob match second (fnmatch)
- Longest/most-specific glob wins if multiple match
- No match = session skipped. Unmatched directories are reported at the end of export so you can add them to the map

To find your encoded directory names:

```bash
ls ~/.claude/projects/
```

#### Per-project config

Use dict format to add per-project options:

```yaml
projects:
  research-project:
    patterns:
      - "-Users-you-dev-research-*"
    include_thinking: true
```

Currently supported options:
- `include_thinking` (default: `false`) — include Claude's thinking blocks as collapsible `<details>` sections

## Output

Sessions and memories are exported to `$VAULT_DIR/{output_dir}/` organized by project:

```
Claude-Sessions/
├── my-project/
│   ├── memory/
│   │   ├── feedback_example.md
│   │   └── MEMORY.md
│   ├── 2026-03-15-1400-abc12345.md
│   └── 2026-03-16-0900-def67890.md
└── another-project/
    ├── memory/
    │   └── ...
    └── ...
```

Session files are named `{date}-{HHMM}-{session_id_prefix}.md`. Memory files are copied as-is from `~/.claude/projects/{encoded-dir}/memory/` — only newer files are copied on re-sync.

### Frontmatter

```yaml
---
type: claude-session
date: 2026-03-27
session_id: e296b4f9-a304-4774-adb2-5fe84a9f4914
title: "Exploring session export design"
summary: ""
project: my-project
source_dir: -Users-you-dev-my-project
git_branch: feature/xyz
last_activity: 2026-03-27T14:30:00Z
tags: []
related: []
---
```

| Field | Auto/Manual | Preserved on re-sync |
|-------|------------|---------------------|
| `type` | Auto | No |
| `date` | Auto | No |
| `session_id` | Auto | No |
| `title` | Auto | Yes |
| `summary` | Manual | Yes |
| `project` | Auto | Yes (if non-empty; auto-fills if empty) |
| `source_dir` | Auto | No |
| `git_branch` | Auto | No |
| `last_activity` | Auto | No |
| `tags` | Manual | Yes |
| `related` | Manual | Yes |

### Conversation

Clean user/assistant pairs. XML command messages are cleaned (`<command-name>/sync</command-name>` becomes `/sync`). Tool results, meta messages, and system records are filtered out.

The `## My Notes` section is preserved across re-syncs for your annotations.

## Hooks

Hooks are **not auto-registered**. You opt in via `/session-export:setup`, which adds them to your global or local settings.

When enabled, the plugin syncs on two events:

- **PreCompact** — before context compression, when conversation is at max richness
- **SessionEnd** — when the session terminates

No per-message syncing. The JSONL transcript is the live record; markdown export is for checkpoints and review.

### Manual hook configuration

If you prefer to add hooks manually instead of using `/session-export:setup`, add this to `~/.claude/settings.json` (global) or `.claude/settings.local.json` (project-local):

```json
{
  "hooks": {
    "PreCompact": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "uv run ~/.claude/plugins/marketplaces/claude-session-export-obsidian/skills/session-export/scripts/claude-session-export.py sync",
            "timeout": 15
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "uv run ~/.claude/plugins/marketplaces/claude-session-export-obsidian/skills/session-export/scripts/claude-session-export.py sync",
            "timeout": 15
          }
        ]
      }
    ]
  }
}
```

If you installed via `--plugin-dir` for local development, replace the path accordingly.

## Requirements

- [uv](https://docs.astral.sh/uv/) — Python package runner
- Python 3.11+
- Claude Code

No manual dependency installation needed. `uv` handles everything via PEP 723 inline metadata.
