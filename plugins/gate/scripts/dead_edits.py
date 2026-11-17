#!/usr/bin/env python3
"""Print the unreachable Go functions that the change since a base commit touched.

Usage: dead_edits.py <repo-root> <base> <module-dir>. Runs `deadcode -test -json ./...`
in the module and keeps a function when a line added since base falls inside it.
Prints `path:line:col: unreachable func: Name` rows with paths relative to the module.
Prints nothing when deadcode is missing, fails, or the module has no main package.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from new_issues import added_lines


def func_end(lines: list[str], start: int) -> int:
    """Return the 1-based line of the closing brace of the top-level func declared at start."""
    for i in range(start - 1, len(lines)):
        if lines[i].rstrip() == "}" or (i == start - 1 and lines[i].rstrip().endswith("}")):
            return i + 1
    return start


def dead_funcs(module: str) -> list[tuple[str, int, int, str]]:
    """Return (path, line, col, name) for each function deadcode finds unreachable in the module."""
    try:
        out = subprocess.run(
            ["deadcode", "-test", "-json", "./..."],
            cwd=module, capture_output=True, text=True, check=False, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if out.returncode != 0:
        return []
    try:
        pkgs = json.loads(out.stdout or "[]")
    except json.JSONDecodeError:
        return []
    found = []
    for pkg in pkgs:
        for fn in pkg.get("Funcs") or []:
            pos = fn["Position"]
            path = pos["File"] if os.path.isabs(pos["File"]) else os.path.join(module, pos["File"])
            found.append((os.path.realpath(path), pos["Line"], pos["Col"], fn["Name"]))
    return found


def main() -> int:
    """Print the touched unreachable functions; always return 0."""
    root, base, module = os.path.realpath(sys.argv[1]), sys.argv[2], os.path.realpath(sys.argv[3])
    changed = added_lines(root, base)
    for path, line, col, name in dead_funcs(module):
        if path not in changed or not path.startswith(module + os.sep):
            continue
        touched = changed[path]
        if touched is not None:
            with open(path, encoding="utf-8", errors="replace") as f:
                end = func_end(f.read().splitlines(), line)
            if not touched & set(range(line, end + 1)):
                continue
        print(f"{os.path.relpath(path, module)}:{line}:{col}: unreachable func: {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
