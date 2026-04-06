#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pyyaml",
#     "python-dotenv",
# ]
# ///
"""Export Claude Code sessions to Obsidian markdown.

Usage:
    claude-session-export sync [--session-id ID] [--transcript PATH] [-q]
    claude-session-export export (--today | --all | --project NAME | --memory | FILE) [-q]
    claude-session-export note TEXT --session-id ID [-q]

Supports multiple vaults via comma-separated VAULT_DIR:
    VAULT_DIR="/path/vault1,/path/vault2"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import typing
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from fnmatch import fnmatch
from pathlib import Path

import yaml
from dotenv import load_dotenv

# =============================================================================
# Constants
# =============================================================================

PROJECTS_DIR = Path.home() / ".claude" / "projects"
DEFAULT_OUTPUT_DIR = "Claude-Sessions"
PRESERVED_SECTION = "## My Notes"
AGENT_PREFIX = "agent-"
GIT_BRANCH_DEFAULT = "HEAD"
CUSTOM_TITLE_TYPE = "custom-title"
SUMMARY_TYPE = "summary"
CONFIG_FILENAME = "project-map.yaml"
COMMAND_PREFIX = "/"
MY_NOTES_COMMENT = "<!-- Add your notes here. This section is preserved across syncs. -->"


# =============================================================================
# Exceptions
# =============================================================================

class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


# =============================================================================
# Data Types
# =============================================================================

@dataclass
class ConversationEntry:
    role: str  # "user" or "assistant"
    content: str
    thinking: str = ""
    label: str = ""  # "rejected", "approved", "while_processing", or "" for normal
    tool_context: str = ""
    tool_summary: str = ""  # e.g. "Edit path/to/file.py" or "Bash"
    timestamp: str = ""  # ISO 8601 from JSONL record


@dataclass
class SessionData:
    session_id: str = ""
    date: str = ""
    title: str = ""
    git_branch: str = ""
    encoded_dir: str = ""
    first_timestamp: str = ""
    last_timestamp: str = ""
    conversation: list[ConversationEntry] = field(default_factory=list)
    tool_use_index: dict[str, dict[str, typing.Any]] = field(default_factory=dict)
    skipped_lines: int = 0


@dataclass
class VaultContext:
    """Holds per-vault resolved configuration."""
    vault_dir: Path
    config: dict[str, typing.Any]
    name: str  # short name for prefixed output


# =============================================================================
# Configuration
# =============================================================================

def resolve_vault_dirs() -> list[Path]:
    """Resolve VAULT_DIR from environment or .env file.

    Supports comma-separated paths for multi-vault:
        VAULT_DIR="/path/vault1,/path/vault2"

    Resolution order:
    1. Environment variable (shell, Claude Code settings, parent process)
    2. .env file in CWD
    3. Error with instructions

    Returns list of valid vault Paths. Raises ConfigError only if NO valid
    vaults are found.
    """
    vault = os.environ.get("VAULT_DIR")
    if not vault:
        load_dotenv()
        vault = os.environ.get("VAULT_DIR")

    if not vault:
        raise ConfigError(
            "VAULT_DIR not set.\n"
            "Create a .env file with VAULT_DIR=/path/to/vault\n"
            "or set it in your shell profile. See README."
        )

    raw_paths = [p.strip() for p in vault.split(",") if p.strip()]
    if not raw_paths:
        raise ConfigError(
            "VAULT_DIR is empty after parsing.\n"
            "Create a .env file with VAULT_DIR=/path/to/vault\n"
            "or set it in your shell profile. See README."
        )

    valid: list[Path] = []
    for raw in raw_paths:
        vault_path = Path(raw)
        if not vault_path.exists():
            print(f"Warning: VAULT_DIR path does not exist, skipping: {vault_path}", file=sys.stderr)
            continue
        if not vault_path.is_dir():
            print(f"Warning: VAULT_DIR is not a directory, skipping: {vault_path}", file=sys.stderr)
            continue
        valid.append(vault_path)

    if not valid:
        raise ConfigError(
            "No valid VAULT_DIR paths found.\n"
            f"Checked: {', '.join(raw_paths)}\n"
            "Check your VAULT_DIR setting."
        )

    return valid


def load_config(vault_dir: Path) -> dict[str, typing.Any]:
    """Load full config from project-map.yaml.

    Exits with error if map file doesn't exist — project map is required.
    """
    map_path = vault_dir / CONFIG_FILENAME
    if not map_path.exists():
        raise ConfigError(
            f"Project map not found at {map_path}\n"
            "Create a project-map.yaml in your vault. See README.\n"
            "Or run /session-export:setup to configure."
        )
    try:
        with open(map_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in {map_path}: {e}") from e

    if not isinstance(data, dict):
        raise ConfigError(
            f"Invalid project map at {map_path}\n"
            "Expected a YAML mapping at the top level."
        )
    if "projects" not in data or not isinstance(data.get("projects"), dict):
        raise ConfigError(
            f"Invalid project map at {map_path}\n"
            "Expected format:\n"
            "  projects:\n"
            '    my-project:\n'
            '      - "-Users-you-dev-my-project"'
        )
    return data


def load_vault_contexts(vault_dirs: list[Path]) -> list[VaultContext]:
    """Load config for each vault. Warns and skips vaults with broken config.

    Raises ConfigError only if NO vaults have valid config.
    """
    contexts: list[VaultContext] = []
    for vault_dir in vault_dirs:
        try:
            config = load_config(vault_dir)
            contexts.append(VaultContext(
                vault_dir=vault_dir,
                config=config,
                name=vault_dir.name,
            ))
        except ConfigError as e:
            print(f"Warning: Skipping vault {vault_dir.name}: {e}", file=sys.stderr)

    if not contexts:
        raise ConfigError(
            "No vaults have valid configuration.\n"
            "Ensure at least one vault has a valid project-map.yaml."
        )

    return contexts


def get_output_dir_name(config: dict[str, typing.Any]) -> str:
    """Get the output directory name from config. Defaults to 'Claude-Sessions'."""
    return str(config.get("output_dir", DEFAULT_OUTPUT_DIR))


def get_project_map(config: dict[str, typing.Any]) -> dict[str, list[str]]:
    """Extract project map from config. Returns {project_name: [patterns]}."""
    projects = config.get("projects", {})
    if not isinstance(projects, dict):
        return {}
    result: dict[str, list[str]] = {}
    for project_name, value in projects.items():
        if isinstance(value, list):
            result[str(project_name)] = [p for p in value if isinstance(p, str)]
        elif isinstance(value, dict):
            patterns = value.get("patterns", [])
            if isinstance(patterns, list):
                result[str(project_name)] = [p for p in patterns if isinstance(p, str)]
    return result


def get_project_config(config: dict[str, typing.Any], project_name: str) -> dict[str, typing.Any]:
    """Get per-project config (e.g., include_thinking).

    Only returns config for dict-format entries. List-format entries have no config.
    """
    projects = config.get("projects", {})
    if not isinstance(projects, dict):
        return {}
    value = projects.get(project_name, {})
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if k != "patterns"}
    return {}


def resolve_project(encoded_dir: str, project_map: dict[str, list[str]]) -> str:
    """Resolve encoded directory name to project name via map."""
    if not encoded_dir:
        return ""

    # Exact match first
    for project_name, patterns in project_map.items():
        for pattern in patterns:
            if pattern == encoded_dir:
                return project_name

    # Glob match — longest pattern wins
    matches: list[tuple[int, str]] = []
    for project_name, patterns in project_map.items():
        for pattern in patterns:
            if fnmatch(encoded_dir, pattern):
                matches.append((len(pattern), project_name))

    if matches:
        matches.sort(reverse=True)
        return matches[0][1]

    return ""


# =============================================================================
# Output helpers
# =============================================================================

def _make_printer(
    vault_contexts: list[VaultContext],
    quiet: bool = False,
) -> typing.Callable[[str, VaultContext | None], None]:
    """Return a print function that prefixes with vault name when multi-vault.

    When a single vault is active, output has no prefix (current behavior).
    When multiple vaults, output is prefixed: [vault-name] message
    """
    multi = len(vault_contexts) > 1

    def _print(msg: str, ctx: VaultContext | None = None) -> None:
        if quiet:
            return
        if multi and ctx is not None:
            print(f"[{ctx.name}] {msg}")
        else:
            print(msg)

    return _print


# =============================================================================
# JSONL Parsing (iterator-based)
# =============================================================================

def iter_jsonl(file_path: Path) -> typing.Iterator[dict[str, typing.Any]]:
    """Iterate over JSONL records one at a time. Yields parsed dicts.

    Warns on stderr about skipped malformed lines.
    """
    skipped = 0
    with open(file_path, encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
    if skipped:
        print(f"Warning: Skipped {skipped} malformed JSONL lines in {file_path.name}", file=sys.stderr)


def is_real_user_message(record: dict[str, typing.Any]) -> bool:
    """Check if a record is a real user message (not meta, not tool result)."""
    if record.get("type") != "user":
        return False
    if record.get("isMeta"):
        return False
    if record.get("toolUseResult"):
        return False
    if record.get("sourceToolAssistantUUID"):
        return False
    msg = record.get("message", {})
    content = msg.get("content", "")
    if not isinstance(content, str):
        return False
    return bool(content)


def clean_user_message(content: str) -> str:
    """Clean XML tags from user messages, prefix commands with slash."""
    if not isinstance(content, str):
        return ""

    # Skip local command output and caveats entirely
    if re.search(r"<local-command-stdout>|<local-command-caveat>", content):
        return ""

    # Skip system reminders
    if re.search(r"<system-reminder>", content):
        return ""

    # Extract command name and args from XML
    cmd_match = re.search(r"<command-name>/?([^<]+)</command-name>", content)
    args_match = re.search(r"<command-args>([^<]*)</command-args>", content)

    if cmd_match:
        cmd = cmd_match.group(1).strip()
        args = args_match.group(1).strip() if args_match else ""
        return f"/{cmd} {args}".strip() if args else f"/{cmd}"

    # Skip command messages (skill invocations with XML payload)
    if re.search(r"<command-message>", content):
        return ""

    # Strip any remaining XML tags
    cleaned = re.sub(r"<[^>]+>", "", content).strip()

    # Skip empty or caveat-only messages
    if not cleaned or cleaned.startswith("Caveat:"):
        return ""

    return cleaned


def extract_assistant_text(record: dict[str, typing.Any]) -> str:
    """Extract text content from an assistant record."""
    msg = record.get("message", {})
    contents = msg.get("content", [])
    if not isinstance(contents, list):
        return ""

    texts = []
    for item in contents:
        if isinstance(item, dict) and item.get("type") == "text":
            text = item.get("text", "")
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())

    return "\n\n".join(texts)


def extract_thinking(record: dict[str, typing.Any]) -> str:
    """Extract thinking content from an assistant record."""
    msg = record.get("message", {})
    contents = msg.get("content", [])
    if not isinstance(contents, list):
        return ""

    thoughts = []
    for item in contents:
        if isinstance(item, dict) and item.get("type") == "thinking":
            text = item.get("thinking", "")
            if isinstance(text, str) and text.strip():
                thoughts.append(text.strip())

    return "\n\n".join(thoughts)


def extract_rejection_comment(record: dict[str, typing.Any]) -> tuple[str, str]:
    """Extract user comment from a tool rejection record.

    When a user rejects a tool use with a comment, the JSONL stores it as a
    ``type == "user"`` record where ``message.content`` is a list containing a
    ``tool_result`` dict with ``is_error: True``. The actual comment is embedded
    in the text between ``"the user said:\\n"`` and ``"\\n\\nNote:"``.

    Returns (comment_text, tool_use_id). Both empty strings if no match.
    """
    if record.get("type") != "user":
        return "", ""
    msg = record.get("message", {})
    content = msg.get("content")
    if not isinstance(content, list):
        return "", ""

    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "tool_result":
            continue
        if not item.get("is_error"):
            continue
        tool_use_id = item.get("tool_use_id", "")
        text = item.get("content", "")
        if not isinstance(text, str):
            continue
        marker = "the user said:\n"
        idx = text.find(marker)
        if idx == -1:
            continue
        after = text[idx + len(marker):]
        end_marker = "\n\nNote:"
        end_idx = after.find(end_marker)
        comment = after[:end_idx] if end_idx != -1 else after
        comment = comment.strip()
        if comment:
            return comment, tool_use_id
    return "", ""


def extract_approval_comment(record: dict[str, typing.Any]) -> tuple[str, str]:
    """Extract user comment from a tool approval record.

    When a user approves a tool use with an added comment, the JSONL stores it
    as a ``type == "user"`` record where ``message.content`` is a list
    containing both a ``tool_result`` item and a separate ``text`` item with the
    user's comment.

    Only triggers when a ``tool_result`` is present in the same content array
    (to avoid matching regular multi-part user messages).

    Returns (comment_text, tool_use_id). Both empty strings if no match.
    """
    if record.get("type") != "user":
        return "", ""
    msg = record.get("message", {})
    content = msg.get("content")
    if not isinstance(content, list):
        return "", ""

    tool_use_id = ""
    has_tool_result = False
    texts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "tool_result":
            has_tool_result = True
            tool_use_id = item.get("tool_use_id", "")
        elif item.get("type") == "text":
            text = item.get("text", "")
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())

    if not has_tool_result or not texts:
        return "", ""

    return "\n\n".join(texts), tool_use_id


def parse_iso_date(timestamp: str) -> str:
    """Extract YYYY-MM-DD from an ISO timestamp. Returns empty string on failure."""
    if not isinstance(timestamp, str) or "T" not in timestamp:
        return ""
    return timestamp.split("T")[0]


def format_timestamp_utc(timestamp: str) -> str:
    """Format an ISO timestamp as 'YYYY-MM-DD HH:MM UTC'. Returns empty string on failure."""
    if not isinstance(timestamp, str) or not timestamp:
        return ""
    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, TypeError):
        return ""


def _index_tool_uses(record: dict[str, typing.Any], index: dict[str, dict[str, typing.Any]]) -> None:
    """Index tool_use blocks from an assistant record for later correlation."""
    msg = record.get("message", {})
    contents = msg.get("content", [])
    if not isinstance(contents, list):
        return

    for item in contents:
        if not isinstance(item, dict) or item.get("type") != "tool_use":
            continue
        tool_use_id = item.get("id", "")
        if not tool_use_id:
            continue
        tool_name = item.get("name", "")
        tool_input = item.get("input", {})
        if not isinstance(tool_input, dict):
            tool_input = {}

        if tool_name in ("Edit", "Write", "Read"):
            summary = f"{tool_name} {tool_input.get('file_path', '')}"
        elif tool_name == "Bash":
            summary = tool_name
        else:
            summary = tool_name

        index[tool_use_id] = {"summary": summary, "input": tool_input, "tool_name": tool_name}


def _format_tool_context(tool_use_id: str, tool_use_index: dict[str, dict[str, typing.Any]]) -> str:
    """Format tool context string from the tool_use_index for a given tool_use_id."""
    if not tool_use_id or tool_use_id not in tool_use_index:
        return ""

    entry = tool_use_index[tool_use_id]
    tool_name = entry.get("tool_name", "")
    tool_input = entry.get("input", {})

    def _indent_for_callout(text: str) -> str:
        """Prefix every line with '> ' for Obsidian callout blocks."""
        return "\n".join(f"> {line}" for line in text.split("\n"))

    if tool_name == "Edit":
        file_path = tool_input.get("file_path", "")
        old_string = tool_input.get("old_string", "")
        new_string = tool_input.get("new_string", "")
        parts = [f"**File:** `{file_path}`", ""]
        if old_string:
            parts.extend(["**Old:**", "```", old_string, "```", ""])
        if new_string:
            parts.extend(["**New:**", "```", new_string, "```"])
        return _indent_for_callout("\n".join(parts))

    if tool_name == "Write":
        file_path = tool_input.get("file_path", "")
        content = tool_input.get("content", "")
        raw_lines = content.split("\n")
        truncated = "\n".join(raw_lines[:50])
        suffix = f"\n... ({len(raw_lines) - 50} more lines)" if len(raw_lines) > 50 else ""
        return _indent_for_callout(f"**File:** `{file_path}`\n\n```\n{truncated}{suffix}\n```")

    if tool_name == "Bash":
        command = tool_input.get("command", "")
        return _indent_for_callout(f"```bash\n{command}\n```")

    # Other tools: show name and input as JSON
    try:
        input_json = json.dumps(tool_input, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        input_json = str(tool_input)
    return _indent_for_callout(f"**{tool_name}**\n\n```json\n{input_json}\n```")


def extract_session_data(
    records: typing.Iterator[dict[str, typing.Any]],
) -> SessionData:
    """Extract all session data from an iterator of JSONL records.

    Always extracts thinking blocks and commands — the decision to include
    them in output is made at markdown generation time, not parse time.
    """
    data = SessionData()

    for record in records:
        record_type = record.get("type")

        # Session ID
        session_id = record.get("sessionId")
        if isinstance(session_id, str) and session_id and not data.session_id:
            data.session_id = session_id

        # Git branch
        git_branch = record.get("gitBranch")
        if isinstance(git_branch, str) and git_branch and git_branch != GIT_BRANCH_DEFAULT and not data.git_branch:
            data.git_branch = git_branch

        # Timestamps
        timestamp = record.get("timestamp", "")
        if isinstance(timestamp, str) and timestamp:
            if not data.first_timestamp:
                data.first_timestamp = timestamp
            data.last_timestamp = timestamp

        # Date from first real user message
        if record_type == "user" and timestamp and not data.date:
            if is_real_user_message(record):
                data.date = parse_iso_date(timestamp)

        # Custom title
        if record_type == CUSTOM_TITLE_TYPE:
            custom_title = record.get("customTitle", "")
            if isinstance(custom_title, str) and custom_title:
                data.title = custom_title.split("\n")[0].strip()[:100]

        # Queue operations — messages typed while Claude was processing
        if record_type == "queue-operation":
            if record.get("operation") == "enqueue":
                queued_content = record.get("content", "")
                if isinstance(queued_content, str) and queued_content.strip():
                    cleaned = clean_user_message(queued_content)
                    if cleaned:
                        data.conversation.append(ConversationEntry(role="user", content=cleaned, label="while_processing", timestamp=timestamp))

        # Index tool_use blocks from assistant records
        if record_type == "assistant":
            _index_tool_uses(record, data.tool_use_index)

        # User messages
        if is_real_user_message(record):
            msg = record.get("message", {})
            content = msg.get("content", "")
            cleaned = clean_user_message(content)
            if cleaned:
                data.conversation.append(ConversationEntry(role="user", content=cleaned, timestamp=timestamp))
        elif record_type == "user":
            # Check for rejection comments on tool results
            rejection, reject_tool_id = extract_rejection_comment(record)
            if rejection:
                tool_context = _format_tool_context(reject_tool_id, data.tool_use_index)
                tool_summary = data.tool_use_index.get(reject_tool_id, {}).get("summary", "")
                data.conversation.append(ConversationEntry(
                    role="user", content=rejection, label="rejected",
                    tool_context=tool_context, tool_summary=tool_summary,
                    timestamp=timestamp,
                ))
            else:
                # Check for approval comments alongside tool results
                approval, approve_tool_id = extract_approval_comment(record)
                if approval:
                    tool_context = _format_tool_context(approve_tool_id, data.tool_use_index)
                    tool_summary = data.tool_use_index.get(approve_tool_id, {}).get("summary", "")
                    data.conversation.append(ConversationEntry(
                        role="user", content=approval, label="approved",
                        tool_context=tool_context, tool_summary=tool_summary,
                        timestamp=timestamp,
                    ))

        # Assistant messages — always extract thinking
        if record_type == "assistant":
            text = extract_assistant_text(record)
            thinking = extract_thinking(record)

            if text or thinking:
                data.conversation.append(ConversationEntry(
                    role="assistant", content=text, thinking=thinking,
                    timestamp=timestamp,
                ))

    # Title fallback
    if not data.title:
        for entry in data.conversation:
            if entry.role == "user":
                data.title = entry.content.replace("\n", " ").strip()[:80]
                break

    if not data.title:
        fallback_date = data.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        sid = (data.session_id or "unknown")[:8]
        data.title = f"{fallback_date} {sid}"

    # Date fallback
    if not data.date:
        data.date = parse_iso_date(data.first_timestamp) or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    return data


# =============================================================================
# Markdown Generation
# =============================================================================

def parse_frontmatter(content: str) -> dict[str, typing.Any]:
    """Parse YAML frontmatter from markdown content."""
    if not content.startswith("---"):
        return {}
    end = content.find("---", 3)
    if end == -1:
        return {}
    try:
        result = yaml.safe_load(content[3:end])
        return result if isinstance(result, dict) else {}
    except yaml.YAMLError:
        return {}


def extract_my_notes(content: str) -> str | None:
    """Extract the ## My Notes section from markdown."""
    idx = content.find(PRESERVED_SECTION)
    if idx == -1:
        return None

    next_heading = content.find("\n## ", idx + len(PRESERVED_SECTION))
    if next_heading == -1:
        section = content[idx:]
    else:
        section = content[idx:next_heading]

    return section.rstrip()


