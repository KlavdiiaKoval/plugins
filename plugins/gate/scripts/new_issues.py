#!/usr/bin/env python3
"""Filter linter output on stdin to issues on lines added since a base commit.

Usage: new_issues.py <repo-root> <base-or-HEAD> [--baseline FILE]. Reads `path:line:...`
lines and prints those whose line was added or changed. Exits 1 when any remain, or when
no row has a location at all (the linter itself failed), printing the input.

`vet:` rows are type errors, so they are kept wherever they are. With --baseline, a row
is new when its file and message (line numbers ignored) occur more often than in FILE,
the same check's output at the base commit. A changed signature then still fails on
callers in files the change never touched.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections import Counter

LOCATION = re.compile(r"^(?P<path>[^:\s][^:]*?):(?P<line>\d+):")
HUNK = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@")


def added_lines(root: str, base: str) -> dict[str, set[int] | None]:
    """Map each changed file (absolute path) to the line numbers added since base, or None when every line is new."""
    out = subprocess.run(
        ["git", "-c", "core.quotePath=false", "diff", "-U0", "--no-ext-diff", "--no-color",
         "--src-prefix=a/", "--dst-prefix=b/", base, "--"],
        cwd=root, capture_output=True, text=True, check=False,
    ).stdout
    lines: dict[str, set[int] | None] = {}
    current = None
    for row in out.splitlines():
        if row.startswith("+++ "):
            name = row[4:].rstrip("\t")
            if name.startswith('"') and name.endswith('"'):
                name = name[1:-1]
            current = os.path.join(root, name[2:]) if name.startswith("b/") else None
            if current:
                lines.setdefault(current, set())
        elif current and (m := HUNK.match(row)):
            start, count = int(m.group(1)), int(m.group(2) or "1")
            lines[current].update(range(start, start + count))
    untracked = subprocess.run(
        ["git", "ls-files", "-z", "--others", "--exclude-standard"],
        cwd=root, capture_output=True, text=True, check=False,
    ).stdout
    for name in filter(None, untracked.split("\0")):
        lines[os.path.join(root, name)] = None
    return lines


def key(row: str) -> str:
    """Return the row without its line and column, so moved code still matches."""
    return re.sub(r"^([^:\s][^:]*?):\d+:(?:\d+:)?", r"\1:", row.strip())


def beyond_baseline(rows: list[str], baseline: list[str]) -> list[str]:
    """Return the rows whose key occurs more often than in the baseline."""
    left = Counter(key(r) for r in baseline if r.strip())
    kept = []
    for row in rows:
        if not row.strip():
            continue
        k = key(row)
        if left[k] > 0:
            left[k] -= 1
        else:
            kept.append(row)
    return kept


def main() -> int:
    """Print the rows on stdin that are new since the base; return 1 when any remain."""
    root, base = os.path.realpath(sys.argv[1]), sys.argv[2]
    rows = sys.stdin.read().splitlines()
    if len(sys.argv) > 4 and sys.argv[3] == "--baseline":
        with open(sys.argv[4]) as f:
            kept = beyond_baseline(rows, f.read().splitlines())
        print("\n".join(kept))
        return 1 if kept else 0
    cwd = os.path.realpath(os.getcwd())
    added = added_lines(root, base)
    located = [(row, m) for row in rows if (m := LOCATION.match(row))]
    if not located and any(row.strip() for row in rows):
        print("\n".join(rows))
        return 1
    kept = [row for row in rows if row.startswith("vet: ")]
    for row, m in located:
        path = os.path.realpath(os.path.join(cwd, m.group("path")))
        if path in added and (added[path] is None or int(m.group("line")) in added[path]):
            kept.append(row)
    print("\n".join(kept))
    return 1 if kept else 0


if __name__ == "__main__":
    sys.exit(main())
