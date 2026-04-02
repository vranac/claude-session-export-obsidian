#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pyyaml",
#     "python-dotenv",
# ]
# ///
"""Manage VAULT_DIR configuration and project-map.yaml creation.

Idempotent and deterministic. All machine-readable output is JSON to stdout;
human-readable messages go to stderr.

Usage:
    configure-vault.py set-env --vault-dir /path/to/vault [--env-file .env]
    configure-vault.py check [--vault-dir /path/to/vault]
    configure-vault.py init-map --vault-dir /path/to/vault [--output-dir Claude-Sessions]
    configure-vault.py validate-map --vault-dir /path/to/vault
    configure-vault.py list-unmapped --vault-dir /path/to/vault
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# =============================================================================
# Constants
# =============================================================================

PROJECTS_DIR = Path.home() / ".claude" / "projects"
DEFAULT_OUTPUT_DIR = "Claude-Sessions"
CONFIG_FILENAME = "project-map.yaml"


# =============================================================================
# Helpers
# =============================================================================


def _info(msg: str) -> None:
    """Print a human-readable message to stderr."""
    print(msg, file=sys.stderr)


def _error(msg: str) -> None:
    """Print an error message to stderr."""
    print(f"Error: {msg}", file=sys.stderr)


def _json_out(data: Any) -> None:
    """Print JSON to stdout."""
    print(json.dumps(data, indent=2))


def _parse_vault_paths(raw: str) -> list[str]:
    """Split a comma-separated vault string into trimmed, non-empty paths."""
    return [p.strip() for p in raw.split(",") if p.strip()]


def _scan_claude_projects() -> list[str]:
    """Scan ~/.claude/projects/ for directories containing .jsonl files.

    Returns sorted list of encoded directory names.
    """
    if not PROJECTS_DIR.exists():
        return []
    try:
        return sorted([
            d.name
            for d in PROJECTS_DIR.iterdir()
            if d.is_dir() and next(d.glob("*.jsonl"), None) is not None
        ])
    except PermissionError:
        _info(f"Warning: Cannot read {PROJECTS_DIR}")
        return []


def _resolve_vault_dir(args_vault_dir: str | None) -> tuple[str | None, str | None]:
    """Resolve VAULT_DIR from args, environment, or .env.

    Returns (raw_value, source) where source is one of
    "argument", "environment", ".env", or None.
    """
    if args_vault_dir:
        return args_vault_dir, "argument"

    vault = os.environ.get("VAULT_DIR")
    if vault:
        return vault, "environment"

    load_dotenv()
    vault = os.environ.get("VAULT_DIR")
    if vault:
        return vault, ".env"

    return None, None


def _read_env_file(env_path: Path) -> list[str]:
    """Read an .env file, returning lines. Returns empty list if missing."""
    if not env_path.exists():
        return []
    try:
        return env_path.read_text(encoding="utf-8").splitlines()
    except PermissionError:
        _error(f"Permission denied reading {env_path}")
        sys.exit(1)


def _write_env_file(env_path: Path, lines: list[str]) -> None:
    """Write lines to an .env file, creating parent dirs if needed."""
    try:
        env_path.parent.mkdir(parents=True, exist_ok=True)
        content = "\n".join(lines)
        if content and not content.endswith("\n"):
            content += "\n"
        env_path.write_text(content, encoding="utf-8")
    except PermissionError:
        _error(f"Permission denied writing {env_path}")
        sys.exit(1)


def _load_project_map(vault_path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Load project-map.yaml from a vault directory.

    Returns (data, error_message). On success error_message is None.
    """
    map_path = vault_path / CONFIG_FILENAME
    if not map_path.exists():
        return None, f"{CONFIG_FILENAME} not found in {vault_path}"
    try:
        text = map_path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            return None, f"{CONFIG_FILENAME} root is not a mapping"
        return data, None
    except yaml.YAMLError as exc:
        return None, f"Invalid YAML: {exc}"
    except PermissionError:
        return None, f"Permission denied reading {map_path}"


