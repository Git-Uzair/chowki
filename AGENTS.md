# Repository Operating Contract for Agents

This document defines the binding rules and commands for any AI agent or developer operating in the `chowki` repository.

## 1. Product Name & Terminology

- Product and package name must be `chowki` everywhere (Python package `chowki`, Node scope `@chowki/*`).
- The legacy term "check" + "point" is strictly banned across all code, tests, identifiers, docstrings, and prose.
- Enforced automatically by `scripts/check_layout.py`.

## 2. Build, Test, and Lint Commands

All commands are executed from the repo root:

| Purpose | Command |
|---|---|
| Install / sync | `uv sync --all-extras --dev` |
| Unit + property tests | `uv run pytest python/chowki/tests/unit -q` |
| Integration tests | `uv run pytest python/chowki/tests/integration -q` |
| Benchmarks | `uv run pytest python/chowki/tests/benchmarks --benchmark-only -q` |
| Full test sweep | `uv run pytest python/chowki/tests -q` |
| Lint | `uv run ruff check .` |
| Format check | `uv run ruff format --check .` |
| Type check (primary) | `uv run pyright` |
| Type check (secondary) | `uv run mypy python/chowki/src` |

## 3. Directory Layout Rules

- Polyglot monorepo structure driven by `spec/` schemas.
- Python packages live under `python/<pkg>/src/<pkg>`.
- Python tests live under `python/<pkg>/tests/{unit,integration,benchmarks}`.
- Language-neutral protocol schemas live under `spec/v1/`.
- No language-specific source files or packages at the repo root.

## 4. Serialization Rules

- State serialization uses `msgspec` Structs in MessagePack format.
- `pickle`, `cloudpickle`, `eval`, `exec`, and `__reduce__` hooks are strictly forbidden anywhere in the codebase.

## 5. Performance Rule

- Any change to a module under `chowki/state/` or `chowki/core/` must be accompanied by a run of the benchmark suite.
- Changes must not regress the execution budgets defined in `python/chowki/tests/benchmarks/budgets.py`.

## 6. Test-Driven Development (TDD) Rule

- Write the failing test first, confirm it fails for the expected reason, and then write implementation code.

## 7. Workflow Side Effects Rule

- Every side effect in a Chowki workflow must live inside a `@chowki.step`. Because `resume()` re-executes the workflow function body from the top, any side effect outside a `@chowki.step` will be re-executed on warm resume.

## 8. Documentation Currency Rules

- `docs/features.md` is the feature catalog and SDK parity matrix. Any task that ships or changes a feature updates its row there **in the same commit**.
- Wire-format changes additionally update `docs/research/07-cross-sdk-parity.md` and the matching `spec/v1/` schema in the same commit.
- Plan documents in `docs/plans/` carry `**Status:**` markers per task; flip them as tasks complete. When a phase's plan is fully COMPLETED, delete the plan file and flip the phase to DONE in `docs/plans/00-roadmap.md` (see the roadmap's Working agreement).
