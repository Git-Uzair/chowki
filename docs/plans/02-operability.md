# chowki — Phase 2 Plan: Python Operability & the Cross-SDK Spec

**Plan file:** `docs/plans/02-operability.md`
**Date:** 2026-08-11
**Covers:** Roadmap Phase 2 (`docs/plans/00-roadmap.md`) — nothing else.
**Sources of truth:** `docs/features.md` (feature rows marked 🔜 2),
`docs/research/07-cross-sdk-parity.md`, `docs/research/06-python-monorepo-standards.md`
(codegen/CI), amendments in `03-durable-execution.md` / `05-hitl-gateway.md`, `AGENTS.md`.

## Conventions binding on every task

- **TDD is mandatory** (`AGENTS.md` §6): write the named tests first, watch them fail
  for the stated reason, then implement.
- One commit per task (`feat(chowki):` / `fix(chowki):` / `docs(...)` prefixes), flip
  the task's `**Status:**` marker in this file in the same commit.
- Any change under `chowki/state/` or `chowki/core/` runs the benchmark suite; budgets
  in `python/chowki/tests/benchmarks/budgets.py` are non-negotiable (`AGENTS.md` §5).
- **Update `docs/features.md`** — flip the matching row 🔜→✅ when a task lands.
- Commands (install/test/lint/type/bench) are the table in `AGENTS.md` §2.
- `pyright` strict + `mypy --strict` + `ruff check` + `ruff format --check` +
  `scripts/check_layout.py` green before a task is done.
- Wire-format changes (Task 7) must update `docs/research/07-cross-sdk-parity.md` and
  the relevant `spec/v1/` schema in the same commit.

Task order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10. Tasks 5, 6, 9 are independent of
their neighbours and may be reordered; 8 must follow 6 and 7 (vectors freeze the wire).

---

## Task 1 — Workflow registry, resume-by-name, and `rerun`

**Status:** PENDING

**Goal:** callers stop passing workflow function references around; recovered runs
become re-runnable. Removes the `TypeError` heuristic in `core/resume.py`
(`_invoke_workflow`).

**Files:** new `python/chowki/src/chowki/core/registry.py`,
new `python/chowki/tests/unit/test_registry.py`; modified `core/runner.py`,
`core/resume.py`, `chowki/__init__.py`, `tests/unit/test_public_api.py`.

**Behavior:**
1. `registry.py`: process-global `dict[str, Callable[..., Any]]` with
   `register_workflow(name, fn)` (re-registering a different function under an
   existing name logs a `structlog` warning and replaces — test suites redefine
   workflows constantly), `get_workflow(name) -> Callable | None`,
   `registered_workflows() -> dict` (copy), `clear_registry()` (test isolation).
2. `@chowki.workflow` gains `register: bool = True` and registers the **wrapper**
   under the effective workflow name at decoration time.
3. `resume()` signature: `workflow_fn` becomes optional, new keyword
   `workflow: str | None = None`. Resolution order: explicit `workflow_fn` →
   `workflow` name via registry → `run.workflow` via registry. Unresolvable →
   `ChowkiConfigError` telling the caller to import the defining module or pass
   `workflow_fn`. `_invoke_workflow`'s message-sniffing fallback is deleted.
4. New public `chowki.rerun(run_id, *, engine=None)`: loads the run record, resolves
   its workflow from the registry, invokes it with `run_id=` (memoisation does the
   rest). This is what you call on `recover_runs()` output.

**Tests first** (`test_registry.py`): decoration registers; `register=False` opts out;
`resume(run_id, token, decision)` with **no** `workflow_fn` completes a paused run;
resolution by explicit `workflow=` name; unresolvable name raises `ChowkiConfigError`;
`rerun` completes a recovered PENDING run without re-executing memoised steps
(call-count assert); replacement warning fires once. Update the pinned public-API set
(`rerun` joins `__all__`).

**Done-when:** full unit suite green; `chowki.resume(run_id=..., token=...,
decision=...)` alone resumes a paused run in the quickstart example (update
`examples/python/quickstart.py` to drop its `workflow_fn=` argument).