def generate_frontmatter(
    data: SessionData,
    session_id: str,
    project: str,
    existing_fm: dict[str, typing.Any] | None = None,
) -> list[str]:
    """Generate YAML frontmatter lines."""
    lines = ["---"]
    lines.append("type: claude-session")
    lines.append(f"date: {data.date}")
    lines.append(f"session_id: {session_id}")

    # Title — preserve if manually set and clean (no XML remnants)
    title = existing_fm.get("title") if existing_fm else None
    if isinstance(title, str) and re.search(r"<[^>]+>", title):
        title = None
    if not title:
        title = data.title or "Untitled Session"
    title_escaped = title.replace('"', '\\"')
    lines.append(f'title: "{title_escaped}"')

    # Summary — manual only, preserve
    summary = existing_fm.get("summary", "") if existing_fm else ""
    if summary:
        summary_escaped = str(summary).replace('"', '\\"')
        lines.append(f'summary: "{summary_escaped}"')
    else:
        lines.append('summary: ""')

    # Project — preserve if non-empty, auto-populate if empty
    existing_project = existing_fm.get("project", "") if existing_fm else ""
    final_project = existing_project or project
    if final_project:
        project_escaped = str(final_project).replace('"', '\\"')
        lines.append(f'project: "{project_escaped}"')
    else:
        lines.append('project: ""')

    # Source dir
    if data.encoded_dir:
        source_escaped = data.encoded_dir.replace('"', '\\"')
        lines.append(f'source_dir: "{source_escaped}"')
    else:
        lines.append('source_dir: ""')

    # Git branch
    if data.git_branch:
        branch_escaped = data.git_branch.replace('"', '\\"')
        lines.append(f'git_branch: "{branch_escaped}"')
    else:
        lines.append('git_branch: ""')

    # Last activity
    last_activity = data.last_timestamp or datetime.now(timezone.utc).isoformat()
    lines.append(f'last_activity: "{last_activity}"')

    # Tags — manual, preserve
    tags = existing_fm.get("tags", []) if existing_fm else []
    if isinstance(tags, list) and tags:
        lines.append("tags:")
        for tag in tags:
            tag_escaped = str(tag).replace('"', '\\"')
            lines.append(f'  - "{tag_escaped}"')
    else:
        lines.append("tags: []")

    # Related — manual, preserve
    related = existing_fm.get("related", []) if existing_fm else []
    if isinstance(related, list) and related:
        lines.append("related:")
        for rel in related:
            rel_escaped = str(rel).replace('"', '\\"')
            lines.append(f'  - "{rel_escaped}"')
    else:
        lines.append("related: []")

    lines.append("---")
    return lines


