#!/usr/bin/env python3
"""Record which check rejected Claude's work, and report the rules that keep firing.

    rejections.py log <gate> <fallback-rule> <dir>   reads the check's output on stdin
    rejections.py report [days]                      rule counts, most frequent first

Only gate names, rule ids and repo names are stored, never code.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

SECTION = re.compile(r"^--- (.+?) in (\S+)$", re.MULTILINE)
RULE_PATTERNS = (
    re.compile(r"^\S+:\d+:\d+: ([A-Z]+\d+)\b"),
    re.compile(r"error (TS\d+):"),
    re.compile(r"^\S+:\d+:\d+: .*\(([a-z][a-z0-9-]*)\)$"),
    re.compile(r"^\S+:\d+:\d+: .*\[([@\w/-]+)\]$"),
    re.compile(r"^--- (FAIL):"),
)


DEFAULT_DATA = Path.home() / ".claude" / "plugins" / "data" / "gate-klavdiiakoval"


def log_path() -> Path:
    """Return the log file in the plugin's data dir: CLAUDE_PLUGIN_DATA in a hook, the installed plugin's dir otherwise."""
    return Path(os.environ.get("CLAUDE_PLUGIN_DATA") or DEFAULT_DATA) / "rejections.jsonl"


def repo_name(where: str) -> str:
    """Return the name of the git repo that holds where, or the folder name."""
    try:
        root = subprocess.run(
            ["git", "-C", where, "rev-parse", "--show-toplevel"], check=False, capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        root = ""
    return Path(root or where).name


def rules_in(text: str) -> list[str]:
    """Return the distinct rule ids named in a check's output, in first-seen order."""
    found: dict[str, None] = {}
    for line in text.splitlines():
        for pattern in RULE_PATTERNS:
            m = pattern.search(line.strip())
            if m:
                found["go-test" if m.group(1) == "FAIL" else m.group(1)] = None
                break
    return list(found)


def sections(text: str, fallback: str, where: str) -> list[tuple[str, str, str]]:
    """Split gate-repo style output into (step, dir, body); other output is one section."""
    heads = list(SECTION.finditer(text))
    if not heads:
        return [(fallback, where, text)]
    out = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        out.append((m.group(1), m.group(2), text[m.end() : end]))
    return out


def record(gate: str, rules: list[str], where: str) -> None:
    """Append one line per rule to the log. Never raises: logging must not break a check."""
    if not rules:
        return
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: UP017 - datetime.UTC needs Python 3.11, and hooks may run on macOS python3 3.9.
        repo = repo_name(where)
        with path.open("a") as f:
            for rule in rules:
                f.write(json.dumps({"ts": ts, "gate": gate, "rule": rule, "repo": repo}) + "\n")
    except OSError:
        return


def log_output(gate: str, fallback: str, where: str, text: str) -> None:
    """Record the rules found in a check's output, using the step name when none are found."""
    for step, folder, body in sections(text, fallback, where):
        record(gate, rules_in(body) or [step or "unparsed"], folder)


def entries(days: int) -> list[dict]:
    """Return log entries from the last days days."""
    since = datetime.now(timezone.utc) - timedelta(days=days)  # noqa: UP017 - datetime.UTC needs Python 3.11, and hooks may run on macOS python3 3.9.
    out = []
    try:
        lines = log_path().read_text().splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            e = json.loads(line)
            if datetime.fromisoformat(e["ts"]) >= since:
                out.append(e)
        except (ValueError, KeyError, TypeError):
            continue
    return out


def report(days: int) -> str:
    """Return a table of gate and rule counts with the repos they fired in."""
    rows = entries(days)
    if not rows:
        return f"No rejections in the last {days} days ({log_path()})."
    counts: Counter[tuple[str, str]] = Counter()
    repos: dict[tuple[str, str], set[str]] = defaultdict(set)
    for e in rows:
        key = (e["gate"], e["rule"])
        counts[key] += 1
        repos[key].add(e.get("repo", "?"))
    lines = [f"Rejections in the last {days} days:", f"{'count':>5}  {'gate':<11} {'rule':<28} repos"]
    for (gate, rule), n in counts.most_common():
        lines.append(f"{n:>5}  {gate:<11} {rule:<28} {', '.join(sorted(repos[(gate, rule)]))}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    """Run the `log` or `report` subcommand; print usage and return 1 for anything else."""
    if len(argv) >= 4 and argv[0] == "log":
        log_output(argv[1], argv[2], argv[3], sys.stdin.read())
        return 0
    if argv and argv[0] == "report":
        print(report(int(argv[1]) if len(argv) > 1 else 30))
        return 0
    print(__doc__, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