---

## Task 2 — Inspection API: `inspect_run`

**Status:** PENDING

**Goal:** the "inspect" leg of the control-plane pitch: one call returning everything
about a run, without disturbing live pipeline state.

**Files:** new `python/chowki/src/chowki/core/inspection.py`,
new `python/chowki/tests/unit/test_inspection.py`; modified `chowki/__init__.py`,
`tests/unit/test_public_api.py`.

**Behavior:**
1. `RunInspection` (frozen `msgspec.Struct`): `run: RunRecord`,
   `steps: list[StepRecord]` (ordinal order), `state: JSONValue | None` (latest
   **redacted** state reconstructed from `snapshots_for_resume`; `None` when no
   snapshots), `audit: list[JSONObject]`, `pause: PauseRequest | None`,
   `resumable: bool` (status ∈ {PENDING, RUNNING, PAUSED}).
2. `chowki.inspect_run(run_id, *, engine=None) -> RunInspection`; unknown run raises
   `ChowkiStateError`.
3. Reconstruction uses a **fresh throwaway `SnapshotPipeline`** built from the
   engine's redactor/blobs/keyring — never `engine.pipeline_for()` — so inspecting an
   active run cannot corrupt its delta baseline. Named `inspection.py` (not
   `inspect.py`) to avoid stdlib-name confusion.

**Tests first:** inspect a COMPLETED run (state + steps match); inspect a PAUSED run
(`pause` populated, `resumable` true); secrets in state appear only as placeholders;
unknown run raises; inspecting a paused run then resuming it still works (pipeline
isolation); encrypted-at-rest run inspects correctly with the engine keyring.

**Done-when:** unit suite green; `inspect_run` in `__all__` and the pinned API test.

---

## Task 3 — Retention & GC: `chowki.maintenance`

**Status:** PENDING

**Goal:** nothing in storage grows unboundedly without an operator story. Audit stays
append-only — rotation is a database-level operator concern, deliberately out of scope.

**Files:** new `python/chowki/src/chowki/maintenance.py`,
new `python/chowki/tests/unit/test_maintenance.py`; modified `storage/base.py`,
`storage/sqlite.py`, `storage/memory.py`, `tests/unit/test_storage_contract.py`.

**Behavior:**
1. Adapter contract additions (contract tests first, both adapters):
   `delete_run(run_id) -> bool` (cascades steps, snapshots, gateway handle; **never**
   audit rows), `purge_nonces(older_than_epoch) -> int` (deletes rows with
   `expires_at < cutoff` only), `list_blob_refs() -> list[str]`,
   `delete_blob(ref) -> bool`.
2. `maintenance.delete_run(run_id, *, engine=None, force=False)`: refuses non-terminal
   runs (PENDING/RUNNING/PAUSED) unless `force=True`; drops the engine's memoised
   pipeline; returns whether anything was deleted.
3. `maintenance.purge_expired_nonces(*, engine=None, grace_seconds=86400) -> int`:
   cutoff = now − grace. Safe because token verification checks `exp` **before** nonce
   consumption — an expired token can never reach the nonce store — and the grace
   window absorbs clock skew. State this reasoning in the docstring; it amends the
   "never GC nonces" rule from Phase 1.
4. `maintenance.sweep_blobs(*, engine=None) -> int`: collect live refs by decoding
   **every** stored snapshot payload of every run (decrypting via the engine keyring
   where needed; blob refs occur only inside snapshot payloads — step results and
   pause payloads are never blob-extracted), delete unreferenced blobs, return the
   count. If any snapshot cannot be decrypted/decoded, **abort with
   `ChowkiStateError` naming the run** — never delete blobs whose liveness is
   unprovable.

**Tests first:** cascade delete removes steps/snapshots/handle but leaves audit;
force policy on non-terminal runs; purge respects grace and keeps unexpired nonces;
consumed-unexpired nonce survives purge (replay safety); sweep keeps referenced blobs,
removes orphans, aborts on an undecryptable snapshot, and works end-to-end with
encryption enabled.

**Done-when:** contract + unit suites green on both adapters.

---

