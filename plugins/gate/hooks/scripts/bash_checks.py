#!/usr/bin/env python3
"""PreToolUse hook on Bash, Read and Grep: run the command guard, the message check, the PR gate and the gate snapshot in one process.

Each check used to start its own Python process on every Bash call. Order and output:
- the guard denies: print its JSON reply and stop.
- the message check finds problems: print them on stderr, exit 2 and skip the PR gate.
- else the PR gate runs (tests check and AI review on `gh pr create`) and its exit code is ours.
- when the call is allowed, take the snapshot `mark-bash.sh post` compares against. A denied
  call never reaches PostToolUse, so it needs none.
A Read or Grep payload only goes through the guard. Any internal error lets the call through.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))  # shared modules live in scripts/

import guard_bash
import message_check
import pr_gate
import snapshot


def guard(payload: dict) -> dict | None:
    """Return the guard's deny reply, or None. A crash in the guard lets the call through."""
    try:
        return guard_bash.decide(payload)
    except Exception:  # noqa: BLE001 - a broken guard must not block every command.
        return None


def pr_checks(payload: dict) -> int:
    """Return the PR gate's exit code; the message check already passed. A crash lets the PR through."""
    try:
        return pr_gate.gate(payload, messages_checked=True)
    except Exception:  # noqa: BLE001 - a broken gate must not block the PR.
        return 0


def snap(payload: dict) -> None:
    """Take the pre-call snapshot for the repo gate. A crash only skips marking the repo."""
    try:
        snapshot.take(payload)
    except Exception:  # noqa: BLE001 - a broken snapshot must not block the command.
        return


def dispatch(payload: object) -> int:
    """Run the checks for one parsed payload in order and return the hook's exit code."""
    if not isinstance(payload, dict):
        return 0
    reply = guard(payload)
    if reply:
        print(json.dumps(reply))
        return 0
    if payload.get("tool_name") in ("Read", "Grep"):
        return 0
    if message_check.run(payload):
        return 2
    rc = pr_checks(payload)
    if rc == 0:
        snap(payload)
    return rc


def main() -> int:
    """Read the hook payload on stdin once and run every check on it. Fails open on bad input."""
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    return dispatch(payload)


if __name__ == "__main__":
    sys.exit(main())
