#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""Configure Claude Code PreCompact and SessionEnd hooks for session export.

Manages hooks in Claude Code settings files. Idempotent and deterministic.

Usage:
    configure-hooks.py add --settings-file PATH --script-path PATH
    configure-hooks.py remove --settings-file PATH
    configure-hooks.py check --settings-file PATH
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

HOOK_MARKER = "claude-session-export"
HOOK_EVENTS = ("PreCompact", "SessionEnd", "SessionStart")


def build_hook_entry(script_path: str, event: str) -> dict[str, Any]:
    """Build a single hook matcher entry for the given script path and event."""
    entry: dict[str, Any] = {
        "hooks": [
            {
                "type": "command",
                "command": f"uv run {script_path} sync",
                "timeout": 15,
            }
        ]
    }
    # SessionStart fires on multiple occasions — we only want it on /clear
    if event == "SessionStart":
        entry["matcher"] = "clear"
    return entry


def read_settings(settings_path: Path) -> dict[str, Any]:
    """Read settings from a JSON file, returning empty dict if it doesn't exist."""
    if not settings_path.exists():
        return {}
    try:
        text = settings_path.read_text(encoding="utf-8")
        if not text.strip():
            return {}
        data = json.loads(text)
        if not isinstance(data, dict):
            print(f"Warning: {settings_path} does not contain a JSON object, ignoring", file=sys.stderr)
            return {}
        return data
    except json.JSONDecodeError as exc:
        print(f"Error: Failed to parse {settings_path}: {exc}", file=sys.stderr)
        sys.exit(1)
    except PermissionError:
        print(f"Error: Permission denied reading {settings_path}", file=sys.stderr)
        sys.exit(1)


def write_settings(settings_path: Path, settings: dict[str, Any]) -> None:
    """Write settings to a JSON file, creating parent directories if needed."""
    try:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps(settings, indent=2) + "\n", encoding="utf-8"
        )
    except PermissionError:
        print(f"Error: Permission denied writing {settings_path}", file=sys.stderr)
        sys.exit(1)


def find_export_hook_indices(matchers: list[dict[str, Any]]) -> list[int]:
    """Find indices of hook matchers that contain the session-export marker."""
    indices: list[int] = []
    for i, matcher in enumerate(matchers):
        for hook in matcher.get("hooks", []):
            command = hook.get("command", "")
            if HOOK_MARKER in command:
                indices.append(i)
                break
    return indices


def cmd_add(settings_path: Path, script_path: str) -> None:
    """Add session-export hooks to the settings file."""
    settings = read_settings(settings_path)
    original_json = json.dumps(settings, sort_keys=True)
    hooks = settings.setdefault("hooks", {})

    already_configured = True

    for event in HOOK_EVENTS:
        entry = build_hook_entry(script_path, event)
        matchers: list[dict[str, Any]] = hooks.setdefault(event, [])
        existing_indices = find_export_hook_indices(matchers)

        if existing_indices:
            # Update existing hook command paths in place.
            for idx in existing_indices:
                old_hooks = matchers[idx].get("hooks", [])
                for hook in old_hooks:
                    if HOOK_MARKER in hook.get("command", ""):
                        hook["command"] = f"uv run {script_path} sync"
                        hook["timeout"] = 15
        else:
            # No existing export hook -- append a new one.
            matchers.append(entry)
            already_configured = False

    if already_configured:
        # Check whether the commands actually match the desired state.
        # If we updated any paths above, we still need to write.
        current_json = json.dumps(settings, sort_keys=True)
        if current_json == original_json:
            print("Hooks already configured")
            return

    write_settings(settings_path, settings)
    print(f"Hooks configured for events: {', '.join(HOOK_EVENTS)}")
    print(f"Script path: {script_path}")
    print(f"Settings file: {settings_path}")


def cmd_remove(settings_path: Path) -> None:
    """Remove session-export hooks from the settings file."""
    settings = read_settings(settings_path)
    hooks = settings.get("hooks")

    if not hooks:
        print("No hooks to remove")
        return

    removed_any = False

    for event in HOOK_EVENTS:
        matchers: list[dict[str, Any]] = hooks.get(event, [])
        indices_to_remove = find_export_hook_indices(matchers)

        if indices_to_remove:
            removed_any = True
            # Remove in reverse order to keep indices stable.
            for idx in reversed(indices_to_remove):
                matchers.pop(idx)

            # Clean up empty event lists.
            if not matchers:
                del hooks[event]

    # Clean up empty hooks dict.
    if not hooks:
        del settings["hooks"]

    if not removed_any:
        print("No hooks to remove")
        return

    write_settings(settings_path, settings)
    print(f"Hooks removed for events: {', '.join(HOOK_EVENTS)}")
    print(f"Settings file: {settings_path}")


def cmd_check(settings_path: Path) -> None:
    """Check if session-export hooks are present and output JSON."""
    settings = read_settings(settings_path)
    hooks = settings.get("hooks", {})

    present_events: list[str] = []
    for event in HOOK_EVENTS:
        matchers = hooks.get(event, [])
        if find_export_hook_indices(matchers):
            present_events.append(event)

    result = {
        "present": len(present_events) == len(HOOK_EVENTS),
        "events": present_events,
    }
    print(json.dumps(result, indent=2))


def main() -> None:
    """Entry point for the configure-hooks CLI."""
    parser = argparse.ArgumentParser(
        description="Manage Claude Code session-export hooks in settings files."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # add
    add_parser = subparsers.add_parser("add", help="Add hooks to a settings file")
    add_parser.add_argument(
        "--settings-file", required=True, type=Path, help="Path to the settings file"
    )
    add_parser.add_argument(
        "--script-path",
        required=True,
        type=str,
        help="Absolute path to the claude-session-export.py script",
    )

    # remove
    remove_parser = subparsers.add_parser(
        "remove", help="Remove hooks from a settings file"
    )
    remove_parser.add_argument(
        "--settings-file", required=True, type=Path, help="Path to the settings file"
    )

    # check
    check_parser = subparsers.add_parser("check", help="Check if hooks are present")
    check_parser.add_argument(
        "--settings-file", required=True, type=Path, help="Path to the settings file"
    )

    args = parser.parse_args()

    match args.command:
        case "add":
            cmd_add(args.settings_file, args.script_path)
        case "remove":
            cmd_remove(args.settings_file)
        case "check":
            cmd_check(args.settings_file)


if __name__ == "__main__":
    main()
