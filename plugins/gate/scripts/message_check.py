#!/usr/bin/env python3
"""PreToolUse hook on Bash: check commit messages and PR titles and bodies against the git-conventions skill.

Exit 2 sends the problems back to Claude; exit 0 lets the command run. A command with no
message to read (an editor commit, `gh pr create --fill`) passes.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import prose
import rejections
from ghpr import gh_subcommand, git, is_trusted_owner, is_trusted_repo, merge_base
from offswitch import ON, is_off
from shellparse import commands

SUBJECT_MAX = 72
VAGUE = {"wip", "fix", "fixes", "update", "updates", "changes", "misc", "stuff", "tmp", "test"}
TYPES = ("feat", "fix", "chore", "docs", "refactor", "test", "ci", "perf", "build", "revert", "style")
CONVENTIONAL = re.compile(r"^(?P<type>[a-z]+)(?:\([a-z0-9._/-]+\))?(?P<critical>!)?: (?P<desc>\S.*)$")
TICKET_KEY = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
REVIEW_FIX = re.compile(r"\b(?:address(?:ed|es)? (?:the )?(?:review|comments|feedback)|review (?:comments|feedback)|fix(?:ed|es)? (?:lint|tests?|typos?|ci|the build))\b", re.IGNORECASE)
AUTOSQUASH = ("fixup!", "squash!", "amend!")
HEADING = re.compile(r"(?m)^#{1,6} ")
TESTED = re.compile(r"(?m)^Tested:")
RISK = re.compile(r"(?m)^Risk:")
# Patterns run on a diff line without its leading "+" or "-".
DEP_LINES = {
    "go.mod": re.compile(r"^\s*(?:require\s+)?([\w.\-]+\.[\w.\-/]+)\s+v\d"),
    "package.json": re.compile(r'^\s*"(@?[\w.\-/]+)"\s*:\s*"[^"]+"'),
    "requirements.txt": re.compile(r"^\s*([A-Za-z][\w.\-]{1,})\s*(?:\[[\w,\s-]*\])?\s*(?:[><=~!;]|$)"),
}
# pyproject entries: `"requests>=2",` or `"requests",` in a list, `requests = "^2"` in a poetry table.
PY_LIST_ENTRY = re.compile(r'^\s*"([A-Za-z][\w.\-]*)(?:\[[\w,\s-]*\])?\s*(?:[><=~!\^;][^"]*)?"\s*,?\s*$')
PY_TABLE_ENTRY = re.compile(r'^\s*([A-Za-z][\w.\-]*)\s*=\s*["{]')
TOML_SECTION = re.compile(r"^\s*\[+([^\]]+)\]+\s*$")
TOML_ARRAY_OPEN = re.compile(r"^\s*([A-Za-z_][\w.\-]*)\s*=\s*\[[^\]]*$")
DEP_KEY = re.compile(r"dependenc(?:y|ies)|requires")
NOT_DEPS = {"version", "name", "description", "requires-python", "python"}


def flag_values(words: list[str], short: str, long: str) -> list[str]:
    """Return every value given to a flag, in order, as `-m x`, `-mx`, `--message x` or `--message=x`."""
    values = []
    for i, word in enumerate(words):
        if word in (short, long) and i + 1 < len(words):
            values.append(words[i + 1])
        elif word.startswith(long + "="):
            values.append(word.split("=", 1)[1])
        elif short and word.startswith(short) and len(word) > len(short) and not word.startswith("--"):
            values.append(word[len(short) :])
    return values


def read_file(path: str, cwd: str) -> str | None:
    """Return a message file's text, or None when it is stdin or unreadable."""
    if path == "-":
        return None
    try:
        return (Path(cwd) / Path(path).expanduser()).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def commit_message(words: list[str], cwd: str) -> str | None:
    """Return the message a `git commit` will use, or None when it opens an editor or reuses one."""
    i = 1
    while i < len(words) and words[i].startswith("-") and words[i] != "commit":
        i += 2 if words[i] in ("-C", "-c") else 1
    if i >= len(words) or words[i] != "commit":
        return None
    args = words[i + 1 :]
    messages = [m for m in flag_values(args, "-m", "--message")]
    # A bundled short flag such as -am carries the message in the next word.
    for j, word in enumerate(args):
        if re.fullmatch(r"-[a-z]*m", word) and word not in ("-m",) and j + 1 < len(args):
            messages.append(args[j + 1])
    if messages:
        return "\n\n".join(messages)
    files = flag_values(args, "-F", "--file")
    return read_file(files[0], cwd) if files else None


def subject_problems(subject: str, prefix: str) -> list[tuple[str, str]]:
    """Return (rule, reason) pairs for a commit subject or PR title that is not a typed conventional subject."""
    problems = []
    match = CONVENTIONAL.match(subject)
    if not match or match.group("type") not in TYPES:
        problems.append((f"{prefix}-type", f'start with a type ({", ".join(TYPES)}), like "fix(sync): retry failed syncs".'))
        return problems
    desc = match.group("desc")
    if desc.lower().rstrip(".!") in VAGUE:
        problems.append((f"{prefix}-vague", f'"{desc}" says nothing. Say what the change does, in the imperative.'))
    if desc.endswith("."):
        problems.append((f"{prefix}-period", "the subject ends with a period. Drop it."))
    return problems