## Task 4 — CLI: `chowki` console script

**Status:** PENDING

**Goal:** the operator surface the console gateway already talks about, with zero new
dependencies (argparse).

**Files:** new `python/chowki/src/chowki/cli.py`, new
`python/chowki/src/chowki/__main__.py`, new
`python/chowki/tests/integration/test_cli.py`; modified `python/chowki/pyproject.toml`
(`[project.scripts] chowki = "chowki.cli:main"`), `hitl/console.py` (print the real
CLI line again — it exists now).

**Behavior:**
1. Global options: `--db PATH` (default `./.chowki/chowki.db`), `--module/-m IMPORT`
   (repeatable; imports user modules so `@chowki.workflow` registration runs — the
   registry is why the CLI can resume at all), `--json` for machine-readable output.
   Engine is built once per invocation; `CHOWKI_MASTER_KEY` respected for encrypted
   stores; a `resume_secret` is read from `CHOWKI_RESUME_SECRET` when set.
2. Subcommands: `runs list [--status S]`, `runs show RUN_ID` (renders
   `inspect_run`), `resume RUN_ID --token T --decision D [--patch JSON] [--note N]`,
   `reissue-token RUN_ID`, `release-step RUN_ID STEP_ID`,
   `complete-step RUN_ID STEP_ID --result JSON`, `recover` (runs `recover_runs`,
   lists them), `rerun RUN_ID`, `maintenance purge-nonces [--grace S]`,
   `maintenance delete-run RUN_ID [--force]`, `maintenance sweep-blobs`.
3. Exit codes: 0 success; 1 with a one-line stderr message on any `ChowkiError`;
   tokens print to stdout exactly once (they are secrets — no logging).

**Tests first** (integration, subprocess against a tmp SQLite db): `runs list/show`
happy paths; pause a run in-process, then `resume` **via the CLI** with `-m` pointing
at a fixture module written to `tmp_path` (PYTHONPATH injected); `reissue-token` +
resume with the reissued token; `release-step` unblocks a claim-refused run;
`maintenance purge-nonces` reports a count; `--json` output parses.

**Done-when:** integration suite green on Windows paths (use `pathlib`/`sys.executable`
throughout); `uv run chowki --help` exits 0; console gateway message matches reality.

---

## Task 5 — Atomic storage transitions

**Status:** PENDING

**Goal:** a human decision commits atomically — the audit row and the run-status flip
can never be observed half-applied. Designed now so the Phase 5 Postgres adapter
implements the same contract instead of retrofitting it.

**Files:** modified `storage/base.py`, `storage/sqlite.py`, `storage/memory.py`,
`core/resume.py`, `tests/unit/test_storage_contract.py`; new
`python/chowki/tests/unit/test_atomic_transitions.py`.

**Behavior:**
1. Adapter contract: `transaction()` context manager. Inside it, **only write methods
   may be called** (reads are contractually unspecified); nesting raises
   `ChowkiStorageError`. On exception, nothing inside is visible afterwards.
   SQLite: `BEGIN IMMEDIATE` … `COMMIT`/`ROLLBACK` held under the process write lock.
   Memory: hold the lock for the block and journal undo operations, reverting on
   exception.
2. `resume()` adopts it: for each decision path, the audit append and the run-record
   write happen inside one `transaction()`. Snapshot writes (`_persist_state_of_record`)
   stay **before** the transaction — the invariant is: state-of-record durable first,
   then decision+status commit together.
3. Document the invariant in `07-cross-sdk-parity.md` §11 (replace the "known gap"
   sentence) in the same commit.

**Tests first:** contract test on both adapters — two writes then a raise → neither
visible; nesting raises; writes commit together on success. Resume-path test: wrap an
adapter so `put_run` raises after `append_audit` succeeded inside the transaction →
assert the audit row is **not** present afterwards and the run is still PAUSED and
resumable via `reissue_token`.

**Done-when:** both suites green; benchmarks unchanged (transaction is not on the
snapshot hot path).

---

## Task 6 — `spec/v1/` schemas for every wire struct

**Status:** PENDING

