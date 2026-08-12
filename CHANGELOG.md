# Changelog

All notable changes to the `chowki` project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Fixed

- **Step argument hashing no longer collapses complex objects.** msgspec Structs,
  dataclasses, Pydantic models (via `model_dump`), enums, bytes, datetimes, UUIDs,
  Decimals, and ordinary objects (via `__dict__`) are now expanded structurally before
  hashing. Two different instances of one class previously produced the same `args_hash`,
  so a step could return another call's memoised result. Values with no exposable
  structure still collapse to `<TypeName>` and now log `chowki_step_args_opaque`.
- **Workflow arguments are persisted and replayed.** The first call's arguments are
  stored (redacted) on the run record as `RunRecord.inputs` and replayed by `resume()`,
  `aresume()` and `rerun()`. Workflows with required parameters are resumable, and a
  defaulted parameter no longer silently rebinds its default on resume — the failure mode
  that re-ran steps against a different entity than the one a human approved.
- **Concurrent step entry inside one run raises `ChowkiConcurrencyError`** instead of
  interleaving ordinals and corrupting the RFC 6902 snapshot chain. Nested steps are
  unaffected; concurrent independent runs are unaffected.

### Changed

- **`args_hash` values change for steps that take complex arguments.** A run that is
  in flight across this upgrade will miss the memo for those steps, log
  `chowki_step_args_changed`, and re-execute them — including their side effects. **Drain
  or complete in-flight runs before upgrading.** Runs whose step arguments are primitives,
  dicts, lists, sets, or tuples are unaffected.
- Workflow arguments are redacted (best-effort, per the redactor's key-name, pattern,
  and entropy rules) before they are persisted. A recognized secret passed as a
  workflow argument replays as its `[REDACTED:...]` placeholder;
  `chowki_workflow_args_redacted` is logged when that happens. Tuples replay as lists.
- PyPI metadata: new `description`, searchable `keywords`, and AI/recovery/security
  Trove classifiers.
- `scripts/check_layout.py` allowlists `README.md`, `python/chowki/README.md`,
  `docs/comparison.md`, and `POSITIONING.md` for the competitor term, so the READMEs
  can name the concept in the reader's vocabulary. The ban is unchanged everywhere else.

### Added

- `chowki.ChowkiConcurrencyError` in the public API surface.
- Repository metadata (GitHub About box, topics, naming rule) recorded in
  `docs/user-guide/launch-checklist.md`.

## [0.1.0] - 2026-08-11

### Added

#### Phase 1 — Foundation & Execution Core
- **Execution core**: `@chowki.step` decorator with inputs/outputs recording, memoisation, snapshotting, per-step retry overrides, and idempotency claims.
- **Workflow runner**: `@chowki.workflow` wrapper managing run lifecycle (`PENDING`, `RUNNING`, `PAUSED`, `COMPLETED`, `FAILED`, `ABORTED`, `REJECTED`), context binding, and terminal state persistence.
- **State pipeline**: Hot-path pipeline combining multi-tier credential redaction (regex patterns, Shannon entropy scanning, key-name matching), `ref:sha256:` blob offloading (>4 KB), RFC 6902 JSON Patch delta compaction, MessagePack envelope serialization, AES-256-GCM encryption at rest, and RFC 8785 canonical JSON hashing.
- **Human-in-the-Loop (HITL)**: `chowki.pause()` pause gates, auto-pause on guardrail/breaker triggers, single-use HMAC-SHA256 base64url resume tokens with lifetime nonces, token reissuance (`reissue_token`), append-only audit log, and `ConsoleGateway`.
- **Guardrails**: Three-tier loop detection (windowed tool-call hashing, Levenshtein text similarity via `record_text`, delegation graph cycles via `record_transition`), step execution ceilings (`max_steps_per_run`), and token/cost budgets (`report_usage`) supporting OpenAI/Anthropic usage metrics.
- **Storage adapters**: `SQLiteStorage` default WAL backend with process-level locking and status/audit indices, and `MemoryStorage` for unit testing.
- **Telemetry & configuration**: `ChowkiConfig` / `ChowkiEngine` central configuration, structlog structured logging, and OpenTelemetry tracing and metrics integration.

#### Hardening Pass
- Hardened state pipeline argument sanitization against non-finite float (`NaN`, `Infinity`) and unencodable Unicode surrogates.
- Persisted HMAC secret slot isolation (`resume` vs `redaction` slots).
- Fixed replayability handling for non-encodable step execution results.

#### Phase 2 — Developer Experience & Release
- **Workflow registry & resume-by-name**: Global workflow registry auto-registering `@chowki.workflow` functions, enabling `resume()` and `chowki.rerun()` by workflow name string without passing function references.
- **Async-aware resume (`chowki.aresume`)**: Added awaitable `aresume()` for coroutine workflows and explicit `ChowkiConfigError` safety checks preventing unawaited coroutine execution in sync `resume()`.
- **Inspection API (`inspect_run`)**: Public `chowki.inspect_run()` returning frozen `RunInspection` structs (run record, steps in ordinal order, latest redacted state, audit trail, pause request, resumability flag) built on isolated throwaway snapshot pipelines.
- **CLI console script**: Installed `chowki` command-line interface supporting `runs list/show`, `resume` (sync/async), `reissue-token`, `release-step`, `complete-step`, `recover`, and `rerun` with `--db`, `--module/-m`, and `--json` flags.
- **Embedded approval endpoints**: Production guide (`docs/user-guide/resuming-in-production.md`) and runnable FastAPI recipe (`examples/python/fastapi_approvals.py`) with normative exception-to-HTTP mappings (401, 404, 409, 410, 202, 200).
- **User guide & documentation**: Comprehensive user guide covering core concepts, warm resume & Rule R4, guardrails, HITL, configuration & security, operational limits, and production web app integration.
- **Showcase agent**: Zero-waste agent showcase (`examples/python/agent_review.py`) demonstrating active token budget guardrails, HITL approval gates, CLI state patching, and process crash recovery without duplicate LLM calls.
- **Packaging & release engineering**: Automated wheel smoke testing (`scripts/wheel_smoke_test.py`), package metadata assertions (`test_package_metadata.py`), `py.typed` typing marker inclusion, and PyPI release CI workflow (`.github/workflows/release.yml`).

---

## Maintainers Release Runbook

This runbook outlines the required procedure for tagging and publishing official releases of `chowki` to PyPI via GitHub Actions.

### Pre-Release Verification

1. Ensure the working tree is clean and on the `main` branch.
2. Run full local CI verification:
   ```bash
   uv run python scripts/ci_local.py
   ```
3. Run the automated wheel smoke test:
   ```bash
   uv run python scripts/wheel_smoke_test.py
   ```
4. Confirm `CHANGELOG.md` contains an accurate summary of all changes under the target version section (e.g. `[0.1.0]`).

### Tagging & Publishing Process

1. Create a signed, annotated Git tag following Semantic Versioning (prefixed with `v`):
   ```bash
   git tag -a v0.1.0 -m "Release v0.1.0"
   ```
2. Push the tag to GitHub:
   ```bash
   git push origin v0.1.0
   ```
3. The GitHub Actions release workflow (`.github/workflows/release.yml`) will trigger automatically upon tag push.
4. The workflow performs OIDC Trusted Publisher authentication with PyPI, builds `sdist` and `wheel` targets via `uv build --package chowki`, and publishes packages to PyPI.
5. Verify the published release on PyPI at `https://pypi.org/project/chowki/`.
6. Test installing the published release in a clean environment:
   ```bash
   uv add chowki==0.1.0
   ```