def commit_problems(message: str) -> list[tuple[str, str]]:
    """Return (rule, reason) pairs for a commit message that breaks the conventions."""
    lines = message.strip("\n").split("\n")
    subject = lines[0].strip()
    problems = []
    if len(subject) > SUBJECT_MAX:
        problems.append(("commit-subject-length", f"the subject is {len(subject)} characters. Keep it to {SUBJECT_MAX}."))
    problems += subject_problems(subject, "commit")
    if len(lines) > 1 and lines[1].strip():
        problems.append(("commit-blank-line", "put a blank line between the subject and the body."))
    match = CONVENTIONAL.match(subject)
    if match and match.group("critical") and not "".join(lines[1:]).strip():
        problems.append(("commit-critical-body", 'a "!" commit needs a body: what changes, and who notices.'))
    return problems


def branch_problems(cwd: str) -> list[tuple[str, str]]:
    """Return (rule, reason) pairs for branch commits that are not one logical change each."""
    base = merge_base(cwd)
    subjects = git(["log", "--format=%s", f"{base}..HEAD"], cwd).splitlines() if base else []
    problems = []
    seen: set[str] = set()
    for subject in subjects:
        if subject.startswith(AUTOSQUASH):
            problems.append(("branch-fixup", f'squash "{subject}" into the commit it fixes.'))
        elif subject in seen:
            problems.append(("branch-repeat", f'two commits say "{subject}". Make them one commit.'))
        elif REVIEW_FIX.search(subject):
            problems.append(("branch-review-fix", f'fold "{subject}" into the commit it fixes.'))
        elif not CONVENTIONAL.match(subject) or CONVENTIONAL.match(subject).group("type") not in TYPES:
            problems.append(("branch-type", f'reword "{subject}" with a type, like "fix(sync): retry failed syncs".'))
        seen.add(subject)
    if problems:
        problems.append(("branch-howto", f"squash with `git reset --soft {base[:12]}` and new commits, then push with --force-with-lease."))
    return problems


def pr_parts(words: list[str], cwd: str) -> tuple[str, str, str | None, str | None] | None:
    """Return (verb, target, title, body) for `gh pr create|new|edit`, or None for other commands."""
    if not words or os.path.basename(words[0]) != "gh":
        return None
    rest = gh_subcommand(words)
    if rest[:1] != ["pr"] or len(rest) < 2 or rest[1] not in ("create", "new", "edit"):
        return None
    args = rest[2:]
    titles = flag_values(args, "-t", "--title")
    bodies = flag_values(args, "-b", "--body")
    body = bodies[0] if bodies else None
    if body is None and (files := flag_values(args, "-F", "--body-file")):
        body = read_file(files[0], cwd)
    target = next((a for a in args if a.isdigit() or a.startswith("http")), "")
    return rest[1], target, titles[0] if titles else None, body


def pr_diff(verb: str, target: str, words: list[str], cwd: str) -> str:
    """Return the PR's diff: the real PR diff for edit, the branch against its base for create."""
    if verb == "edit":
        repo = flag_values(words, "-R", "--repo")
        args = ["gh", "pr", "diff", *([target] if target else []), *(["-R", repo[0]] if repo else [])]
        try:
            out = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=20, check=False)
        except (OSError, subprocess.SubprocessError):
            return ""
        return out.stdout if out.returncode == 0 else ""
    base = merge_base(cwd)
    return git(["diff", "--unified=0", base, "HEAD"], cwd) if base else ""


class PyprojectPlace:
    """Track whether pyproject diff lines sit in a dependency list or table, from the lines seen."""

    def __init__(self) -> None:
        """Start with no section known."""
        self.section_deps = False
        self.array_deps: bool | None = None

    def see(self, text: str) -> None:
        """Update the place from one line: a hunk header's context or a diff line's text."""
        if m := TOML_SECTION.match(text):
            self.section_deps, self.array_deps = bool(DEP_KEY.search(m.group(1))), None
        elif m := TOML_ARRAY_OPEN.match(text):
            self.array_deps = bool(DEP_KEY.search(m.group(1))) or self.section_deps
        elif text.strip() == "]":
            self.array_deps = None

    def dep(self, text: str) -> str | None:
        """Return the dependency a line names here, or None."""
        if self.array_deps and (m := PY_LIST_ENTRY.match(text)):
            return m.group(1)
        if self.array_deps is None and self.section_deps and (m := PY_TABLE_ENTRY.match(text)):
            return m.group(1)
        return None