**Goal:** the envelope schema pattern, completed: every persisted/transmitted struct
has a hand-written JSON Schema and a conformance test binding it to the code.

**Files:** new `spec/v1/run-record.schema.json`, `spec/v1/step-record.schema.json`,
`spec/v1/pause-request.schema.json`, `spec/v1/resume-claims.schema.json`,
`spec/v1/usage.schema.json`; modified `tests/unit/test_public_api.py`,
`spec/README.md`.

**Behavior:** JSON Schema draft 2020-12, following
`spec/v1/snapshot-envelope.schema.json`'s conventions. `required` lists exactly the
struct's non-defaulted fields **in struct field order** (order is part of the wire
format); `properties` covers every field including appended-with-default ones
(`PauseRequest.origin`, `StepRecord.result_replayable`, `RunRecord.usage`, …).

**Tests first:** extend the existing schema-conformance test into a parametrised check
over all six (struct, schema-file) pairs asserting: `required` == required struct
fields in order, and property-name set == full struct field set. Watch it fail for
each missing schema before writing that schema.

**Done-when:** six green conformance checks; `spec/README.md` lists the schema files.

---

## Task 7 — Blob extraction for large sub-objects

**Status:** PENDING — the one wire-format change in this phase; land before Task 8.

**Goal:** message lists and other large containers dedupe like large strings already
do (`TODO(phase-2)` in `state/blobs.py`).

**Files:** modified `state/blobs.py`, `state/redact.py` (walk integration),
`state/pipeline.py` (if needed), `tests/unit/test_blobs.py`,
`tests/integration/test_end_to_end.py`, `docs/research/07-cross-sdk-parity.md` §8,
`spec/v1/snapshot-envelope.schema.json` (payload notes), new benchmark case in
`tests/benchmarks/test_pipeline_bench.py`.

**Behavior:**
1. New ref kind `"ref:mp:sha256:<hex>"`: the MessagePack encoding of an
   already-redacted **top-level container value** of the state dict (dict or list)
   whose encoded size exceeds `blob_threshold_bytes`. Top-level only — that is where
   agent bulk (message histories, tool schemas) lives; recursive sub-container
   extraction stays out of scope.
2. Escaping: `extract_string`'s existing `ref-lit:` escape extends to user strings
   beginning with the new prefix (one prefix-check list, one escape mechanism).
3. `inline_blobs` decodes `ref:mp:` refs via `decode_state`; a missing blob stays
   `SnapshotIntegrityError`. Write-through durability rule applies unchanged.
4. Delta consequence (document in 07 §8): an unchanged large container diffs as
   nothing (same ref string); a changed one is a single `replace` op with a new ref.

**Tests first:** round-trip a state with a 100-message list (extracted, restored
equal); unchanged-container snapshot produces a delta without the container's bytes
(assert encoded delta size); dedup across two runs sharing the container; user string
starting with `ref:mp:` round-trips via escape; cross-engine resume with container
blobs (durability); threshold boundary (just-under stays inline). Benchmark: a 1 MiB
state whose bulk is one top-level list still meets `snapshot_total_1mb_ms`.

**Done-when:** all suites + benchmarks green; 07 §8 and the envelope schema updated
in the same commit.

---

## Task 8 — Conformance vectors + CI drift gate

**Status:** PENDING — after Tasks 6 and 7 (the wire is frozen for this phase).

**Goal:** machine-checkable parity: fixtures the Node SDK must reproduce byte-for-byte,
regenerated and diffed in CI so they can never silently drift from the code.

**Files:** new `spec/scripts/generate_vectors.py`, new `spec/v1/vectors/*.json` +
`spec/v1/vectors/README.md`; modified `.github/workflows/ci.yml` (replace the
`TODO(phase-2)` drift step), new `python/chowki/tests/unit/test_vectors.py`;
`hitl/tokens.py` may expose an internal `sign_claims(claims, secret)` for
deterministic token construction.

**Behavior:**
1. Deterministic inputs only — fixed keys, fixed timestamps, fixed nonces, passed as
   constants inside the generator (no wall clock, no os.urandom).
