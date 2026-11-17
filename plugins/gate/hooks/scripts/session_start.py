#!/usr/bin/env python3
"""SessionStart hook: give Claude the test-first rule and name the conventions skills for the repo's languages."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))  # shared modules live in scripts/

from offswitch import is_off

MARKERS = {
    "go": ("go.mod",),
    "ts": ("tsconfig.json", "package.json"),
    "python": ("pyproject.toml", "setup.py"),
}
INSTRUCTION_FILES = ("CLAUDE.md", "AGENTS.md", ".claude/CLAUDE.md")
TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "CLAUDE.md"

TEST_FIRST = (
    "Test first. Before you write new behavior, write a test that fails without it, run it and see it fail. "
    "Then write the code and run the test again. Skip this for a shape PR 0 (stubs only) and for changes "
    "with no behavior: docs, config, renames."
)


def repo_root(cwd: str) -> Path | None:
    """Return the git top level for cwd, or None outside a repo."""
    try:
        out = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"], check=False, capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None


def languages(root: Path) -> list[str]:
    """Return the conventions languages whose marker files the repo tracks, at any depth."""
    specs = [f":(glob)**/{name}" for names in MARKERS.values() for name in names]
    try:
        tracked = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--", *specs], check=False, capture_output=True, text=True, timeout=10
        ).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return []
    names = {Path(p).name for p in tracked}
    return [lang for lang, markers in MARKERS.items() if names & set(markers)]


def context(root: Path) -> str:
    """Return the text to add to the session, or "" when the repo has no known language."""
    langs = languages(root)
    if not langs:
        return ""
    parts = [TEST_FIRST]
    # The skills load themselves through their `paths` frontmatter, so only name them here.
    parts += [f"{lang} conventions: use the gate:{lang}-conventions skill." for lang in langs]
    if not any((root / name).is_file() for name in INSTRUCTION_FILES):
        parts.append(
            f"This repo has no CLAUDE.md or AGENTS.md. When you learn how to build, test or run it, "
            f"tell the user once, and offer to write one from {TEMPLATE}."
        )
    return "\n\n".join(parts)


def main() -> int:
    """Read the SessionStart payload and print the added context as hook JSON, if any."""
    if is_off("GATE"):
        return 0
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}
    root = repo_root(payload.get("cwd") or os.getcwd())
    text = context(root) if root else ""
    if text:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