def new_deps(diff: str) -> set[str]:
    """Return direct dependencies the diff adds to a known manifest file.

    A name the diff also removes from the same file is a version change, not a new dependency.
    """
    added: set[tuple[str, str]] = set()
    removed: set[tuple[str, str]] = set()
    current, place = "", PyprojectPlace()
    for line in diff.splitlines():
        if line.startswith(("+++ ", "--- ")):
            current = Path(line[4:]).name if line.startswith("+++") else current
            place = PyprojectPlace()
            continue
        if line.startswith("@@"):
            place = PyprojectPlace()
            place.see(line.rpartition("@@")[2])
            continue
        sign, text = line[:1], line[1:]
        if current == "pyproject.toml":
            name = place.dep(text) if sign in "+-" else None
            place.see(text)
        elif (pattern := DEP_LINES.get(current)) and not text.rstrip().endswith("// indirect"):
            name = m.group(1) if (m := pattern.match(text)) else None
        else:
            name = None
        if sign in "+-" and name and name.lower() not in NOT_DEPS:
            (added if sign == "+" else removed).add((current, name.lower()))
    return {name for _, name in added - removed}


def pr_problems(title: str | None, body: str | None, diff: str) -> list[tuple[str, str]]:
    """Return (rule, reason) pairs for a PR title and body that break the conventions."""
    problems = []
    if title is not None:
        problems += subject_problems(title, "pr-title")
        if ticket_key_required() and not TICKET_KEY.search(title):
            problems.append(("pr-title-key", 'put the ticket key in the title, like "fix(sync): retry failed syncs (ABC-123)".'))
        if len(title) > SUBJECT_MAX:
            problems.append(("pr-title-length", f"the title is {len(title)} characters. Keep it to {SUBJECT_MAX}."))
        problems += prose.title_problems(title)
    if body is None:
        return problems
    problems += prose.body_problems(body)
    if HEADING.search(body):
        problems.append(("pr-body-heading", "no section headings. Write plain paragraphs."))
    if not RISK.search(body):
        problems.append(("pr-body-risk", "add a `Risk:` line: what could break, who would notice, how to roll back."))
    if not TESTED.search(body):
        problems.append(("pr-body-tested", "end with a `Tested:` line that names the commands you ran."))
    unnamed = sorted(d for d in new_deps(diff) if d.split("/")[-1] not in body.lower())
    if unnamed:
        problems.append(("pr-new-dependency", f"name each new dependency and why it is needed: {', '.join(unnamed)}."))
    return problems


def ticket_key_required() -> bool:
    """Report whether the ticket_key option asks for a ticket key in PR titles."""
    return os.environ.get("CLAUDE_PLUGIN_OPTION_TICKET_KEY", "").lower() in ON


def in_trusted(words: list[str], cwd: str) -> bool:
    """Report whether the team rules apply: the command runs in, or names with -R, a repo in a trusted org."""
    repos = flag_values(words, "-R", "--repo")
    return any(is_trusted_owner(r) for r in repos) or is_trusted_repo(cwd)


def check(command: str, cwd: str) -> list[tuple[str, str]]:
    """Return every convention problem in the commit and PR steps of a Bash command.

    Only commands that run in a trusted repo are checked. A command that cannot be parsed
    passes: bash would refuse it too.
    """
    try:
        steps = commands(command, cwd)
    except ValueError:
        return []
    problems = []
    for cmd in steps:
        words, here = cmd.words, cmd.cwd
        if not words:
            continue
        if os.path.basename(words[0]) == "git" and (message := commit_message(words, here)) is not None:
            if in_trusted(words, here):
                problems += commit_problems(message)
        elif (pr := pr_parts(words, here)) is not None and in_trusted(words, here):
            verb, target, title, body = pr
            diff = pr_diff(verb, target, words, here) if body is not None else ""
            problems += pr_problems(title, body, diff)
            if verb in ("create", "new"):
                problems += branch_problems(here)
    return problems


def report(problems: list[tuple[str, str]], cwd: str) -> int:
    """Log the problems and print them for Claude; return the hook's exit code."""
    if not problems:
        return 0
    rejections.record("message", sorted({rule for rule, _ in problems}), cwd)
    print("Fix the message and run it again (see the gate:git-conventions skill):", file=sys.stderr)
    for _, reason in problems:
        print(f"- {reason}", file=sys.stderr)
    return 2


def run(payload: object) -> int:
    """Check the commit and PR messages in a parsed Bash hook payload; return 2 with the problems on stderr. Fails open."""
    if is_off("GATE") or not isinstance(payload, dict):
        return 0
    try:
        command = (payload.get("tool_input") or {}).get("command") or ""
        if not re.search(r"\b(git|gh)\b", command):
            return 0
        cwd = payload.get("cwd") or os.getcwd()
        problems = check(command, cwd)
    except Exception:  # noqa: BLE001 - a broken check must not block every command.
        return 0
    return report(problems, cwd)


def main() -> int:
    """Read a Bash hook payload on stdin and check its messages; exit 2 with the problems. Fails open."""
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    return run(payload)


if __name__ == "__main__":
    sys.exit(main())
