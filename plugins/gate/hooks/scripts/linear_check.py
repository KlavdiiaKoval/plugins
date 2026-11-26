#!/usr/bin/env python3
"""PreToolUse hook on the Linear MCP issue tools: keep the top of a ticket short and plain for people.

A ticket description is a few plain lines for a person, then an optional collapsible
`+++ Agent notes` block with the details an agent needs. Exit 2 sends the problems back
to Claude. Only full descriptions are checked; a `patch` edit or a status change passes.
Set CLAUDE_GATE_OFF=1, or gate_off in /config, to skip.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))  # shared modules live in scripts/

import rejections
from offswitch import is_off

TOP_MAX = 15
COLLAPSIBLE = re.compile(r"(?ms)^\+\+\+.*?(?:^\+\+\+[ \t]*$|\Z)")
FENCE = re.compile(r"(?m)^\s*(```|~~~)")


def top_part(description: str) -> str:
    """Return the part of a description a person reads: everything outside the collapsible blocks."""
    return COLLAPSIBLE.sub("", description.replace("\r\n", "\n"))


def check(description: str) -> list[tuple[str, str]]:
    """Return (rule, reason) for each way the description's top part is too heavy for a person."""
    top = top_part(description)
    problems = []
    lines = [line for line in top.splitlines() if line.strip()]
    if len(lines) > TOP_MAX:
        problems.append(
            (
                "linear-top-long",
                f"the part above the Agent notes has {len(lines)} lines; keep it to {TOP_MAX}. "
                "Move lists, steps and evidence into a `+++ Agent notes` block at the end.",
            )
        )
    if FENCE.search(top):
        problems.append(
            ("linear-top-code", "move code, SQL and logs out of the top part into a `+++ Agent notes` block at the end.")
        )
    return problems


def run(payload: object) -> int:
    """Check the description in a Linear issue tool payload; return 2 with the problems on stderr. Fails open."""
    if is_off("GATE") or not isinstance(payload, dict):
        return 0
    try:
        description = (payload.get("tool_input") or {}).get("description")
        if not isinstance(description, str):
            return 0
        problems = check(description)
    except Exception:  # noqa: BLE001 - a broken check must not block Linear.
        return 0
    if not problems:
        return 0
    rejections.record("linear", sorted({rule for rule, _ in problems}), payload.get("cwd") or os.getcwd())
    print("Fix the ticket description and save it again (see the gate:linear-tickets skill):", file=sys.stderr)
    for _, reason in problems:
        print(f"- {reason}", file=sys.stderr)
    return 2


def main() -> int:
    """Read a hook payload on stdin and check its ticket description. Fails open on bad input."""
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    return run(payload)


if __name__ == "__main__":
    sys.exit(main())