# =============================================================================
# Commands
# =============================================================================


def cmd_set_env(vault_dir: str, env_file: str) -> None:
    """Add or update VAULT_DIR in a .env file."""
    paths = _parse_vault_paths(vault_dir)
    if not paths:
        _error("--vault-dir must not be empty")
        sys.exit(1)

    # Validate each path exists and is a directory.
    for p in paths:
        path = Path(p)
        if not path.exists():
            _error(f"Path does not exist: {p}")
            sys.exit(1)
        if not path.is_dir():
            _error(f"Path is not a directory: {p}")
            sys.exit(1)

    env_path = Path(env_file)
    lines = _read_env_file(env_path)

    # Normalize the value we will write.
    new_value = ",".join(paths)

    # Check if VAULT_DIR is already set to the same value.
    vault_line_prefix = "VAULT_DIR="
    found_index: int | None = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(vault_line_prefix) and not stripped.startswith("#"):
            found_index = i
            existing_value = stripped[len(vault_line_prefix):]
            # Strip surrounding quotes if present.
            if (
                len(existing_value) >= 2
                and existing_value[0] in ('"', "'")
                and existing_value[-1] == existing_value[0]
            ):
                existing_value = existing_value[1:-1]
            if existing_value == new_value:
                _info("VAULT_DIR already set")
                return

    new_line = f'{vault_line_prefix}"{new_value}"'

    if found_index is not None:
        lines[found_index] = new_line
    else:
        lines.append(new_line)

    _write_env_file(env_path, lines)
    _info(f"VAULT_DIR set to: {new_value}")
    _info(f"File: {env_path.resolve()}")


def cmd_set_settings(vault_dir: str, settings_file: str) -> None:
    """Add or update VAULT_DIR in a Claude Code settings JSON file."""
    paths = _parse_vault_paths(vault_dir)
    if not paths:
        _error("--vault-dir must not be empty")
        sys.exit(1)

    for p in paths:
        path = Path(p)
        if not path.exists():
            _error(f"Path does not exist: {p}")
            sys.exit(1)
        if not path.is_dir():
            _error(f"Path is not a directory: {p}")
            sys.exit(1)

    new_value = ",".join(paths)
    settings_path = Path(settings_file)

    # Read existing settings or start fresh
    data: dict[str, Any] = {}
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except (json.JSONDecodeError, OSError) as e:
            _error(f"Cannot read {settings_path}: {e}")
            sys.exit(1)

    # Check if already set to same value
    env_block = data.get("env", {})
    if not isinstance(env_block, dict):
        env_block = {}
    if env_block.get("VAULT_DIR") == new_value:
        _info("VAULT_DIR already set in settings")
        return

    # Update
    env_block["VAULT_DIR"] = new_value
    data["env"] = env_block

    # Write back
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        settings_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        _error(f"Cannot write {settings_path}: {e}")
        sys.exit(1)

    _info(f"VAULT_DIR set to: {new_value}")
    _info(f"File: {settings_path.resolve()}")


def cmd_check(vault_dir: str | None) -> None:
    """Check vault configuration and output JSON."""
    raw_value, source = _resolve_vault_dir(vault_dir)

    if not raw_value:
        _json_out({
            "configured": False,
            "source": None,
            "vaults": [],
        })
        return

    paths = _parse_vault_paths(raw_value)
    vaults: list[dict[str, Any]] = []
    for p in paths:
        vault_path = Path(p)
        vaults.append({
            "path": p,
            "exists": vault_path.is_dir(),
            "has_project_map": (vault_path / CONFIG_FILENAME).is_file(),
        })

    _json_out({
        "configured": True,
        "source": source,
        "vaults": vaults,
    })