2. Vector files: `canonical-hash.json` (value → `content_hash`, covering NFC, astral
   keys, nested containers, integer edge cases), `redaction.json` (text + hmac key →
   redacted text, covering every tier-1 kind, tier-2 hits and safe-filter misses,
   key-name tier), `args-hash.json` (name/args/kwargs → hash, covering sets, cycles
   noted as unrepresentable, type-marker fallbacks), `resume-token.json` (claims +
   secret → exact token string, plus verify cases: valid / bad signature / expired /
   action-not-permitted), `envelope.json` (state + fixed key/nonce → sealed envelope
   fields incl. `ref:mp:` blob case, encrypted and plaintext variants).
3. `test_vectors.py` re-derives every vector through the public code paths and
   asserts equality — the local mirror of the CI gate.
4. CI: run the generator, `git diff --exit-code spec/v1/vectors`.

**Tests first:** write `test_vectors.py` against the intended file layout, watch it
fail on missing files, then build the generator until green; commit generated vectors.

**Done-when:** CI job green; `vectors/README.md` states the Node consumption contract
(every fixture must pass in `@chowki/core`'s conformance suite — `docs/features.md`
"Cross-SDK conformance").

---

## Task 9 — Ratify the durability decision (synchronous dispatch)

**Status:** PENDING

**Goal:** close the roadmap's "write-behind: decide, then do or drop" item. Decision:
**keep the synchronous sink as the contract.** A snapshot is durable before the step
returns; SIGKILL loses nothing acknowledged. Crash recovery is the product's core
promise; the measured dispatch cost (`dispatch_ms` budget, 0.2 ms) does not justify
weakening it for latency.

**Files:** `docs/research/07-cross-sdk-parity.md` §3 (explicit durability statement),
`docs/research/02-serialization.md` (amend the "async local buffer queue" budget line),
`docs/research/00-synthesis.md` (footnote on the §5.1 table row),
`docs/plans/00-roadmap.md` (flip the Phase 2 bullet to DECIDED),
`docs/architecture/overview.md` (one line under Performance Budgets).

**Done-when:** the four amendments landed; `dispatch_ms` benchmark still green (the
proof the decision costs nothing); no code change.

---

## Task 10 — Phase close-out

**Status:** PENDING

**Goal:** the phase ends the way Phase 1 did: verified, documented, and self-erasing.

**Steps:**
1. End-to-end integration test: fixture module defines a registered workflow → run
   pauses → **CLI** resumes with EDIT patch → `inspect_run` shows the patched state →
   `maintenance delete-run` after completion; all against one SQLite file.
2. Full harness: unit + integration + benchmarks + pyright + mypy + ruff + layout.
3. `docs/features.md`: every Phase 2 row flipped to ✅ (spot-check none was missed);
   `docs/architecture/overview.md` module map gains registry/inspection/maintenance/CLI.
4. Research amendments for anything learned during execution (same discipline as the
   Phase 1 hardening pass — plans are disposable, amendments are not).
5. `docs/plans/00-roadmap.md`: Phase 2 → DONE with the closing commit range; delete
   this plan file in that same commit.

**Done-when:** roadmap shows Phase 2 DONE and this file no longer exists.

---

## Risks

- **R1 — Registry is process-global state.** Test pollution across cases; the
  `clear_registry()` fixture (autouse in `conftest.py`) is mandatory from Task 1.
- **R2 — CLI subprocess tests on Windows.** Quote nothing by hand; build argv lists,
  use `sys.executable`, `pathlib` everywhere (ruff `PTH` already enforces).
- **R3 — `sweep_blobs` on encrypted stores needs the keyring.** The CLI must surface
  a clear error naming `CHOWKI_MASTER_KEY` when decryption is required and absent —
  never a stack trace, never a deletion.
- **R4 — Memory-adapter transaction rollback.** Keep the undo journal to the four
  write methods the engine actually uses inside transactions; do not build a general
  MVCC layer. If it grows past ~60 lines, stop and simplify the contract instead.
- **R5 — `ref:mp:` lands after vectors would freeze it wrong.** Task order 7 → 8 is
  load-bearing; do not parallelise them.