_HEADING_RE = re.compile(r"^(#{1,6})([^#])", re.MULTILINE)


def shift_headings(content: str, levels: int = 3) -> str:
    """Shift all markdown ATX headings down by ``levels``, capping at H6.

    Args:
        content: Markdown text whose headings should be shifted.
        levels: Number of heading levels to add.

    Returns:
        The content with all headings shifted down, capped at H6.
    """

    def _shift(match: re.Match[str]) -> str:
        current = len(match.group(1))
        new_level = min(current + levels, 6)
        return "#" * new_level + match.group(2)

    return _HEADING_RE.sub(_shift, content)


def generate_body(
    data: SessionData,
    title: str,
    my_notes: str | None = None,
    include_thinking: bool = False,
    include_commands: bool = True,
    include_tool_context: bool = False,
) -> list[str]:
    """Generate markdown body lines (title, notes, conversation)."""
    lines = [f"# {title}", ""]

    # My Notes
    if my_notes:
        lines.append(my_notes)
    else:
        lines.extend([PRESERVED_SECTION, "", MY_NOTES_COMMENT])
    lines.append("")

    # Conversation
    lines.extend(["## Conversation", ""])

    for entry in data.conversation:
        if entry.role == "user":
            if not include_commands and entry.content.startswith(COMMAND_PREFIX):
                continue
            ts = format_timestamp_utc(entry.timestamp)
            heading = f"### User — {ts}" if ts else "### User"
            lines.append(heading)
            lines.append("")
            if entry.label in ("rejected", "approved"):
                label_text = entry.label.capitalize()
                if include_tool_context and entry.tool_context:
                    if entry.tool_summary:
                        lines.append(f"**{label_text}** `{entry.tool_summary}`:")
                    else:
                        lines.append(f"**{label_text}:**")
                    lines.append("")
                    lines.append(f"> [!info]- Proposed change")
                    lines.append(entry.tool_context)
                    lines.append("")
                    if entry.content:
                        lines.append(shift_headings(entry.content))
                else:
                    lines.append(f"**{label_text}:** {shift_headings(entry.content)}")
            elif entry.label == "while_processing":
                lines.append(f"**While processing:** {shift_headings(entry.content)}")
            else:
                lines.append(shift_headings(entry.content))
            lines.append("")
        elif entry.role == "assistant":
            ts = format_timestamp_utc(entry.timestamp)
            heading = f"### Assistant — {ts}" if ts else "### Assistant"
            lines.extend([heading, ""])
            if include_thinking and entry.thinking:
                thinking_lines = "\n".join(f"> {l}" for l in entry.thinking.split("\n"))
                lines.extend([
                    "> [!tip]- Thinking",
                    thinking_lines, "",
                ])
            if entry.content:
                lines.extend([shift_headings(entry.content), ""])

    return lines