def cmd_init_map(vault_dir: str, output_dir: str) -> None:
    """Create a project-map.yaml template in vault directories."""
    paths = _parse_vault_paths(vault_dir)
    if not paths:
        _error("--vault-dir must not be empty")
        sys.exit(1)

    claude_projects = _scan_claude_projects()

    # Build the template content.
    project_lines = _build_map_template(output_dir, claude_projects)

    for p in paths:
        vault_path = Path(p)
        if not vault_path.is_dir():
            _info(f"Warning: Skipping non-existent vault: {p}")
            continue

        map_path = vault_path / CONFIG_FILENAME
        if map_path.exists():
            _info(f"project-map.yaml already exists: {map_path}")
            continue

        try:
            map_path.write_text(project_lines, encoding="utf-8")
            _info(f"Created {map_path}")
        except PermissionError:
            _error(f"Permission denied writing {map_path}")
            sys.exit(1)


def _build_map_template(output_dir: str, claude_projects: list[str]) -> str:
    """Build the YAML template string for project-map.yaml."""
    lines: list[str] = [
        "# project-map.yaml",
        "# Maps encoded Claude project directories to Obsidian project names.",
        "#",
        "# Each key under 'projects' is the name of the subfolder created in",
        f"# $VAULT_DIR/{output_dir}/. Each project uses a 'patterns:' key whose value",
        "# is a list of encoded directory patterns from ~/.claude/projects/.",
        "# Glob patterns (fnmatch) are supported.",
        "#",
        "# Run 'configure-vault.py list-unmapped --vault-dir .' to find unmapped dirs.",
        "",
        f"output_dir: {output_dir}",
        "",
        "projects:",
        "  # Example:",
        "  # my-project:",
        "  #   patterns:",
        "  #     - \"-Users-me-dev-my-project\"",
        "  #     - \"-Users-me-dev-my-project-*\"",
    ]

    if claude_projects:
        lines.append("")
        lines.append("  # Detected Claude project directories:")
        for proj in claude_projects:
            lines.append(f"  # - \"{proj}\"")

    lines.append("")
    return "\n".join(lines)


def cmd_validate_map(vault_dir: str) -> None:
    """Validate project-map.yaml and output JSON results."""
    paths = _parse_vault_paths(vault_dir)
    if not paths:
        _error("--vault-dir must not be empty")
        sys.exit(1)

    # Validate the first vault (primary).
    vault_path = Path(paths[0])
    data, err = _load_project_map(vault_path)

    if err is not None:
        _json_out({
            "valid": False,
            "error": err,
            "has_output_dir": False,
            "output_dir": None,
            "project_count": 0,
            "warnings": [],
        })
        sys.exit(1)

    assert data is not None  # guaranteed by _load_project_map

    warnings: list[str] = []

    # Check output_dir.
    has_output_dir = "output_dir" in data
    output_dir_value = data.get("output_dir", DEFAULT_OUTPUT_DIR)
    if not has_output_dir:
        warnings.append(
            f"'output_dir' not set; default '{DEFAULT_OUTPUT_DIR}' will be used"
        )

    # Check projects key.
    projects = data.get("projects")
    valid = True
    project_count = 0

    if projects is None:
        warnings.append("'projects' key is missing")
        valid = False
    elif not isinstance(projects, dict):
        warnings.append("'projects' must be a mapping (dict), not a list or scalar")
        valid = False
    else:
        project_count = len(projects)
        if project_count == 0:
            warnings.append("'projects' is empty; no sessions will be exported")

    _json_out({
        "valid": valid,
        "has_output_dir": has_output_dir,
        "output_dir": output_dir_value,
        "project_count": project_count,
        "warnings": warnings,
    })

    if not valid:
        sys.exit(1)


