# Claude Session Export Obsidian Plugin

## What This Is

A Claude Code plugin that exports conversations from JSONL transcripts to Obsidian-compatible markdown. One skill (`session-export`), four commands (`setup`, `sync`, `export`, `note`), no auto-registered hooks.

## Architecture

- **Plugin root**: `.claude-plugin/plugin.json` + `marketplace.json`
- **Main script**: `skills/session-export/scripts/claude-session-export.py` — Python via `uv run`, PEP 723 inline deps
- **Detection script**: `skills/session-export/scripts/detect-config.py` — outputs JSON with current config state, used by setup
- **Commands**: `commands/*.md` — slash command definitions
- **Skill**: `skills/session-export/SKILL.md` — auto-invoked by Claude

## Key Design Decisions

- **No auto-registered hooks**. Hooks are opt-in via `/session-export:setup` or manual config. The plugin ships without `hooks/hooks.json`.
- **Project detection** uses encoded directory names from `~/.claude/projects/` matched against `$VAULT_DIR/project-map.yaml` with exact match + glob support.
- **Unmatched sessions are skipped**, not exported to `_unmatched/`. Unmatched directories are reported at the end of export.
- **Sessions organized by project** subdirectories under `$VAULT_DIR/{output_dir}/`.
- **Memories** (from `~/.claude/projects/{dir}/memory/`) are copied alongside sessions into `{project}/memory/`.
- **VAULT_DIR** resolution: env var → `.env` in CWD → error. Required. Supports comma-separated paths for multi-vault (parse JSONL once, write to all vaults).
- **project-map.yaml** is required. Script errors without it.
- **Iterator-based JSONL parsing** — records are processed one at a time, not loaded into memory.
- **SessionIndex** built once per command to avoid repeated directory scans.

## Frontmatter Schema

```yaml
type: claude-session
date: YYYY-MM-DD
session_id: UUID
title: "..."
summary: ""           # manual, preserved
project: name         # auto, preserved if non-empty
source_dir: encoded   # auto
git_branch: branch    # auto
last_activity: ISO    # auto
tags: []              # manual, preserved
related: []           # manual, preserved
```

## Preserved Fields on Re-sync

`title`, `summary`, `project` (if non-empty), `tags`, `related`, `## My Notes` section.

## Conversation Filtering

- **User messages**: `type == "user"`, not `isMeta`, not tool result, content is string. XML cleaned to `/command` format.
- **Assistant text**: `type == "assistant"`, text blocks only. Thinking blocks configurable per-project.
- **Skipped**: tool results, meta, system, progress, file-history-snapshot.

## Config File Format

`$VAULT_DIR/project-map.yaml`:
```yaml
output_dir: Claude-Sessions    # configurable top-level folder name

projects:
  project-name:
    patterns:
      - "-encoded-dir-exact"
      - "-encoded-dir-glob-*"
  project-with-config:
    patterns:
      - "-encoded-dir-*"
    include_thinking: true
    include_commands: false
    include_tool_context: true
```
