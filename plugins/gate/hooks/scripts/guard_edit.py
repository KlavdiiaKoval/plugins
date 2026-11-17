#!/usr/bin/env python3
"""PreToolUse hook on Edit and Write: stop edits that silence a check instead of fixing the code.

Denies a new bare suppression (a nolint, noqa, type: ignore or eslint-disable with no rule
or reason, any ts-ignore) and `_ = err`. Asks the user before a new skipped test, an edit
to a generated file, or an edit to lint, type-check or gate config. Set CLAUDE_GUARD_OFF=1, or guard_off in /config, to skip.

`guard_edit.py scan <root> <base>` checks the lines added since base for the denied
patterns, so the gate also catches ones written through a shell command.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))  # shared modules live in scripts/

import rejections
from offswitch import is_off

GO = (".go",)
TS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
PY = (".py",)
SKIP_REASON = "This adds a skipped test. Allow it only if the skip is on purpose, not to hide a failure."
CONFIG_NAMES = {
    ".golangci.yml",
    ".golangci.yaml",
    ".golangci.toml",
    ".golangci.json",
    "ruff.toml",
    ".ruff.toml",
    "mypy.ini",
    ".mypy.ini",
}
TS_STRICT_KEYS = ("strict", "noImplicit", "noUnused", "noUnchecked", "noFallthrough", "skipLibCheck", "exactOptional")
SCANNED = set(GO + TS + PY)
MAX_SCAN_BYTES = 1_000_000
GENERATED = re.compile(r"^\s*(?://|#|/?\*).*(?:\bDO NOT EDIT\b|@generated\b)", re.MULTILINE)


@dataclass(frozen=True)
class Rule:
    name: str
    exts: tuple[str, ...]
    pattern: re.Pattern[str]
    decision: str
    reason: str


RULES = (
    Rule(
        "bare-nolint",
        GO,
        re.compile(r"//\s*nolint\b(?!:[\w,-]+\s+//\s*\S)"),
        "deny",
        "A //nolint needs the linter and a reason: //nolint:errcheck // why. Fix the code if you can.",
    ),
    Rule(
        "discarded-err",
        GO,
        re.compile(r"\b_\s*=\s*err\b"),
        "deny",
        "Don't discard an error with `_ = err`. Handle it or return it.",
    ),
    Rule("skipped-test", GO, re.compile(r"\bt\.Skip(?:f|Now)?\("), "ask", SKIP_REASON),
    Rule(
        "ts-ignore",
        TS,
        re.compile(r"@ts-(?:ignore|nocheck)\b"),
        "deny",
        "Don't use @ts-ignore or @ts-nocheck. Fix the type, or use @ts-expect-error with a reason.",
    ),
    Rule(
        "bare-eslint-disable",
        TS,
        re.compile(r"eslint-disable(?:-next-line|-line)?(?![\w-])(?!\s+[@\w/-]+(?:\s*,\s*[@\w/-]+)*\s+--\s+\S)"),
        "deny",
        "An eslint-disable needs the rule and a reason: // eslint-disable-next-line rule -- why.",
    ),
    Rule("skipped-test", TS, re.compile(r"\b(?:it|test|describe)\.skip\("), "ask", SKIP_REASON),
    Rule(
        "bare-noqa",
        PY,
        re.compile(r"#\s*noqa\b(?!:\s*[A-Z]+\d+)"),
        "deny",
        "A # noqa needs the rule code: # noqa: F401. Fix the code if you can.",
    ),
    Rule(
        "bare-type-ignore",
        PY,
        re.compile(r"#\s*type:\s*ignore\b(?!\[)"),
        "deny",
        "A # type: ignore needs the error code: # type: ignore[arg-type]. Fix the type if you can.",
    ),
    Rule(
        "skipped-test",
        PY,
        re.compile(r"pytest\.mark\.skip\b|unittest\.skip\b|pytest\.skip\("),
        "ask",
        SKIP_REASON,
    ),
)


def read(path: Path) -> str:
    """Return the file's text, or "" when it does not exist or can't be read."""
    try:
        return path.read_text(errors="ignore")
    except OSError:
        return ""


def changes(tool_input: dict, current: str) -> list[tuple[str, str]]:
    """Return (before, after) text pairs for an Edit, MultiEdit or Write."""
    if "content" in tool_input:
        return [(current, tool_input.get("content") or "")]
    if "edits" in tool_input:
        return [(e.get("old_string") or "", e.get("new_string") or "") for e in tool_input["edits"]]
    return [(tool_input.get("old_string") or "", tool_input.get("new_string") or "")]


def quoted(text: str, pos: int) -> bool:
    """Report whether pos sits inside a string literal on its line. Text after a comment starts is never quoted."""
    quote, i = "", text.rfind("\n", 0, pos) + 1
    while i < pos:
        c = text[i]
        if quote:
            if c == "\\":
                i += 1
            elif c == quote:
                quote = ""
        elif c in "\"'`":
            quote = c
        elif c == "#" or text.startswith("//", i):
            return False
        i += 1
    return bool(quote)


def hits(pattern: re.Pattern[str], text: str) -> int:
    """Count the matches of pattern in text that are not inside a string literal."""
    return sum(1 for m in pattern.finditer(text) if not quoted(text, m.start()))


def adds(pattern: re.Pattern[str], pairs: list[tuple[str, str]]) -> bool:
    """Report whether the edit leaves more matches of pattern than it found."""
    return sum(hits(pattern, a) - hits(pattern, b) for b, a in pairs) > 0


def touches(pairs: list[tuple[str, str]], words: tuple[str, ...]) -> bool:
    """Report whether any word appears in the text the edit replaces or writes."""
    return any(w in b or w in a for b, a in pairs for w in words)


def is_config(path: Path, pairs: list[tuple[str, str]]) -> bool:
    """Report whether the edit changes lint, type-check or gate config."""
    name = path.name
    if name in CONFIG_NAMES or name.startswith((".eslintrc", "eslint.config.")):
        return True
    if name == "gate.sh" and path.parent.name == ".claude":
        return True
    if name.startswith("tsconfig") and name.endswith(".json"):
        return touches(pairs, TS_STRICT_KEYS)
    if name == "pyproject.toml":
        return touches(pairs, ("ruff", "mypy"))
    if name == "setup.cfg":
        return touches(pairs, ("mypy", "flake8"))
    if name == "package.json":
        return touches(pairs, ("eslintConfig",))
    return False


def findings(path: Path, tool_input: dict) -> list[tuple[str, str, str]]:
    """Return (rule, decision, reason) for each guard the edit trips."""
    current = read(path)
    pairs = changes(tool_input, current)
    out = [(r.name, r.decision, r.reason) for r in RULES if path.suffix in r.exts and adds(r.pattern, pairs)]
    if current and GENERATED.search(current[:2048]):
        out.append(("generated-file", "ask", "This file is generated. Change its source and regenerate it instead."))
    if is_config(path, pairs):
        out.append(("lint-config", "ask", "This changes lint or gate config. Allow it only if the user asked for it."))
    return out


def decide(payload: dict) -> dict | None:
    """Return the hook's JSON reply, or None to let the edit through."""
    if is_off("GATE", "GUARD"):
        return None
    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path")
    if not file_path:
        return None
    path = Path(file_path)
    hits = findings(path, tool_input)
    if not hits:
        return None
    where = str(path.parent if path.parent.is_dir() else payload.get("cwd") or os.getcwd())
    rejections.record("guard", sorted({name for name, _, _ in hits}), where)
    decision = "deny" if any(d == "deny" for _, d, _ in hits) else "ask"
    reasons = list(dict.fromkeys(reason for _, _, reason in hits))
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": " ".join(reasons),
        }
    }