def generate_markdown(
    data: SessionData,
    session_id: str,
    project: str,
    existing_fm: dict[str, typing.Any] | None = None,
    my_notes: str | None = None,
    include_thinking: bool = False,
    include_commands: bool = True,
    include_tool_context: bool = False,
) -> str:
    """Generate full markdown content from session data."""
    fm_lines = generate_frontmatter(data, session_id, project, existing_fm)

    # Resolve title for body (same logic as frontmatter)
    title = existing_fm.get("title") if existing_fm else None
    if isinstance(title, str) and re.search(r"<[^>]+>", title):
        title = None
    if not title:
        title = data.title or "Untitled Session"

    body_lines = generate_body(data, title, my_notes, include_thinking, include_commands, include_tool_context)

    return "\n".join(fm_lines + [""] + body_lines)


# =============================================================================
# Session Index (avoids repeated directory scans)
# =============================================================================

@dataclass
class SessionIndex:
    """Pre-built index of session transcripts and existing exports."""
    # session_id -> (jsonl_path, encoded_dir)
    transcripts: dict[str, tuple[Path, str]] = field(default_factory=dict)
    # session_id_prefix -> exported_file_path
    exports: dict[str, Path] = field(default_factory=dict)

    @classmethod
    def build(cls, vault_dir: Path | None = None, output_dir_name: str = DEFAULT_OUTPUT_DIR) -> SessionIndex:
        """Scan project dirs and vault to build the index."""
        idx = cls()

        # Index transcripts
        if PROJECTS_DIR.exists():
            try:
                for project_dir in PROJECTS_DIR.iterdir():
                    if not project_dir.is_dir():
                        continue
                    try:
                        for jsonl_file in project_dir.glob("*.jsonl"):
                            if jsonl_file.stat().st_size > 0:
                                idx.transcripts[jsonl_file.stem] = (jsonl_file, project_dir.name)
                    except PermissionError:
                        print(f"Warning: Cannot read {project_dir}", file=sys.stderr)
            except PermissionError:
                print(f"Warning: Cannot read {PROJECTS_DIR}", file=sys.stderr)

        # Index existing exports
        if vault_dir:
            output_dir = vault_dir / output_dir_name
            if output_dir.exists():
                for md_file in output_dir.rglob("*.md"):
                    # Extract session ID prefix from filename (last 8 chars before .md)
                    stem = md_file.stem
                    parts = stem.rsplit("-", 1)
                    if len(parts) == 2 and len(parts[1]) == 8 and re.fullmatch(r'[0-9a-f]{8}', parts[1]):
                        idx.exports[parts[1]] = md_file

        return idx

    def find_transcript(self, session_id: str) -> tuple[Path, str] | None:
        """Find transcript path and encoded dir for a session."""
        return self.transcripts.get(session_id)

    def find_export(self, session_id: str) -> Path | None:
        """Find existing export file for a session."""
        return self.exports.get(session_id[:8])


