#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pyyaml",
#     "python-dotenv",
# ]
# ///
"""Detect existing configuration for claude-session-export.

Outputs JSON with current state of all config: vault dirs, hooks, project maps.
Used by /session-export:setup to avoid wasting tokens on file reads.

Supports comma-separated VAULT_DIR for multi-vault setups.
"""

from __future__ import annotations

import json
import os
import sys
import typing
from pathlib import Path

import yaml
from dotenv import load_dotenv


def _check_hooks_in_settings(settings_path: Path) -> bool:
    """Check if a settings file contains claude-session-export hooks."""
    if not settings_path.exists():
        return False
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return False
        hooks = data.get("hooks", {})
        if not isinstance(hooks, dict):
            return False
        for event_hooks in hooks.values():
            if not isinstance(event_hooks, list):
                continue
            for group in event_hooks:
                if not isinstance(group, dict):
                    continue
                for hook in group.get("hooks", []):
                    if not isinstance(hook, dict):
                        continue
                    if "claude-session-export" in hook.get("command", ""):
                        return True
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        print(f"Warning: Cannot read {settings_path}: {e}", file=sys.stderr)
    return False


def _detect_vault_config(vault_path: Path) -> dict[str, typing.Any]:
    """Detect project map configuration for a single vault."""
    vault_info: dict[str, typing.Any] = {
        "path": str(vault_path),
        "name": vault_path.name,
        "exists": vault_path.is_dir(),
        "project_map": {
            "exists": False,
            "path": None,
            "has_output_dir": False,
            "output_dir": None,
            "has_projects": False,
            "project_count": 0,
            "contents": None,
        },
    }

    if not vault_path.is_dir():
        return vault_info

    map_path = vault_path / "project-map.yaml"
    if map_path.exists():
        vault_info["project_map"]["exists"] = True
        vault_info["project_map"]["path"] = str(map_path)
        try:
            data = yaml.safe_load(map_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                vault_info["project_map"]["contents"] = data
                if "output_dir" in data:
                    vault_info["project_map"]["has_output_dir"] = True
                    vault_info["project_map"]["output_dir"] = data["output_dir"]
                projects = data.get("projects")
                if isinstance(projects, dict) and projects:
                    vault_info["project_map"]["has_projects"] = True
                    vault_info["project_map"]["project_count"] = len(projects)
        except (yaml.YAMLError, OSError, UnicodeDecodeError) as e:
            print(f"Warning: Cannot parse {map_path}: {e}", file=sys.stderr)

    return vault_info


def detect() -> dict[str, typing.Any]:
    """Detect all existing configuration. Returns structured dict."""
    result: dict[str, typing.Any] = {
        "vault_dir": {"raw_value": None, "source": None, "vaults": []},
        "hooks": {
            "global": False,
            "local": False,
            "global_path": str(Path.home() / ".claude" / "settings.json"),
            "local_path": ".claude/settings.local.json",
        },
        "script_path": {
            "marketplace": None,
            "plugin_root": os.environ.get("CLAUDE_PLUGIN_ROOT"),
        },
        "claude_projects": [],
    }

    # --- VAULT_DIR ---
    vault = os.environ.get("VAULT_DIR")
    source = "environment" if vault else None
    if not vault:
        load_dotenv()
        vault = os.environ.get("VAULT_DIR")
        if vault:
            source = ".env"

    if vault:
        result["vault_dir"]["raw_value"] = vault
        result["vault_dir"]["source"] = source

        raw_paths = [p.strip() for p in vault.split(",") if p.strip()]
        for raw in raw_paths:
            vault_path = Path(raw)
            result["vault_dir"]["vaults"].append(_detect_vault_config(vault_path))

    # --- Hooks ---
    global_settings = Path.home() / ".claude" / "settings.json"
    result["hooks"]["global"] = _check_hooks_in_settings(global_settings)

    local_settings = Path(".claude/settings.local.json")
    result["hooks"]["local"] = _check_hooks_in_settings(local_settings)

    # --- Script Path ---
    marketplace_path = (
        Path.home() / ".claude" / "plugins" / "marketplaces" / "claude-session-export-obsidian"
        / "skills" / "session-export" / "scripts" / "claude-session-export.py"
    )
    if marketplace_path.exists():
        result["script_path"]["marketplace"] = str(marketplace_path)

    # --- Claude Projects ---
    projects_dir = Path.home() / ".claude" / "projects"
    if projects_dir.exists():
        try:
            result["claude_projects"] = sorted([
                d.name for d in projects_dir.iterdir()
                if d.is_dir() and next(d.glob("*.jsonl"), None) is not None
            ])
        except PermissionError:
            print(f"Warning: Cannot read {projects_dir}", file=sys.stderr)

    return result


def main() -> None:
    try:
        print(json.dumps(detect(), indent=2))
    except Exception as e:
        error_result = {"error": str(e), "type": type(e).__name__}
        print(json.dumps(error_result, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
