# Hooks

`hooks.json` wires each Claude Code event to one entry point in `scripts/`. Shared modules live in `../scripts/`.

| Entry point | Event | What it does |
| --- | --- | --- |
| `session_start.py` | SessionStart | Gives Claude the test-first rule and names the conventions skill for each repo language. |
| `guard_edit.py` | Before Edit, Write, MultiEdit | Refuses edits that silence a check, like a bare `//nolint` or `@ts-ignore`. |
| `bash_checks.py` | Before Bash, Read, Grep | Runs the command guard, the message check and the PR gate in one process, then takes a git snapshot. A Read or Grep runs only the secrets guard. |
| `gate-file.sh` | After Edit, Write, MultiEdit | Formats and lints the edited file. |
| `mark-bash.sh` | After Bash | Marks the repo for the end-of-answer gate when a command changed file content. |
| `gate-repo.sh` | Stop | Builds and lints each changed repo, and in trusted repos runs Go tests, pytest and vitest or jest. It also flags edits to Go functions nothing calls and runs `govulncheck` after a `go.mod` change. |

Every entry point fails open: if it crashes, the action goes through. Run `bash ../tests/hooktest.sh` after any change.
