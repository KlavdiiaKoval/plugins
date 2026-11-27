---
name: git-conventions
description: Use when writing a git commit message, a PR title or a PR description, when squashing a branch before a PR, or when a commit or `gh pr create` was refused by the message check. Covers Conventional Commits types, the "!" marker for critical changes, one logical change per commit, and the PR body (why, what changes, risk, tested).
---

A reviewer reads the PR description first and `git log` for years after. Write both for someone who has never seen this code. Plain words, short sentences.

## Commits

**One logical change per commit.** A reviewer should be able to read each commit alone. Three commits that do the same thing ("fix sync", "fix sync again", "address review") are one commit. Before `gh pr create`, squash fixups into the commit they fix:

```
git reset --soft $(git merge-base HEAD origin/main)   # then commit again, one commit per logical change
git push --force-with-lease                            # only on your own branch
```

Don't mix a refactor with a behavior change. Split them, refactor first.

**Subject:** `<type>(<scope>): <what it does>`, imperative, lowercase after the colon, at most 72 characters, no period. The scope is optional.

| Type | Use for |
| --- | --- |
| `feat` | New behavior a user or caller can see |
| `fix` | A bug fix |
| `refactor` | Same behavior, better code |
| `perf` | Same behavior, faster |
| `test` | Tests only |
| `docs` | Docs only |
| `build`, `ci` | Build files, CI config |
| `chore` | Upkeep: dependency bumps, config, cleanup |
| `revert` | Undo an earlier commit |

**Critical changes get a `!`.** Put it before the colon when the commit changes critical logic: money, auth or permissions, data writes or deletes, migrations, a public API or a contract other services rely on. Example: `fix(billing)!: charge once per invoice`. A `!` commit must have a body that says what changes and who notices. Use `BREAKING CHANGE: <what callers must do>` in the body when callers have to change.

**Body:** optional for small commits. Leave a blank line after the subject. Say why. The diff already shows what.

## PR title

The same shape as a commit subject, plus the ticket key when your team uses one: `fix(sync): retry failed syncs (ABC-123)`. A squash merge turns the title into the commit on `main`, so it must read well in `git log`. Use `!` when any commit in the PR has one.

## PR description

Bottom line up front (BLUF), then four labeled blocks. A reviewer gets the point from line one and scans the rest by label.

1. **Bottom line.** One plain sentence: what this PR does and what it fixes. No label.
1. **`Why:`** The problem, for whom, in one or two sentences a newcomer understands.
1. **`What changes:`** One bullet per behavior change, even a small one. Then only what the diff doesn't show: a design choice, a trade-off, what you left out on purpose. Name each new dependency and why the standard library is not enough.
1. **`Risk:`** What could break, who would notice, and how to roll back. Use bullets when there is more than one point. Be concrete: "a sync that keeps failing now takes 5 tries before it alerts" beats "low risk". If nothing changes for users, say that.
1. **`Tested:`** the commands you ran.

Add an `After deploy:` line only when someone must act after the merge. Link the ticket and related PRs. No file lists, no restating the diff, no history beyond a PR link, no labels like "low risk" or "blast radius" without the facts behind them.

Add a diagram when a picture says it faster than bullets: a new or changed flow between services, a state machine, a data path, or a dependency that changes direction. Put it in a ```` ```mermaid ```` block right after `What changes:`, use the names from the code, keep it to about 15 nodes, and add one line under it that says what to look at. Skip it for small or single-file changes. The checks skip code blocks, so a diagram never counts toward the line cap.

Make it easy to read:

- One fact per bullet, at most two sentences.
- Short sentences, one idea each, at most 30 words.
- Plain words. Say "retries" not "implements a resilience mechanism". Put code names in backticks.
- No headings, bold, tables or emoji. The labels are the structure.
- No em or en dashes as punctuation (a dash between numbers is fine) and no stock phrases such as "that said" or "it's worth noting".
- At most 45 non-blank lines.
- Before you post, read it once as someone new to the code.

Example:

```
Failed syncs now retry with backoff, so a warehouse 503 no longer leaves customers with stale data.

Why: a 503 from the warehouse fails the whole sync, and data stays stale until the next day's run.

What changes:
- A failed sync retries after 1, 2, 4, 8 and 16 seconds.
- After the fifth try it goes to the dead-letter queue, as before.
- Syncs that already succeed don't change.

Risk:
- A sync that keeps failing alerts about 30 seconds later than now.
- Roll back by reverting this PR. There is no migration.

Tested: go test ./sync/...
```

## What the hook checks

`message_check.py` runs before `git commit` and `gh pr create`, `gh pr new` and `gh pr edit`. It refuses:

- a commit subject without a known type, over 72 characters, ending in a period, or vague ("fix: wip")
- a body with no blank line after the subject, and a `!` commit with no body
- at `gh pr create`: branch commits that are fixups, repeats, review or lint fixes, or untyped
- a PR title without a type, without a ticket key (when the `ticket_key` option is on), over 72 characters, or with an em or en dash
- a PR body that doesn't open with a one-sentence bottom line, has a heading, misses a `Why:`, `What changes:`, `Risk:` or `Tested:` line, or doesn't name a new dependency
- a PR body with an em or en dash as punctuation, a stock phrase, a sentence over 30 words, a paragraph over five sentences, a list item over two, or over 45 non-blank lines. Code blocks and code spans are skipped.

Editor commits and `gh pr create --fill` are not checked. Whether the text is clear is still up to you: the hook only checks shape and length.
