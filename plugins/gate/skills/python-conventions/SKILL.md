---
name: python-conventions
description: Use when writing, reviewing or refactoring Python code, including packaging a library. Covers typing, project layout, uv, dependencies and test style.
paths:
  - "**/*.py"
  - "**/pyproject.toml"
---

Baseline is the toolchain: assume ruff (lint and format) and mypy (strict) pass; do not restate what they enforce. Follow the repo's setup first.

- Type-hint public APIs and run mypy strict. Ship `py.typed` (PEP 561) for a typed library so consumers get the types.
- src/ layout. `pyproject.toml` with PEP 621 metadata; `uv` for env, lock and build; `uv_build` backend for pure Python, hatchling when you need extensions. Dev and test deps in `[dependency-groups]`, runtime feature extras in `[project.optional-dependencies]`.
- No upper bound on `requires-python`; set the floor to the real minimum.
- Layers: follow the repo's layering and keep dependencies pointing inward. Domain and core modules never import server, transport, db or adapter modules. If the repo has `import-linter` contracts, they are the source of truth.
- One job per function. Split out a block that has its own job and isn't tied to the caller's control flow. Don't extract for its own sake.
- Reach for the stdlib (pathlib, dataclasses, logging) before a dependency. Specific exceptions, never a bare `except`.
- Tests: pytest, behavior over mocks; small fakes over mock frameworks.