# =============================================================================
# Session Operations
# =============================================================================

def get_output_path(
    vault_dir: Path,
    output_dir_name: str,
    session_id: str,
    first_timestamp: str,
    project: str,
) -> Path | None:
    """Get the output path for a session, organized by project subdirectory."""
    if not project:
        return None
    output_dir = vault_dir / output_dir_name
    try:
        dt = datetime.fromisoformat(first_timestamp.replace("Z", "+00:00"))
        date_time = dt.strftime("%Y-%m-%d-%H%M")
    except (ValueError, AttributeError):
        date_time = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    return output_dir / project / f"{date_time}-{session_id[:8]}.md"


def _find_transcript(
    session_id: str,
    transcript_path: str | None,
    index: SessionIndex | None,
    quiet: bool = False,
) -> tuple[Path, str] | None:
    """Locate the JSONL transcript for a session.

    Returns (jsonl_path, encoded_dir) or None if not found.
    """
    if session_id.startswith(AGENT_PREFIX):
        return None

    encoded_dir = ""
    jsonl_path: Path | None = None

    if transcript_path:
        jsonl_path = Path(transcript_path)
        if jsonl_path.parent.name and jsonl_path.parent != PROJECTS_DIR:
            encoded_dir = jsonl_path.parent.name
    elif index:
        result = index.find_transcript(session_id)
        if result:
            jsonl_path, encoded_dir = result
        else:
            if not quiet:
                print(f"Transcript not found for {session_id[:8]}", file=sys.stderr)
            return None
    else:
        # Fallback: scan directories
        if PROJECTS_DIR.exists():
            for project_dir in PROJECTS_DIR.iterdir():
                if not project_dir.is_dir():
                    continue
                candidate = project_dir / f"{session_id}.jsonl"
                if candidate.exists() and candidate.stat().st_size > 0:
                    jsonl_path = candidate
                    encoded_dir = project_dir.name
                    break
        if not jsonl_path:
            if not quiet:
                print(f"Transcript not found for {session_id[:8]}", file=sys.stderr)
            return None

    if not jsonl_path.exists() or jsonl_path.stat().st_size == 0:
        if not quiet:
            print(f"Transcript not found for {session_id[:8]}", file=sys.stderr)
        return None

    return jsonl_path, encoded_dir


