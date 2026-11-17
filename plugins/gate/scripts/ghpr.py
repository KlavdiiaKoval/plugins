"""Shared helpers for the commit and PR hooks: find `gh pr create` in a Bash command, run git, find the base."""

from __future__ import annotations

import functools
import os
import re
import subprocess
from pathlib import Path

from shellparse import Command, commands, normalize

GH_FLAGS_WITH_ARG = {"-R", "--repo", "--hostname"}
GIT_FLAGS_WITH_ARG = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
HISTORY_COMMANDS = ("commit", "merge", "cherry-pick", "am", "rebase", "pull", "revert", "reset")
NAME = re.compile(r"[A-Za-z0-9._-]+")
HOST = re.compile(r"[A-Za-z0-9.-]+")


def gh_subcommand(words: list[str]) -> list[str]:
    """Return the words after `gh` with its global flags removed."""
    rest = words[1:]
    out = []
    i = 0
    while i < len(rest):
        if rest[i] in GH_FLAGS_WITH_ARG:
            i += 2
        elif rest[i].split("=", 1)[0] in GH_FLAGS_WITH_ARG:
            i += 1
        else:
            out.append(rest[i])
            i += 1
    return out


def is_pr_create(words: list[str]) -> bool:
    """Report whether a simple command is `gh pr create` or its alias `gh pr new`."""
    if not words or os.path.basename(words[0]) != "gh":
        return False
    return gh_subcommand(words)[:2] in (["pr", "create"], ["pr", "new"])


def git_subcommand(words: list[str]) -> str:
    """Return the git subcommand of a simple command, or "" when it is not git."""
    if not words or os.path.basename(words[0]) != "git":
        return ""
    i = 1
    while i < len(words) and words[i].startswith("-"):
        i += 2 if words[i] in GIT_FLAGS_WITH_ARG else 1
    return words[i] if i < len(words) else ""


def pr_create_dir(command: str, cwd: str) -> str | None:
    """Return the directory `gh pr create` runs in, or None when the command has none.

    Follows `cd` steps before it. Falls back to cwd when that directory does not exist or the
    command cannot be parsed.
    """
    try:
        parts = commands(command, cwd)
    except ValueError:
        return cwd if re.search(r"\bgh\s+pr\s+(create|new)\b", command) else None
    for cmd in parts:
        if is_pr_create(cmd.words):
            return cmd.cwd if Path(cmd.cwd).is_dir() else cwd
    return None


def commits_before_pr_create(command: str) -> bool:
    """Report whether the command makes a commit before its `gh pr create` step."""
    try:
        parts: list[Command] = commands(command, ".")
    except ValueError:
        head = re.split(r"\bgh\s+pr\s+(?:create|new)\b", normalize(command), maxsplit=1)[0]
        verbs = "|".join(HISTORY_COMMANDS)
        return re.search(rf"(?m)(?:^|[;&|(])\s*(?:\w+=\S*\s+)*(?:rtk\s+)?git\s+(?:-\S+\s+)*(?:{verbs})\b", head) is not None
    for cmd in parts:
        if is_pr_create(cmd.words):
            return False
        if git_subcommand(cmd.words) in HISTORY_COMMANDS:
            return True
    return False


def trusted_orgs() -> list[tuple[str, str]]:
    """Return the (host, owner) pairs from the trusted_orgs option, lowercased.

    Each comma or space separated entry is `owner` (on github.com) or `host/owner`. Malformed entries are skipped.
    """
    raw = os.environ.get("CLAUDE_PLUGIN_OPTION_TRUSTED_ORGS", "")
    out = []
    for item in raw.replace(",", " ").split():
        host, _, owner = item.rpartition("/") if "/" in item else ("github.com", "", item)
        if "/" not in host and HOST.fullmatch(host) and NAME.fullmatch(owner):
            out.append((host.lower(), owner.lower()))
    return out


def is_trusted_owner(repo: str) -> bool:
    """Report whether a gh `-R` value ([HOST/]OWNER/REPO) names a repo in a trusted org."""
    parts = repo.lower().split("/")
    if len(parts) == 2:
        parts = ["github.com", *parts]
    return len(parts) == 3 and (parts[0], parts[1]) in trusted_orgs()


@functools.cache
def is_trusted_repo(cwd: str) -> bool:
    """Report whether cwd is in a git repo with a remote in a trusted org (ssh or https)."""
    orgs = trusted_orgs()
    if not orgs:
        return False
    pattern = "|".join(rf"{re.escape(h)}[:/]+{re.escape(o)}/" for h, o in orgs)
    return re.search(pattern, git(["remote", "-v"], cwd), re.IGNORECASE) is not None


def git(args: list[str], cwd: str) -> str:
    """Run git and return stdout, or "" on any failure."""
    try:
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def merge_base(root: str) -> str:
    """Return the merge-base commit with the default branch, or "" when there is none."""
    ref = git(["symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"], root).split()
    for name in [*ref, "origin/main", "origin/master", "main", "master"]:
        if sha := git(["merge-base", "HEAD", name], root).strip():
            return sha
    return ""
