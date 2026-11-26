#!/usr/bin/env python3
"""PreToolUse hook on Bash, Read and Grep: block force pushes, pushes to main, and reading secrets files.

A force push with --force-with-lease is allowed. A command that names a secrets file is denied
whatever the program, except a few that only look at the file (ls, test, stat...) or write it
(the target of cp or `>`). Set CLAUDE_GUARD_OFF=1, or guard_off in /config, to skip.
"""

from __future__ import annotations

import fnmatch
import glob
import itertools
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import rejections
from offswitch import is_off
from ghpr import GIT_FLAGS_WITH_ARG
from shellparse import Command, commands, loose_commands

MAIN = {"main", "master"}
PUSH_VALUE_FLAGS = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}
SAFE_PROGRAMS = {"ls", "test", "[", "[[", "touch", "stat", "echo", "printf"}
COPY_PROGRAMS = {"cp", "mv", "ln", "install"}
INTERPRETERS = re.compile(r"^(python[0-9.]*|node|nodejs|deno|bun|ruby|perl|php)$")
SAFE_ENV = {
    ".env.example",
    ".env.sample",
    ".env.template",
    ".env.dist",
    ".env.defaults",
}
SECRET_NAMES = {
    ".envrc",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    ".git-credentials",
    ".netrc",
    "credentials",
    "credentials.json",
}
SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
NOT_KEY_FILES = ("keybinding", "keymap")
# Names a glob such as `.e?v` or `id_*` is matched against, whether or not the file exists.
SECRET_SAMPLES = (*SECRET_NAMES, ".env", ".env.local", ".env.production", "anthropic_key.sh", "sa-key.json")
GLOB_CHARS = set("*?[")
FRAGMENT_SPLIT = re.compile(r"[=@:;,]")
CODE_SPLIT = re.compile(r"[^\w.~/*?\[\]$-]+")
MAX_GLOB_MATCHES = 2000
FORCE = "Don't force push. Use --force-with-lease if you rebased your own branch, or push a new commit."
TO_MAIN = "Don't push to main or master. Push a branch and open a draft PR."
SECRET = "Don't read secrets files into the conversation. Ask the user, or read the .env.example."


def is_secret(path: str) -> bool:
    """Report whether a path names a secrets file, by its file name."""
    name = Path(path.rstrip("/")).name
    lower = name.lower()
    if name in SAFE_ENV:
        return False
    return (
        name == ".env"
        or name.startswith((".env.", "anthropic_key"))
        or name in SECRET_NAMES
        or name.endswith(SECRET_SUFFIXES)
        or (fnmatch.fnmatchcase(lower, "*key*.json") and not lower.startswith(NOT_KEY_FILES))
    )


def glob_names_secret(pattern: str, cwd: str) -> bool:
    """Report whether a glob could name a secrets file: by its shape, or by what it matches in cwd."""
    name = Path(pattern).name
    if name[:1] not in GLOB_CHARS and any(fnmatch.fnmatchcase(s, name) for s in SECRET_SAMPLES):
        return True
    matches = glob.iglob(os.path.join(cwd, pattern))
    return any(is_secret(m) for m in itertools.islice(matches, MAX_GLOB_MATCHES))


def path_names_secret(path: str, cwd: str) -> bool:
    """Report whether one path, or glob, names a secrets file."""
    path = os.path.expanduser(path)
    if GLOB_CHARS & set(path):
        return glob_names_secret(path, cwd)
    return is_secret(path)


def word_names_secret(word: str, cwd: str) -> bool:
    """Report whether a shell word names a secrets file, also inside `@path`, `if=path` or `REV:path`.

    A word with a blank in it is text, such as a commit message, not a path.
    """
    if not word or any(ch.isspace() for ch in word):
        return False
    return any(path_names_secret(part, cwd) for part in {word, *FRAGMENT_SPLIT.split(word)} if part)


def code_names_secret(code: str, cwd: str) -> bool:
    """Report whether inline interpreter code, such as `python3 -c "open('.env')"`, names a secrets file."""
    return any(path_names_secret(part, cwd) for part in CODE_SPLIT.split(code) if part)


def reads_secret(cmd: Command) -> bool:
    """Report whether a command could read a secrets file: it names one, whatever the program.

    Safe programs (ls, test, touch, echo...) and the destination of cp or mv are allowed, and so
    is writing a secrets file through `>`.
    """
    if any("<" in op and op != "<<<" and word_names_secret(target, cmd.cwd) for op, target in cmd.redirects):
        return True
    if not cmd.words:
        return False
    prog = os.path.basename(cmd.words[0])
    if prog in SAFE_PROGRAMS:
        return False
    words = cmd.words[:-1] if prog in COPY_PROGRAMS else cmd.words
    if any(word_names_secret(w, cmd.cwd) for w in words if not (prog in COPY_PROGRAMS and w.startswith("-"))):
        return True
    return bool(INTERPRETERS.match(prog)) and any(code_names_secret(w, cmd.cwd) for w in cmd.words[1:])


