# Shared modules

The hook entry points in `../hooks/scripts/` call these. None of them is wired to an event directly.

| File | What it does |
| --- | --- |
| `guard_bash.py` | Refuses force pushes, pushes to main and any command that names a secrets file. |
| `message_check.py` | Checks commit messages, PR titles and PR bodies in repos of the trusted orgs. |
| `prose.py` | The PR readability rules: no dashes or stock phrases, short sentences and paragraphs, a line cap. |
| `pr_gate.py` | At `gh pr create`, runs the tests check and then the AI review. |
| `tests_check.py` | Refuses a PR when a Go, Python or TypeScript project's code changed but none of its tests did. |
| `review_check.py` | Asks a second Claude, with no tools, to review the branch diff against the conventions skills. |
| `snapshot.py` | Records which files are dirty before a command runs. |
| `shellparse.py` | Splits a shell command into the commands it really runs, through wrappers, `bash -c` and heredocs. |
| `ghpr.py` | Finds `gh pr create` in a command and runs the git lookups the PR checks need. |
| `new_issues.py` | Keeps only lint issues on lines that changed. |
| `dead_edits.py` | Lists the changed Go functions that nothing calls from main or tests. |
| `rejections.py` | Logs every refusal by rule. `rejections.py report 30` sums the last 30 days. |
| `offswitch.py`, `lib.sh` | Read the off switches. `lib.sh` also checks for a trusted repo and finds the merge base. |
