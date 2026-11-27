---
name: ts-conventions
description: Use when writing, reviewing or refactoring TypeScript or Node code, including publishing a library to npm. Covers strict typing, ESM packaging, async rules and test style.
paths:
  - "**/*.ts"
  - "**/*.tsx"
  - "**/package.json"
  - "**/tsconfig*.json"
---

Baseline is the toolchain: assume tsc (strict), ESLint and Prettier pass; do not restate what they enforce. Follow the repo's setup first.

- Strict TypeScript. Do not reach for `any` to dodge a type; model it, or use `unknown` plus narrowing.
- Libraries: ESM-first; dual ESM+CJS only when real CJS consumers need it; build with a real tool (tsc/tshy/tsup), not a hand-rolled script. `exports` map with `types` first per branch, honest `sideEffects`, a `files` allowlist, `engines.node`. Verify with `publint` and `@arethetypeswrong/cli`. Do not bundle dependencies.
- Peer-depend on a singleton the host also loads (for example a plugin API kept on `globalThis`); a bundled second copy breaks the shared instance.
- Layers: follow the repo's layering and keep dependencies pointing inward. Domain and core modules never import server, transport, db or adapter modules. If the repo has a `no-restricted-imports` or boundaries rule, it is the source of truth.
- One job per function. Split out a block that has its own job and isn't tied to the caller's control flow. Don't extract for its own sake.
- async/await over raw promises and callbacks. No floating promises; handle rejections.
- Tests: a real runner (Vitest / node:test), behavior over mocks; mock only at true boundaries.

A library is not an app: when others import the code, respect packaging norms, correct dependency kinds (peer vs runtime), type markers, version floors, and a small public surface.

Every exported function and method gets a short, accurate, plain doc comment. For a shared library or SDK whose exported API is the product, write full doc comments on it. Inline comments only where the code might be unclear.