def cmd_list_unmapped(vault_dir: str) -> None:
    """List Claude project directories not covered by the project map."""
    paths = _parse_vault_paths(vault_dir)
    if not paths:
        _error("--vault-dir must not be empty")
        sys.exit(1)

    vault_path = Path(paths[0])
    data, err = _load_project_map(vault_path)

    if err is not None:
        _error(err)
        sys.exit(1)

    assert data is not None

    # Collect all patterns from the project map.
    projects = data.get("projects", {})
    if not isinstance(projects, dict):
        _error("'projects' in project-map.yaml is not a mapping")
        sys.exit(1)

    all_patterns: list[str] = []
    for _name, value in projects.items():
        if isinstance(value, list):
            all_patterns.extend(str(v) for v in value)
        elif isinstance(value, dict):
            patterns = value.get("patterns", [])
            if isinstance(patterns, list):
                all_patterns.extend(str(v) for v in patterns)

    # Scan Claude projects and find unmapped ones.
    claude_projects = _scan_claude_projects()
    from fnmatch import fnmatch

    unmapped: list[str] = []
    for proj_dir in claude_projects:
        matched = any(fnmatch(proj_dir, pat) for pat in all_patterns)
        if not matched:
            unmapped.append(proj_dir)

    _json_out({"unmapped": unmapped})


# =============================================================================
# CLI
# =============================================================================


def main() -> None:
    """Entry point for the configure-vault CLI."""
    parser = argparse.ArgumentParser(
        description="Manage VAULT_DIR configuration and project-map.yaml."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # set-env
    set_env_parser = subparsers.add_parser(
        "set-env", help="Set VAULT_DIR in a .env file"
    )
    set_env_parser.add_argument(
        "--vault-dir", required=True, help="Vault directory path (comma-separated for multi-vault)"
    )
    set_env_parser.add_argument(
        "--env-file", default=".env", help="Path to .env file (default: .env in CWD)"
    )

    # set-settings
    set_settings_parser = subparsers.add_parser(
        "set-settings", help="Set VAULT_DIR in a Claude Code settings JSON file"
    )
    set_settings_parser.add_argument(
        "--vault-dir", required=True, help="Vault directory path (comma-separated for multi-vault)"
    )
    set_settings_parser.add_argument(
        "--settings-file", required=True, help="Path to settings file (e.g., ~/.claude/settings.json)"
    )

    # check
    check_parser = subparsers.add_parser(
        "check", help="Check vault configuration status"
    )
    check_parser.add_argument(
        "--vault-dir", default=None, help="Vault directory path (overrides env/.env resolution)"
    )

    # init-map
    init_map_parser = subparsers.add_parser(
        "init-map", help="Create a project-map.yaml template in the vault"
    )
    init_map_parser.add_argument(
        "--vault-dir", required=True, help="Vault directory path (comma-separated for multi-vault)"
    )
    init_map_parser.add_argument(
        "--output-dir", default=DEFAULT_OUTPUT_DIR, help=f"Output directory name (default: {DEFAULT_OUTPUT_DIR})"
    )

    # validate-map
    validate_map_parser = subparsers.add_parser(
        "validate-map", help="Validate an existing project-map.yaml"
    )
    validate_map_parser.add_argument(
        "--vault-dir", required=True, help="Vault directory path"
    )

    # list-unmapped
    list_unmapped_parser = subparsers.add_parser(
        "list-unmapped", help="List Claude project directories not in the project map"
    )
    list_unmapped_parser.add_argument(
        "--vault-dir", required=True, help="Vault directory path"
    )

    args = parser.parse_args()

    match args.command:
        case "set-env":
            cmd_set_env(args.vault_dir, args.env_file)
        case "set-settings":
            cmd_set_settings(args.vault_dir, args.settings_file)
        case "check":
            cmd_check(args.vault_dir)
        case "init-map":
            cmd_init_map(args.vault_dir, args.output_dir)
        case "validate-map":
            cmd_validate_map(args.vault_dir)
        case "list-unmapped":
            cmd_list_unmapped(args.vault_dir)


if __name__ == "__main__":
    main()
