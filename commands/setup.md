---
description: Interactive setup for claude-session-export-obsidian plugin
user_invocable: true
---

# Session Export Setup

You MUST follow these steps in EXACT order. Do NOT skip the detection steps. Do NOT ask questions before completing detection. Use the helper scripts for all configuration — do NOT manually read/write settings files.

## Step 0: Detect ALL existing configuration FIRST

```bash
uv run ${CLAUDE_PLUGIN_ROOT}/skills/session-export/scripts/detect-config.py
```

Parse the JSON output. Do NOT make additional file reads. Only AFTER reading the output, present findings and start asking questions.

## Step 1: Vault Path(s)

Present what detection found for `vault_dir` (raw value, source, per-vault status).

If VAULT_DIR not set, ask: "Where is your Obsidian vault? (full path, comma-separated for multiple)"

If set, ask: "Do you want to keep this, or change it?"

Once the vault path is determined, ask where to store it. Explain each option:

"Where should VAULT_DIR be stored?"
1. **`.env` file** (in project root) — Best for manual CLI use (`cse export --today`). The script loads this automatically. Works from this directory only. Good if you mostly export from the project root.
2. **Claude Code user settings** (`~/.claude/settings.json`) — Available to all projects and hooks automatically. Best if you want hooks to work everywhere without a `.env` file in every project. This is the global default.
3. **Claude Code local project settings** (`.claude/settings.local.json`) — Per-project override. Useful if this project needs a different vault than your global default (e.g., client vault). Gitignored, so it won't affect teammates.
4. **Both .env and Claude settings** — Maximum compatibility. Hooks use Claude settings, manual CLI uses .env.

Wait for the user's choice, then execute:

For `.env`:
```bash
uv run ${CLAUDE_PLUGIN_ROOT}/skills/session-export/scripts/configure-vault.py set-env --vault-dir "<path>"
```

For Claude Code user settings (`~/.claude/settings.json`):
```bash
uv run ${CLAUDE_PLUGIN_ROOT}/skills/session-export/scripts/configure-vault.py set-settings --vault-dir "<path>" --settings-file ~/.claude/settings.json
```

For Claude Code local settings (`.claude/settings.local.json`):
```bash
uv run ${CLAUDE_PLUGIN_ROOT}/skills/session-export/scripts/configure-vault.py set-settings --vault-dir "<path>" --settings-file .claude/settings.local.json
```

For both: do .env first, then Claude settings.

For multi-vault use comma-separated paths in any of the above.

## Step 2: Hooks

Present what detection found for hooks (global/local).

If hooks already present, tell the user and ask if they want to change scope.

If no hooks found, ask: "Do you want auto-sync hooks? [yes/no]" → "Global or local?"

Determine the script path from detection output (`script_path.marketplace` or `script_path.plugin_root`). If neither found, construct from `${CLAUDE_PLUGIN_ROOT}`.

To add hooks:
```bash
# Global
uv run ${CLAUDE_PLUGIN_ROOT}/skills/session-export/scripts/configure-hooks.py add --settings-file ~/.claude/settings.json --script-path "<absolute-path-to-claude-session-export.py>"

# Local
uv run ${CLAUDE_PLUGIN_ROOT}/skills/session-export/scripts/configure-hooks.py add --settings-file .claude/settings.local.json --script-path "<absolute-path-to-claude-session-export.py>"
```

To remove existing hooks before changing scope:
```bash
uv run ${CLAUDE_PLUGIN_ROOT}/skills/session-export/scripts/configure-hooks.py remove --settings-file <path-to-old-settings-file>
```

NEVER manually edit settings JSON. NEVER use `${CLAUDE_PLUGIN_ROOT}` in the hook command path — use the resolved absolute path.

## Step 3: Project Map (per vault)

For each vault, present what detection found for its project map.

If map exists and is valid, show project count and ask if user wants to edit.

If map is missing or invalid, ask: "Set up project map for [vault name]? [yes/no]"

To create a template:
```bash
uv run ${CLAUDE_PLUGIN_ROOT}/skills/session-export/scripts/configure-vault.py init-map --vault-dir "<vault-path>"
```

To validate an existing map:
```bash
uv run ${CLAUDE_PLUGIN_ROOT}/skills/session-export/scripts/configure-vault.py validate-map --vault-dir "<vault-path>"
```

To see what directories need mapping:
```bash
uv run ${CLAUDE_PLUGIN_ROOT}/skills/session-export/scripts/configure-vault.py list-unmapped --vault-dir "<vault-path>"
```

If the user wants to edit, show the unmapped directories and help them update the YAML. Write the file using the Edit tool. The `patterns:` format is the recommended format for all entries. It supports three config options: `include_thinking` (default: false), `include_commands` (default: true), and `include_tool_context` (default: false).

## Step 4: Shell Alias

Ask: "Shell alias for CLI use outside Claude Code? [yes/no]"

If yes, determine the script path (same as step 2) and output:
```
alias cse="uv run <absolute-path-to-claude-session-export.py>"
```
Tell user to add to `~/.zshrc`.

## Step 5: Summary

ALWAYS end with a summary. Run check commands to get final state:

```bash
uv run ${CLAUDE_PLUGIN_ROOT}/skills/session-export/scripts/configure-hooks.py check --settings-file ~/.claude/settings.json
uv run ${CLAUDE_PLUGIN_ROOT}/skills/session-export/scripts/configure-vault.py check
```

Present:
```
Setup complete:
  Vault(s): <path(s)>
  Hooks: <global / local / none>
  Project map(s): <per-vault status>

To update, run /session-export:setup again or edit directly:
  Hooks:        ~/.claude/settings.json or .claude/settings.local.json
  Project maps: <vault>/project-map.yaml
  Vault dir:    VAULT_DIR in .env or shell profile
```
