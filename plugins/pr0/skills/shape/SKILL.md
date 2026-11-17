---
name: shape
description: "Use when starting a feature that will take more than one PR, or when the user asks for the shape, skeleton, structure, interfaces or contracts of a feature before implementation. Produces \"PR 0\": compilable types, interfaces and stub scaffolding plus a design doc, so a human can agree on modules, interfaces and contracts before any logic is written."
---

The goal is a PR a reviewer can read in 15 minutes and say "yes, this is the shape". Shape only. No logic, no tests of logic, no wiring.

## Builds on

This skill adds the PR 0 parts on top of three skills from `mattpocock/skills` (MIT). Load each one at the step named below. If one is missing, give the user its install command, then go on with the rule written in that step.

- `codebase-design`: deep modules, seams and adapters. Use its vocabulary and principles to shape the interfaces (step 4).
- `domain-modeling`: the repo's `GLOSSARY.md` and ADRs. Read them in step 1. The doc's Terms come from the glossary, and new terms go into it. Do not contradict an ADR; offer one for a decision that is hard to undo.
- `grilling`: the interview in step 3, limited to questions that change the shape.

Install: `agr add -g mattpocock/skills/<name>`. The repo is public, so no token is needed. If it fails with "Authentication failed", a `GITHUB_TOKEN` or `GH_TOKEN` in the shell can't read it: run it again with `env -u GITHUB_TOKEN -u GH_TOKEN`.

## Steps

Load each reference file at the step that names it.

1. Read the repo layout first and follow it. Do not invent layers or folders.
   - Module boundaries, the existing layers (for example domain, app, server, db, adapters), and how other repos import this one.
   - The closest existing feature of the same kind. Mirror its files: folder, file names, type and function naming, where its routes, handlers, workers and tests live, and how they are registered.
   - Where docs live and the repo's doc rules (folder, front matter, required sections). Put the design doc there and follow those rules on top of the sections in [references/design-doc.md](references/design-doc.md).
   - The repo's own instructions (`CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING.md`) and its PR title style.
   - The domain language and past decisions: `GLOSSARY.md` and `docs/adr/` (`domain-modeling`).
   - If the feature is small, say which layers it skips and why, in one line.
2. Read the linked tickets and their comments. Every requirement or concern there shows up in the doc.
3. Ask the user only what changes the shape: which modules are touched, who calls whom, sync or async. Use `grilling` for the format: one round of questions, each with your recommended answer. Check where the feature's trigger really lives: if it starts in another repo, say so before building. Everything else, decide and show it.
4. Write the scaffolding: types, interfaces, shared contracts, entry points, error kinds, stub bodies with doc comments. Load a conventions skill for the language if one is installed, and follow [references/scaffolding.md](references/scaffolding.md).
5. Write the design doc in the repo's docs folder (or `DESIGN.md` next to the code if there is none). Follow [references/design-doc.md](references/design-doc.md) for its sections and writing rules.
6. Add an import rule so domain packages cannot import adapter, server or db packages: `depguard` in `.golangci.yml`, `no-restricted-imports` in eslint, or `import-linter` contracts in Python. Skip if one already covers it. If CI does not run that linter, the rule does nothing: tell the user instead of adding it.
7. Make every diagram readable in light and dark viewers, and render each one to check it. Follow [references/diagrams.md](references/diagrams.md).
8. Before the PR, hand the doc to a fresh subagent that has seen none of this work. Give it only the doc path and the repo, and ask it to check every claim (page text, limits, retry counts, status codes, routes, schedules, package arrows) against the code and list each wrong one with `file:line`. Fix what it finds. Self-checks miss errors that a fresh read catches.
9. Before the first push, check whether a branch push or a merge deploys or publishes anywhere (read the CI workflow triggers, including release automation like release-please). If a push does, run build and lint locally, then push with `[skip ci]` in the commit message or ask the user first. PR 0 has no behavior to deploy or release.
10. Open the PR as a draft, titled `feat(<scope>): shape <feature> (PR 0)`, plus the ticket key if the repo uses one. Keep it to 72 characters. If the repo has its own title style, use that instead (for example `chore(<scope>): ...` where release tooling reads titles). Description below.

## PR description

Google's CL description style, plain prose with no section headings:

- First: the problem, and why this PR is only types and stubs. Say "Start with the doc, then read the doc comments, which are the contracts."
- Then: the later PRs (one line each: what it fills), that nothing is registered, routed or wired, any one-line real bodies, and every module pin with its commit, if any.
- Then a `Risk:` line. For PR 0 it is usually that nothing changes for users, since nothing is wired.
- Then a `Tested:` line with the commands that pass (Go: build, vet, `go test -short`, golangci-lint with `GOWORK=off`; TS: `tsc --noEmit`, eslint, tests; Python: type check, ruff, tests). No new lint findings.


## After PR 0 merges

The design doc becomes a frozen record. Follow [references/after-merge.md](references/after-merge.md) when a later PR changes a decision or a PR 0 signature.

## Do not

- Write implementation, SQL (migrations included), templates or test cases. Test files only for fakes a port needs, or stub methods an existing fake needs to compile.
- Explain design principles in the doc. State the decision, not the theory.
- Add "Path to running", "PR plan", "Corner cases" or "Design notes" sections to the doc. The PR description lists the later PRs, and corner cases and decisions go into the steps.
