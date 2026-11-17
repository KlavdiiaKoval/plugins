# After PR 0 merges

The design doc becomes a frozen record. Put this line under its title: "Frozen at PR 0. Code and its doc comments are the source of truth for contracts."

- Do not keep the contracts table or the package graph in sync with later PRs.
- If a decision changes, in a later PR or in a review or chat, add a sub-bullet to the step it affects saying what changed, where and why. Never rewrite or delete the old line.
- When an open question is answered, move the answer into the step it affects and remove the question.
- A later PR that changes a PR 0 signature says so in its description and links the changed step. Find those changes with a tool, not by reading: in Go, `apidiff -w pr0.export <import path>` on the PR 0 commit, then `apidiff pr0.export <import path>` on the branch (`golang.org/x/exp/cmd/apidiff`); in Python, `griffe check <package> -a <pr0 ref>`; in TypeScript, diff the `.d.ts` files `tsc --declaration --emitDeclarationOnly` writes on each side.