def parse_session(
    session_id: str,
    transcript_path: str | None,
    index: SessionIndex | None = None,
    quiet: bool = False,
) -> SessionData | None:
    """Parse JSONL once and return SessionData, or None if not found.

    Always extracts thinking blocks and commands. The decision to include
    them in markdown output is deferred to write_session_to_vault.
    """
    found = _find_transcript(session_id, transcript_path, index, quiet)
    if not found:
        return None

    jsonl_path, encoded_dir = found
    data = extract_session_data(iter_jsonl(jsonl_path))
    data.session_id = session_id
    data.encoded_dir = encoded_dir
    return data


def write_session_to_vault(
    data: SessionData,
    vault_dir: Path,
    config: dict[str, typing.Any],
    index: SessionIndex | None = None,
    quiet: bool = False,
) -> Path | None:
    """Write parsed SessionData to a specific vault. Returns output path or None.

    Resolves project from this vault's config, determines include_thinking
    per-project, generates markdown, and writes to the vault.
    """
    project_map = get_project_map(config)
    output_dir_name = get_output_dir_name(config)

    # Resolve project — skip unmatched sessions
    project = resolve_project(data.encoded_dir, project_map)
    if not project:
        return None

    # Check per-project config
    project_config = get_project_config(config, project) if project else {}
    include_thinking = bool(project_config.get("include_thinking", False))
    include_commands = bool(project_config.get("include_commands", True))
    include_tool_context = bool(project_config.get("include_tool_context", False))

    # Find or create output file
    existing_file = index.find_export(data.session_id) if index else None
    output_file = existing_file or get_output_path(
        vault_dir, output_dir_name, data.session_id, data.first_timestamp, project
    )

    if output_file is None:
        return None

    # Load existing content for preservation
    existing_fm: dict[str, typing.Any] | None = None
    my_notes: str | None = None
    if output_file.exists():
        try:
            content = output_file.read_text(encoding="utf-8")
            existing_fm = parse_frontmatter(content)
            my_notes = extract_my_notes(content)
        except (OSError, UnicodeDecodeError) as e:
            print(f"Warning: Cannot read existing file {output_file}: {e}", file=sys.stderr)

    # Generate and write
    output_file.parent.mkdir(parents=True, exist_ok=True)
    markdown = generate_markdown(
        data, data.session_id, project, existing_fm, my_notes,
        include_thinking, include_commands, include_tool_context,
    )

    try:
        output_file.write_text(markdown, encoding="utf-8")
    except OSError as e:
        print(f"Error: Cannot write {output_file}: {e}", file=sys.stderr)
        return None

    # Sync memories for this project
    if data.encoded_dir:
        sync_memories(data.encoded_dir, project, vault_dir, output_dir_name, quiet)

    return output_file


# =============================================================================
# Memory Sync
# =============================================================================

