#!/usr/bin/env python3
"""PreToolUse hook: review the branch before `gh pr create` opens the PR.

Covers what linters cannot: naming, readability, testability, duplicated intent,
and the conventions skill for each language in the diff.
Blocks only on high-severity findings. Set CLAUDE_REVIEW_OFF=1, or review_off in /config, to skip.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import rejections
from ghpr import git, merge_base, pr_create_dir
from offswitch import is_off

MAX_DIFF_CHARS = 60_000
MODEL = os.environ.get("CLAUDE_REVIEW_MODEL", "sonnet")
TIMEOUT = 120
SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

RUBRIC = """You are reviewing a diff. Report only what a senior engineer would block a PR on.

Look for, in this order:
1. Correctness and security bugs reachable from the changed lines.
2. Names that do not say what the thing is or does.
3. The same intent implemented twice in different shapes.
4. Code too tangled to test without mocks.
5. Code that needs a comment to be understood.
6. An import that crosses the repo's layers, such as domain or core code importing a
   server, transport, db or adapter package. Judge the layers from the repo's folders.
7. A commit that changes money, auth, a migration or a public API, and has no `!` after
   its type in the subject, such as "fix(billing)!: ...". Kind "convention", high.

Ignore anything a formatter or linter already catches: spacing, import order,
line length, missing types, unused variables.
"""

CONVENTIONS_HEADER = """
The team's conventions follow. A clear break of one is "high". A judgment
call on one is "medium".
"""

OUTPUT = """
Reply with JSON only, no prose:
{"findings":[{"severity":"high|medium|low","kind":"bug|security|naming|duplicate|untestable|needs-comment|layering|convention","file":"path","line":0,"what":"one sentence","fix":"one sentence"}]}
Use "high" only when you would block the merge. Empty list if nothing qualifies.

Diff:
"""

LANG_BY_EXT = {
    ".go": "go",
    ".ts": "ts",
    ".tsx": "ts",
    ".js": "ts",
    ".jsx": "ts",
    ".mjs": "ts",
    ".cjs": "ts",
    ".py": "python",
}


def languages(diff: str) -> list[str]:
    """Return the conventions languages for the files changed in the diff."""
    found = set()
    for path in re.findall(r"^diff --git a/\S+ b/(\S+)$", diff, re.MULTILINE):
        lang = LANG_BY_EXT.get(Path(path).suffix)
        if lang:
            found.add(lang)
    return sorted(found)


def skill_body(lang: str) -> str:
    """Return a language's conventions skill text without its frontmatter, or "" when it is missing."""
    try:
        text = (SKILLS_DIR / f"{lang}-conventions" / "SKILL.md").read_text()
    except OSError:
        return ""
    return re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.DOTALL).strip()


def conventions(langs: list[str]) -> str:
    """Return the body of each language's conventions skill, frontmatter removed."""
    parts = [f"\n## {lang}\n{body}\n" for lang in langs if (body := skill_body(lang))]
    return CONVENTIONS_HEADER + "".join(parts) if parts else ""


def fit(diff: str, limit: int = MAX_DIFF_CHARS) -> tuple[str, list[str]]:
    """Keep whole file diffs while they fit in limit; return the kept text and the paths left out."""
    kept, left_out, size = [], [], 0
    for chunk in re.split(r"(?m)^(?=diff --git )", diff):
        if not chunk:
            continue
        if size + len(chunk) <= limit:
            kept.append(chunk)
            size += len(chunk)
        else:
            m = re.match(r"diff --git a/\S+ b/(\S+)", chunk)
            left_out.append(m.group(1) if m else "?")
    return "".join(kept), left_out


def prompt(diff: str, subjects: str = "") -> str:
    """Build the review prompt for the diff and commit subjects, naming any files too big to include."""
    body, left_out = fit(diff)
    if left_out:
        body += "\n[Not shown, the diff was too big: " + ", ".join(left_out) + ". Review only what is shown.]\n"
    commits = f"\nCommit subjects:\n{subjects.strip()}\n" if subjects.strip() else ""
    return RUBRIC + conventions(languages(body)) + commits + OUTPUT + body


def diff_for(root: str) -> str:
    """Return the branch diff against the default branch, or the uncommitted diff when there is no base."""
    base = merge_base(root)
    flags = ["diff", "--no-color", "--no-ext-diff", "--src-prefix=a/", "--dst-prefix=b/"]
    return git([*flags, base, "HEAD"], root) if base else git([*flags, "HEAD"], root)


def subjects_for(root: str) -> str:
    """Return the subjects of the branch's commits, one per line, or "" when there is no base."""
    base = merge_base(root)
    return git(["log", "--no-merges", "--format=%s", f"{base}..HEAD"], root) if base else ""


def review(diff: str, subjects: str = "") -> tuple[list[dict], str]:
    """Run the reviewer with no tools; return its findings, or [] and the reason the review did not run."""
    env = {**os.environ, "CLAUDE_GATE_OFF": "1"}
    try:
        result = subprocess.run(
            ["claude", "-p", "--model", MODEL, "--tools", "", "--strict-mcp-config"],
            input=prompt(diff, subjects),
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return [], f"the reviewer took longer than {TIMEOUT}s"
    except (OSError, subprocess.SubprocessError) as e:
        return [], f"the reviewer could not start ({type(e).__name__})"
    if result.returncode != 0:
        tail = result.stderr.strip().splitlines()[-1:] or [""]
        return [], f"the reviewer exited {result.returncode}: {tail[0][:200]}"
    match = re.search(r"\{.*\}", result.stdout, re.DOTALL)
    try:
        findings = json.loads(match.group(0)).get("findings") if match else None
    except (json.JSONDecodeError, AttributeError):
        findings = None
    if not isinstance(findings, list):
        return [], "the reviewer reply had no findings list"
    return [f for f in findings if isinstance(f, dict)], ""


def skipped(reason: str) -> None:
    """Tell the user the review did not run, without blocking the PR."""
    print(json.dumps({"systemMessage": f"PR review skipped: {reason}."}))


def check(payload: dict) -> int:
    """Return 2 and print the findings when the review finds a blocking issue, else 0."""
    if is_off("GATE", "REVIEW"):
        return 0
    command = (payload.get("tool_input") or {}).get("command") or ""
    root = pr_create_dir(command, payload.get("cwd") or os.getcwd())
    if root is None:
        return 0
    diff = diff_for(root)
    if not diff.strip():
        return 0
    findings, problem = review(diff, subjects_for(root))
    if problem:
        skipped(problem)
        return 0

    blocking = [f for f in findings if f.get("severity") == "high"]
    if not blocking:
        return 0

    rejections.record("review", sorted({str(f.get("kind") or "unknown") for f in blocking}), root)
    print("Review found blocking issues:", file=sys.stderr)
    for f in blocking:
        where = f"{f.get('file', '?')}:{f.get('line', 0)}"
        print(f"- {where}: {f.get('what', '')} Fix: {f.get('fix', '')}", file=sys.stderr)
    return 2


def main() -> int:
    """Read the hook payload on stdin and run the check. Fails open on bad input or a crash."""
    try:
        payload = json.load(sys.stdin)
        return check(payload) if isinstance(payload, dict) else 0
    except Exception:  # noqa: BLE001 - a broken check must not block the PR.
        return 0


if __name__ == "__main__":
    sys.exit(main())