def added_text(root: str, base: str) -> dict[str, list[tuple[int, str]]]:
    """Map each file changed since base, and each untracked file, to its added (line, text) pairs."""
    out = subprocess.run(
        [
            "git",
            "-c",
            "core.quotePath=false",
            "diff",
            "-U0",
            "--no-ext-diff",
            "--no-color",
            "--dst-prefix=b/",
            base,
            "--",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    ).stdout
    added: dict[str, list[tuple[int, str]]] = {}
    current, line = None, 0
    for row in out.splitlines():
        if row.startswith("+++ "):
            name = row[4:].rstrip("\t")
            if name.startswith('"') and name.endswith('"'):
                name = name[1:-1]
            current = name[2:] if name.startswith("b/") else None
        elif row.startswith("@@") and current:
            line = int(row.split("+", 1)[1].split(",", 1)[0].split(" ", 1)[0])
        elif row.startswith("+") and current:
            added.setdefault(current, []).append((line, row[1:]))
            line += 1
    untracked = subprocess.run(
        ["git", "ls-files", "-z", "--others", "--exclude-standard"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    ).stdout
    for name in filter(None, untracked.split("\0")):
        path = Path(root) / name
        if path.suffix in SCANNED and path.is_file() and path.stat().st_size <= MAX_SCAN_BYTES:
            added[name] = list(enumerate(read(path).splitlines(), start=1))
    return added


def scan(root: str, base: str) -> list[str]:
    """Return `path:line: reason` for each denied pattern on a line added since base."""
    found = []
    for name, lines in sorted(added_text(root, base).items()):
        rules = [r for r in RULES if r.decision == "deny" and Path(name).suffix in r.exts]
        for number, text in lines:
            found += [f"{name}:{number}: {r.reason}" for r in rules if hits(r.pattern, text)]
    return found


def main() -> int:
    """Run `scan <root> <base>` for the repo gate, or check an Edit or Write hook payload on stdin. The hook fails open."""
    if len(sys.argv) == 4 and sys.argv[1] == "scan":
        if is_off("GUARD"):
            return 0
        found = scan(sys.argv[2], sys.argv[3])
        print("\n".join(found))
        return 1 if found else 0
    try:
        payload = json.load(sys.stdin)
        reply = decide(payload) if isinstance(payload, dict) else None
    except Exception:  # noqa: BLE001 - a broken guard must not block every edit.
        return 0
    if reply:
        print(json.dumps(reply))
    return 0


if __name__ == "__main__":
    sys.exit(main())