def sync_memories(
    encoded_dir: str,
    project: str,
    vault_dir: Path,
    output_dir_name: str,
    quiet: bool = False,
) -> int:
    """Copy memory files from a project to the vault. Returns count of files copied."""
    source_dir = PROJECTS_DIR / encoded_dir / "memory"
    if not source_dir.exists():
        return 0

    if not project:
        return 0
    target_dir = vault_dir / output_dir_name / project / "memory"
    target_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    for source_file in source_dir.iterdir():
        if not source_file.is_file():
            continue
        target_file = target_dir / source_file.name
        if target_file.exists() and target_file.stat().st_mtime >= source_file.stat().st_mtime:
            continue
        try:
            shutil.copy2(source_file, target_file)
            copied += 1
        except OSError as e:
            print(f"Warning: Cannot copy {source_file.name}: {e}", file=sys.stderr)

    if copied and not quiet:
        print(f"  Memories: copied {copied} files to {target_dir}")

    return copied


def sync_all_memories(
    vault_dir: Path,
    config: dict[str, typing.Any],
    project_filter: str | None = None,
    quiet: bool = False,
) -> int:
    """Sync memories for all projects (or filtered). Returns total files copied."""
    project_map = get_project_map(config)
    output_dir_name = get_output_dir_name(config)
    total = 0

    if not PROJECTS_DIR.exists():
        return 0

    seen_encoded_dirs: set[str] = set()
    try:
        for project_dir in PROJECTS_DIR.iterdir():
            if not project_dir.is_dir():
                continue
            encoded_dir = project_dir.name
            if encoded_dir in seen_encoded_dirs:
                continue
            seen_encoded_dirs.add(encoded_dir)

            memory_dir = project_dir / "memory"
            if not memory_dir.exists():
                continue

            project = resolve_project(encoded_dir, project_map)
            if project_filter and project != project_filter:
                continue

            total += sync_memories(encoded_dir, project, vault_dir, output_dir_name, quiet)
    except PermissionError:
        print(f"Warning: Cannot read {PROJECTS_DIR}", file=sys.stderr)

    return total


# =============================================================================
# Commands
# =============================================================================

def cmd_sync(args: argparse.Namespace) -> int:
    """Sync a single session across all vaults."""
    vault_dirs = resolve_vault_dirs()
    contexts = load_vault_contexts(vault_dirs)
    vprint = _make_printer(contexts, quiet=args.quiet)

    session_id: str | None = args.session_id
    transcript_path: str | None = args.transcript

    # Try reading from stdin (hook mode)
    if not session_id:
        try:
            if not sys.stdin.isatty():
                hook_input = json.loads(sys.stdin.read())
                session_id = hook_input.get("session_id")
                transcript_path = transcript_path or hook_input.get("transcript_path")
        except json.JSONDecodeError:
            pass
        except OSError as e:
            print(f"Warning: Cannot read stdin: {e}", file=sys.stderr)

    if not session_id:
        print("Error: No session ID. Use --session-id or pipe hook JSON to stdin.", file=sys.stderr)
        return 1

    # Parse JSONL once
    data = parse_session(session_id, transcript_path, quiet=args.quiet)
    if data is None:
        # Check if it was an unmatched project — report per vault
        if not args.quiet:
            for ctx in contexts:
                # Build a lightweight index just to find the transcript
                index = SessionIndex.build(ctx.vault_dir, get_output_dir_name(ctx.config))
                transcript_info = index.find_transcript(session_id)
                if transcript_info:
                    _, encoded_dir = transcript_info
                    project = resolve_project(encoded_dir, get_project_map(ctx.config))
                    if not project:
                        vprint(
                            f"Skipped: no project match for {encoded_dir}. "
                            "Add it to project-map.yaml.",
                            ctx,
                        )
        return 1

    # Write to each vault
    any_success = False
    for ctx in contexts:
        index = SessionIndex.build(ctx.vault_dir, get_output_dir_name(ctx.config))
        result = write_session_to_vault(data, ctx.vault_dir, ctx.config, index, quiet=args.quiet)
        if result:
            vprint(f"Synced: {result}", ctx)
            any_success = True
        elif not args.quiet:
            # Check if unmatched for this vault
            project = resolve_project(data.encoded_dir, get_project_map(ctx.config))
            if not project:
                vprint(
                    f"Skipped: no project match for {data.encoded_dir}. "
                    "Add it to project-map.yaml.",
                    ctx,
                )

    return 0 if any_success else 1


