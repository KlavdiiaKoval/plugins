# gate

A Claude Code plugin that stops Claude from handing back code that fails your checks.

Claude writes code fast, but it skips the boring parts. It mutes a lint error instead of fixing it. It says "done" while a test fails. `gate` checks the work at each step and sends problems straight back to Claude, so you only see code that builds and passes lint and tests.

## What it checks

| When | What happens |
| --- | --- |
| A session starts | Claude gets a "write the test first" rule and the conventions skill for each language in the repo. |
| Before an edit | Edits that mute a check are refused: a bare `//nolint`, `_ = err`, `@ts-ignore`, `# noqa` without a code. Skipping a test or changing lint config needs your OK. |
| Before a command | Refused: force pushes (use `--force-with-lease`), pushes to `main` or `master`, and any command that reads a secrets file such as `.env` or `*.pem`. |
| After an edit | The file is formatted and linted: Go with golangci-lint, Python with ruff, and TypeScript with the repo's eslint in trusted repos. |
| Claude finishes its answer | Each changed repo must build, pass lint and, in trusted repos, pass its tests. If not, Claude gets one more try to fix it. |
| `git commit`, `gh pr create` | Commits use Conventional Commits, one logical change each. PR bodies open with a one-sentence bottom line, then `Why:`, `What changes:`, `Risk:` and `Tested:`. |
| `gh pr create` | Changed code needs a changed test. A second Claude reviews the diff for serious problems. |

Only new problems count. Old lint issues in your repo don't block Claude.

## Install

```
/plugin marketplace add KlavdiiaKoval/plugins
/plugin install gate@klavdiiakoval
```

Needs `git`, `jq` and `python3`. Each check skips quietly when its tool is missing: `go` and `golangci-lint` v2 for Go, the repo's own eslint, tsc, vitest or jest for TypeScript, and `ruff` and `pytest` for Python. `govulncheck` and `deadcode` add Go checks when installed.

## Settings

Set these in `/config`, under the gate plugin.

| Setting | What it does |
| --- | --- |
| `trusted_orgs` | Your team's git orgs, like `acme` for github.com/acme or `gitlab.com/acme`. Commit and PR rules apply only in their repos. Steps that run the repo's own code run only there too: tests, `node_modules/.bin`, `.venv/bin` and `.claude/gate.sh`. Other repos still get build, vet and lint. |
| `ticket_key` | Require a ticket key like `ABC-123` in PR titles. Off by default. |
| `gate_off`, `guard_off`, `tests_off`, `review_off` | Turn off all checks, the edit and command guards, the tests check or the AI review. |

For one session, start Claude Code with `CLAUDE_GATE_OFF=1` (or `GUARD`, `TESTS`, `REVIEW`). To skip the tests check for one PR, add a `No test: <why>` line to the PR body.

## Your own gate

A trusted repo can replace the build, lint and test steps with its own executable script at `.claude/gate.sh`. It runs when Claude finishes an answer. A non-zero exit sends its output back to Claude.

## See what keeps failing

Every refusal is logged with its rule and repo, never code. To see the top rules from the last 30 days:

```
python3 ~/.claude/plugins/cache/klavdiiakoval/gate/<version>/scripts/rejections.py report 30
```

A rule that keeps failing is worth teaching: put it in a conventions skill or your repo's `CLAUDE.md`.

## Skills and agents

- `git-conventions`: how to write commit messages, PR titles and PR descriptions, with a diagram when a flow changes.
- `linear-tickets`: when to file a Linear ticket, a short bottom-line top for people and Agent notes for agents. A Linear save that breaks the shape is refused; teams without Linear never hit the check.
- `go-conventions`, `ts-conventions`, `python-conventions`: opinionated defaults for writing code. The AI review reads them too.
- `go-reviewer` and `security-reviewer` agents.

## Change it

Run the tests after any change:

```
bash plugins/gate/tests/hooktest.sh
```

## License

MIT
