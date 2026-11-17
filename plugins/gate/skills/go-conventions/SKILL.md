---
name: go-conventions
description: Use when writing, reviewing or refactoring Go code. Covers interfaces, errors, concurrency, naming and test style for a senior Go engineer.
paths:
  - "**/*.go"
  - "**/go.mod"
  - "**/go.sum"
---

Write to a senior Go level. Baseline is the toolchain: assume gofmt, goimports, go vet and golangci-lint pass, and do not restate rules a linter enforces. For anything not covered here, follow Go Code Review Comments and the Google Go Style Guide.

- Accept interfaces, return concrete types. Define an interface in the package that consumes it, and only once a real second implementation or test seam needs it. No interface for one impl.
- No premature abstraction: no speculative generics or config knobs, no indirection until a real second case exists. Duplicate twice before extracting.
- One job per function. Split out a block that has its own job and isn't tied to the caller's control flow. Don't extract for its own sake.
- Errors: wrap with %w and add context ("verb noun: %w"). Handle or return, not both (no log-and-return). Do not panic in library code.
- Layers: follow the repo's layering and keep dependencies pointing inward. Domain and core packages never import server, transport, db or adapter packages; they define the interfaces those implement. If the repo has a `depguard` rule, it is the source of truth.
- Keep the zero value useful; avoid constructors that only fill defaults.
- Concurrency: every goroutine has a clear owner and exit path; ctx is the first arg and you honor cancellation; never copy a sync type. Avoid the goroutine if you can.
- Naming: short, no stutter (tracing.Config, not tracing.TracingConfig). Consistent 1-2 char receivers.
- Tests: table-driven with subtests; t.Parallel() where safe; prefer small fakes over mock frameworks; assert behavior, not call counts.