def grep_names_secret(tool_input: dict, cwd: str) -> bool:
    """Report whether a Grep call targets a secrets file through its path or its glob filter."""
    path, pattern = tool_input.get("path") or "", tool_input.get("glob") or ""
    return bool(path and path_names_secret(path, cwd)) or bool(pattern and path_names_secret(pattern, cwd))


def branch(cwd: str | None) -> str:
    """Return the current branch in cwd, or "" when it can't be found."""
    if not cwd or "$" in cwd or not Path(cwd).is_dir():
        return ""
    result = subprocess.run(
        ["git", "-C", cwd, "branch", "--show-current"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def check_push(args: list[str], cwd: str | None) -> str | None:
    """Return a reason to block a git push, given the words after `push`."""
    refspecs, i = [], 0
    while i < len(args):
        arg = args[i]
        if arg in PUSH_VALUE_FLAGS:
            i += 2
            continue
        if arg in ("--force", "--mirror") or re.match(r"^-[a-zA-Z]*f[a-zA-Z]*$", arg):
            return FORCE
        if arg == "--all":
            return TO_MAIN
        if not arg.startswith("-"):
            refspecs.append(arg)
        i += 1
    refspecs = refspecs[1:]
    if any(r.startswith("+") for r in refspecs):
        return FORCE
    current = branch(cwd)
    targets = []
    for refspec in refspecs:
        source, _, dest = (
            refspec.rpartition(":") if ":" in refspec else ("", "", refspec)
        )
        name = dest or source
        targets.append(
            current if name in ("HEAD", "@") else name.removeprefix("refs/heads/")
        )
    if not refspecs:
        targets = [current]
    return TO_MAIN if any(t in MAIN for t in targets) else None


def git_cwd(words: list[str], cwd: str) -> tuple[str, int]:
    """Return the directory git runs in after its -C flags, and the index of its subcommand."""
    i = 1
    while i < len(words) and words[i].startswith("-"):
        if words[i] == "-C" and i + 1 < len(words):
            cwd = str(Path(cwd) / os.path.expanduser(words[i + 1]))
            i += 2
        elif words[i] in GIT_FLAGS_WITH_ARG:
            i += 2
        else:
            i += 1
    return cwd, i


def check_git(words: list[str], cwd: str) -> tuple[str, str] | None:
    """Return (rule, reason) to block a git push, or None."""
    cwd, i = git_cwd(words, cwd)
    if i < len(words) and words[i] == "push":
        reason = check_push(words[i + 1 :], cwd)
        if reason:
            return ("force-push" if reason == FORCE else "push-main", reason)
    return None


def check_command(command: str, cwd: str) -> tuple[str, str] | None:
    """Return (rule, reason) to block a shell command, or None.

    A command shlex cannot split is checked word by word instead of waved through.
    """
    try:
        parts = commands(command, cwd)
    except ValueError:
        parts = loose_commands(command, cwd)
    for cmd in parts:
        if cmd.words and os.path.basename(cmd.words[0]) == "git" and (found := check_git(cmd.words, cmd.cwd)):
            return found
        if reads_secret(cmd):
            return ("read-secret", SECRET)
    return None


def decide(payload: dict) -> dict | None:
    """Return the hook's JSON reply, or None to let the call through."""
    if is_off("GATE", "GUARD"):
        return None
    tool_input = payload.get("tool_input") or {}
    cwd = payload.get("cwd") or os.getcwd()
    tool = payload.get("tool_name")
    if tool == "Read":
        found = ("read-secret", SECRET) if is_secret(tool_input.get("file_path") or "") else None
    elif tool == "Grep":
        found = ("read-secret", SECRET) if grep_names_secret(tool_input, cwd) else None
    else:
        found = check_command(tool_input.get("command") or "", cwd)
    if not found:
        return None
    rejections.record("guard", [found[0]], cwd)
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": found[1],
        }
    }


def main() -> int:
    """Read the hook payload on stdin and print a deny reply when the call is blocked. Fails open."""
    try:
        payload = json.load(sys.stdin)
        reply = decide(payload) if isinstance(payload, dict) else None
    except Exception:  # noqa: BLE001 - a broken guard must not block every command.
        return 0
    if reply:
        print(json.dumps(reply))
    return 0


if __name__ == "__main__":
    sys.exit(main())
