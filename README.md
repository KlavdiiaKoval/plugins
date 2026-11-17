# pr0

A Claude Code skill that shapes a big feature before anyone writes the logic.

Ask Claude to shape a feature. It opens one small pull request, "PR 0", with:

- the types, interfaces and function signatures, each with a doc comment that states its contract
- stub bodies that compile but do nothing yet, for example `panic("TODO: PR 2")`
- a design doc with user stories, states, data model, diagrams and open questions

Nothing is wired up, so PR 0 is safe to merge. Your team agrees on the shape in one short review, and each later PR fills in the stubs.

## Install

```
/plugin marketplace add KlavdiiaKoval/pr0
/plugin install pr0@pr0
```

## Use

Just ask:

> Shape the invoice export feature before we build it.

Claude reads your repo, asks a few questions that change the design, then writes the code and the doc. It opens the PR as a draft.

## Works with

- Go, TypeScript and Python
- [mattpocock/skills](https://github.com/mattpocock/skills): `codebase-design`, `domain-modeling` and `grilling`. Optional. If they're missing, Claude tells you how to install them.
- [mermaid-cli](https://github.com/mermaid-js/mermaid-cli) (`mmdc`), so Claude can check that the diagrams render

## License

MIT
