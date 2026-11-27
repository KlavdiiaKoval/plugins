#!/usr/bin/env python3
"""PreToolUse hook on Bash: run the tests check, then the review, before `gh pr create`.

One entrypoint so the slow review never starts when the message check or the tests check
already blocks. The message check reports its own problems; this hook only skips the rest.
"""

from __future__ import annotations

import json
import os
import sys

import message_check
import review_check
import tests_check
from ghpr import commands, commits_before_pr_create, is_pr_create, is_trusted_repo, pr_create_dir
from offswitch import is_off


def gate(payload: dict, messages_checked: bool = False) -> int:
    """Return the hook's exit code for one payload: 2 when a PR check blocks, else 0.

    Pass messages_checked=True when the caller already ran the message check and it passed.
    """
    command = (payload.get("tool_input") or {}).get("command") or ""
    cwd = payload.get("cwd") or os.getcwd()
    if pr_create_dir(command, cwd) is None:
        return 0
    if is_off("GATE") or not in_trusted_repo(command, cwd):
        return 0
    if commits_before_pr_create(command):
        print("This command commits and opens the PR in one step, so the PR checks cannot see the commit.", file=sys.stderr)
        print("Run the git step first, then `gh pr create` as its own command.", file=sys.stderr)
        return 2
    if not messages_checked and message_check.check(command, cwd):
        return 0
    return tests_check.check(payload) or review_check.check(payload)


def in_trusted_repo(command: str, cwd: str) -> bool:
    """Report whether the command's `gh pr create` runs in, or names with -R, a repo in a trusted org."""
    try:
        parts = commands(command, cwd)
    except ValueError:
        return is_trusted_repo(cwd)
    return any(is_pr_create(cmd.words) and message_check.in_trusted(cmd.words, cmd.cwd) for cmd in parts)


def main() -> int:
    """Read the hook payload on stdin and run the PR checks. Fails open on bad input or a crash."""
    try:
        payload = json.load(sys.stdin)
        return gate(payload) if isinstance(payload, dict) else 0
    except Exception:  # noqa: BLE001 - a broken gate must not block the PR.
        return 0


if __name__ == "__main__":
    sys.exit(main())