def cmd_export(args: argparse.Namespace) -> int:
    """Batch export sessions across all vaults."""
    vault_dirs = resolve_vault_dirs()
    contexts = load_vault_contexts(vault_dirs)
    vprint = _make_printer(contexts, quiet=args.quiet)

    # Memory-only mode
    if args.memory:
        for ctx in contexts:
            total = sync_all_memories(
                ctx.vault_dir, ctx.config, project_filter=args.project, quiet=args.quiet
            )
            vprint(f"Memory sync complete: {total} files copied", ctx)
        return 0

    # Single file export
    if args.file:
        jsonl_path = Path(args.file)
        if not jsonl_path.exists():
            print(f"Error: File not found: {jsonl_path}", file=sys.stderr)
            return 1
        session_id = jsonl_path.stem

        # Parse once
        data = parse_session(session_id, str(jsonl_path), quiet=args.quiet)
        if data is None:
            return 1

        for ctx in contexts:
            index = SessionIndex.build(ctx.vault_dir, get_output_dir_name(ctx.config))
            result = write_session_to_vault(data, ctx.vault_dir, ctx.config, index, quiet=args.quiet)
            if result:
                vprint(f"Synced: {result}", ctx)
        return 0

    if not args.today and not args.all and not args.project:
        print("Error: Specify --today, --all, --memory, or --project NAME.", file=sys.stderr)
        return 1

    # Build a transcript-only index (no vault needed for source scanning)
    transcript_index = SessionIndex.build(vault_dir=None)

    # Filter sessions by date/project criteria (project matching is vault-specific,
    # so we collect all candidate transcripts and filter per-vault)
    today_date = date.today().isoformat()
    candidates: list[tuple[str, Path, str]] = []  # (session_id, path, encoded_dir)

    for session_id, (jsonl_path, encoded_dir) in transcript_index.transcripts.items():
        if session_id.startswith(AGENT_PREFIX):
            continue

        if args.today:
            try:
                mtime = datetime.fromtimestamp(jsonl_path.stat().st_mtime, tz=timezone.utc)
                if mtime.strftime("%Y-%m-%d") != today_date:
                    continue
            except OSError:
                continue

        candidates.append((session_id, jsonl_path, encoded_dir))

    # Parse each JSONL once, then write to all vaults
    parsed_sessions: list[SessionData] = []
    for session_id, jsonl_path, encoded_dir in candidates:
        data = parse_session(session_id, str(jsonl_path), quiet=True)
        if data is not None:
            parsed_sessions.append(data)

    # Write to each vault
    for ctx in contexts:
        project_map = get_project_map(ctx.config)
        output_dir_name = get_output_dir_name(ctx.config)
        vault_index = SessionIndex.build(ctx.vault_dir, output_dir_name)

        unmatched_dirs: set[str] = set()
        sessions_for_vault: list[SessionData] = []

        for data in parsed_sessions:
            project = resolve_project(data.encoded_dir, project_map)
            if not project:
                unmatched_dirs.add(data.encoded_dir)
                continue
            if args.project and project != args.project:
                continue
            sessions_for_vault.append(data)

        vprint(f"Found {len(sessions_for_vault)} sessions", ctx)

        synced = 0
        for data in sessions_for_vault:
            result = write_session_to_vault(
                data, ctx.vault_dir, ctx.config, vault_index, quiet=args.quiet
            )
            if result:
                vprint(f"Synced: {result}", ctx)
                synced += 1

        # Sync memories
        mem_total = sync_all_memories(
            ctx.vault_dir, ctx.config, project_filter=args.project, quiet=args.quiet
        )

        vprint(
            f"Synced {synced}/{len(sessions_for_vault)} sessions, "
            f"{mem_total} memory files copied",
            ctx,
        )

        # Report unmatched directories
        if unmatched_dirs and not args.quiet:
            print(
                f"\nUnmatched directories ({len(unmatched_dirs)}) — "
                "add these to project-map.yaml:",
                file=sys.stderr,
            )
            for d in sorted(unmatched_dirs):
                print(f"  {d}", file=sys.stderr)

    return 0


def cmd_note(args: argparse.Namespace) -> int:
    """Add a timestamped note to a session across all vaults."""
    vault_dirs = resolve_vault_dirs()
    contexts = load_vault_contexts(vault_dirs)
    vprint = _make_printer(contexts, quiet=args.quiet)

    session_id: str | None = args.session_id
    if not session_id:
        print("Error: No session ID. Use --session-id.", file=sys.stderr)
        return 1

    any_found = False
    for ctx in contexts:
        output_dir_name = get_output_dir_name(ctx.config)
        index = SessionIndex.build(ctx.vault_dir, output_dir_name)
        session_file = index.find_export(session_id)

        if not session_file or not session_file.exists():
            continue

        any_found = True

        try:
            content = session_file.read_text(encoding="utf-8")
        except OSError as e:
            print(f"Error: Cannot read {session_file}: {e}", file=sys.stderr)
            continue

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        note_line = f"[{timestamp}] {args.text}"

        idx = content.find(PRESERVED_SECTION)
        if idx == -1:
            print(f"Error: No '## My Notes' section found in {session_file.name}.", file=sys.stderr)
            continue

        comment_marker = "<!-- Add your notes here."
        comment_idx = content.find(comment_marker, idx)

        if comment_idx != -1:
            comment_end = content.find("-->", comment_idx)
            if comment_end != -1:
                content = content[:comment_idx] + note_line + "\n" + content[comment_end + 3:].lstrip("\n")
        else:
            next_heading = content.find("\n## ", idx + len(PRESERVED_SECTION))
            if next_heading == -1:
                content = content.rstrip() + "\n" + note_line + "\n"
            else:
                content = content[:next_heading] + "\n" + note_line + "\n" + content[next_heading:]

        try:
            session_file.write_text(content, encoding="utf-8")
        except OSError as e:
            print(f"Error: Cannot write {session_file}: {e}", file=sys.stderr)
            continue

        vprint(f"Note added to {session_file.name}", ctx)

    if not any_found:
        print(f"Error: No exported file found for session {session_id[:8]} in any vault", file=sys.stderr)
        print("Run sync or export first.", file=sys.stderr)
        return 1

    return 0


# =============================================================================
# Main
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="claude-session-export",
        description="Export Claude Code sessions to Obsidian markdown",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # sync
    p_sync = subparsers.add_parser("sync", help="Sync a single session")
    p_sync.add_argument("--session-id", help="Session ID (UUID)")
    p_sync.add_argument("--transcript", help="Path to transcript JSONL file")
    p_sync.add_argument("-q", "--quiet", action="store_true", help="Suppress output")
    p_sync.set_defaults(func=cmd_sync)

    # export
    p_export = subparsers.add_parser("export", help="Batch export sessions")
    p_export.add_argument("--today", action="store_true", help="Export today's sessions")
    p_export.add_argument("--all", action="store_true", help="Export all sessions")
    p_export.add_argument("--project", help="Filter by project name")
    p_export.add_argument("--memory", action="store_true", help="Export memories only (no sessions)")
    p_export.add_argument("-q", "--quiet", action="store_true", help="Suppress output")
    p_export.add_argument("file", nargs="?", help="Specific JSONL file to export")
    p_export.set_defaults(func=cmd_export)

    # note
    p_note = subparsers.add_parser("note", help="Add a note to a session")
    p_note.add_argument("text", help="Note text")
    p_note.add_argument("--session-id", help="Session ID (UUID)")
    p_note.add_argument("-q", "--quiet", action="store_true", help="Suppress output")
    p_note.set_defaults(func=cmd_note)

    args = parser.parse_args()

    try:
        sys.exit(args.func(args))
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
