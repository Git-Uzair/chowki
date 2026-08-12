# chowki — Feature Catalog & SDK Parity Matrix

**File:** `docs/features.md`
**Status:** Living document — **the single entry point** for planning work in any SDK.
**Maintenance rule (binding, also in `AGENTS.md`):** every task that ships or changes a
feature updates its row here in the same commit. A row nobody updated is a bug.

This catalog is language-agnostic: it describes *behavior*, not Python. The Python SDK
is the reference implementation; byte-level algorithms live in
[`docs/research/07-cross-sdk-parity.md`](research/07-cross-sdk-parity.md) ("07" below).
Phase numbers refer to [`docs/plans/00-roadmap.md`](plans/00-roadmap.md).

---

## How to use this document (the whole workflow)

You need to remember **one file: this one.** Everything else is linked from here.

**To plan the next batch of work:** Phase 2 (Release v0.1) is **DONE** and Phase 3 (Cross-SDK spec prep + Node/TypeScript SDK) is next. Give your plan agent:

> Read `docs/features.md`, `docs/plans/00-roadmap.md` (next non-DONE phase),
> `docs/research/07-cross-sdk-parity.md`, the research doc named in that phase's
> roadmap entry, and `AGENTS.md`. Write `docs/plans/0N-<phase-name>.md`: numbered
> tasks in dependency order, failing test first per task, files touched, Done-when
> gate, `**Status:** PENDING` markers. Scope = exactly the roadmap phase; anything
> else is `TODO(phase-N)`. Update the matrix rows in `docs/features.md` from 🔜 to ✅
> as tasks complete.

**To generate the Node implementation plan (Phase 3):** give your plan agent:

> Read `docs/features.md` — every row with Python ✅ and Node ⬜ is the work list;
> the Node SDK must reach behavioral parity with the Python column, nothing more
> (§13 of 07 lists what NOT to build). Then read
> `docs/research/07-cross-sdk-parity.md` (normative algorithms — where prose
> disagrees, it wins), `spec/v1/` including `vectors/` (conformance fixtures the
> Node SDK must reproduce byte-for-byte), the Node toolchain amendment in
> `docs/research/06-python-monorepo-standards.md` (open decisions — make them
> Tasks 1–2), and `docs/plans/00-roadmap.md` Phase 3. Behavioral questions are
> settled by reading `python/chowki/src/chowki/`, not by judgment. Write
> `docs/plans/03-node-core.md` with the same task format, ending with a
> conformance task that verifies every fixture in `spec/v1/vectors/`.

**To execute any plan:** give your coding agent `AGENTS.md` + the plan file +
"execute task N". One commit per task; flip the task's status marker; keep this
matrix in sync.

**Legend:** ✅ shipped · 🔜 planned (phase noted) · ⬜ not started · — not applicable

---

## 1. Execution core

| Feature | Behavior | Detail | Py | Node |
|---|---|---|---|---|
| Step interceptor (`step`) | Wraps sync/async functions; records inputs/outputs, memoises completed work, snapshots state after success (`snapshot` opt-out), per-step `retries` override, `idempotent` opt-out, `name` override. Plain call outside a run = passthrough. | 07 §4 | ✅ | ⬜ |
| Step identity | `"{name}#{per-run call ordinal}"`; ordinals reset every (re-)execution so replays reproduce ids. Rename/reorder hazard documented. | 07 §4 | ✅ | ⬜ |
| Args-hash | Canonical content hash of sanitized `{name, args, kwargs}`; NFC keys, total-ordered sets, structural expansion of Structs/dataclasses/models/objects, `<TypeName>` marker + warning only for values with no structure, cycle markers, non-finite-float markers. | 07 §4 | ✅ | ⬜ |
| Memoisation | Only COMPLETED + equal args-hash + replayable result short-circuits; non-encodable results store a diagnostic marker and re-run. Changed args on same id → warn + re-execute. | 07 §4 | ✅ | ⬜ |
| Idempotency claims | Atomic claim of `HMAC(resume_secret, run_id\|step_id\|args_hash)` before first side effect; payload-reuse with different args-hash is a hard error. | 07 §5 | ✅ | ⬜ |
| Failure recovery matrix | FAILED + matching args → retry on re-invocation; RUNNING record / claim-without-record → refuse (fate unknown), error names the escape hatches. | 07 §5 | ✅ | ⬜ |
| Operator escape hatches | `release_step(run_id, step_id)` ("effect didn't happen") and `complete_step(run_id, step_id, result)` ("it did; memoise this"). | 07 §5 | ✅ | ⬜ |
| Breaker action matrix | RATE_LIMIT/TOOL → retry (full-jitter backoff, max 3) then PAUSE; VALIDATION → REASK ×2 then PAUSE; CONTEXT_WINDOW → SUMMARIZE once then ABORT; LOOP → PAUSE; BUDGET → configured action. REASK/SUMMARIZE are app signals on the exception. | 07 §9, [04-guardrails](research/04-guardrails.md) | ✅ | ⬜ |
| Error taxonomy + classifier | Six agent error classes; duck-typed classification of foreign exceptions (status 429/529, class-name heuristics) with TOOL_EXECUTION default. | [04-guardrails](research/04-guardrails.md) §3.1 | ✅ | ⬜ |
| Workflow runner (`workflow`) | Opens/loads the run, injects `run_id`/`tenant_id` kwargs (reserved names), binds an engine, drives sync/async bodies, persists terminal status + usage, snapshots final state. | 07 §9 | ✅ | ⬜ |
| Run statuses | PENDING / RUNNING / PAUSED / COMPLETED / FAILED / ABORTED / REJECTED. | 07 §9 | ✅ | ⬜ |
| Crash recovery | `recover_runs` re-arms RUNNING → PENDING (never FAILED) and returns incomplete runs; `resumable_runs` lists them. Re-invocation with the same `run_id` resumes with memoised steps. | 07 §5/§9 | ✅ | ⬜ |
| Warm resume | Newest BASE + deltas reconstruct state; re-execution from the top with memoisation; snapshot indices always continue above stored max (replays never overwrite history). | 07 §8/§9 | ✅ | ⬜ |
| Side-effect rule (R4) | Every side effect must live inside a step: resume re-executes the workflow body. Documented in API docs, quickstart, `AGENTS.md`. | [03-durable-execution](research/03-durable-execution.md) | ✅ | ⬜ |
| Single-writer-per-run | Concurrency inside one run (gather/`Promise.all` over steps) is undefined behavior until Phase 6 branch keys. Must be documented loudly in Node. | 07 §13 | ✅ (documented) | ⬜ |
| Workflow registry | Register workflows by name at decoration; `resume`/`rerun` resolve by name so callers don't pass function references. | 02-release T1 | ✅ | ⬜ |
| Async-aware resume (`aresume`) | Awaitable resume for async workflows; sync `resume()` refuses coroutine workflows loudly instead of returning an unawaited coroutine (verified Phase 1 bug). | 02-release T2 | ✅ | ⬜ |
| Parallel steps, child workflows, cancellation, timers, signals | Deliberately absent everywhere until designed once. | 07 §13 | 🔜 6 | 🔜 6 |

## 2. State pipeline

| Feature | Behavior | Detail | Py | Node |
|---|---|---|---|---|
| Hot-path order | redact+blob-extract (one walk) → base/delta select → MessagePack encode → hash → optional encrypt → dispatch. Budgets enforced by benchmark gates. | 07 §3 | ✅ | ⬜ |
| Redaction tier 1 | Combined alternation over known credential shapes (OpenAI/Anthropic/AWS/GitHub/Slack/Stripe/JWT/Bearer/Basic/private-key/URI-userinfo); inert-screen fast path; user `extra_patterns`. | 07 §7 | ✅ | ⬜ |
| Redaction tier 2 | Shannon entropy ≥ 4.5 bits/char over **code points**, candidate length ≥ 23, digit prefilter, safe-filters (UUID/hex/paths/numbers), 4 KB scan cap with skip counter. | 07 §7 | ✅ | ⬜ |
| Key-name tier | Sensitive key names redact the whole value as `key_name` kind; safe-key/safe-value sets skip scanning. | 07 §7 | ✅ | ⬜ |
| Placeholder derivation | `[REDACTED:kind:8-hex]`, HMAC-SHA256 under a **persisted per-deployment key** (secret slot `redaction`); fixpoint over own placeholders; stable across restarts. | 07 §7 | ✅ | ⬜ |
| Binary boundary | UTF-8-decodable bytes-likes are text-redacted (container type preserved); non-UTF-8 binary passes through by documented exemption; set/frozenset members redacted. | 07 §7 | ✅ | ⬜ |
| Redaction always on | Cannot be disabled; encryption is opt-in. Human patches redacted once, per-op, under the destination key. | 07 §7 | ✅ | ⬜ |
| Blob store | `ref:sha256:` content addressing for string leaves > 4 KB; `ref-lit:` escaping; **write-through durability before the referencing snapshot**; read-through on miss. | 07 §8 | ✅ | ⬜ |
| Blob sub-object extraction | Extract large sub-*trees*, not only strings (new ref kind; wire-contract change — post-release). | Phase 3 spec prep | 🔜 3 | ⬜ |
| Delta engine | RFC 6902 emit add/replace/remove, accept all six ops; fast path for shallow ops; compaction at depth ≥ 50 or delta bytes > 0.20 × base. | 07 §8 | ✅ | ⬜ |
| Codec + envelope | MessagePack payloads in a versioned envelope (field order fixed); integrity hash verified on unseal; migration registry keyed by from-version, sequential chain. | 07 §3 | ✅ | ⬜ |
| Encryption at rest | AES-256-GCM, 96-bit random nonce, AAD = `tenant:run:vN`; KeyRing with active key id + rotation; env bootstrap; **off by default**. | 07 §3 | ✅ | ⬜ |
| Canonical JSON + content hash | RFC 8785 subset: NFC, UTF-16 ordering for astral keys, dup-key error, `sha256:` prefix. ES number formatting = Phase 3 on both SDKs. | 07 §1 | ✅ | ⬜ |
| Two hash semantics | `content_hash` = cross-SDK identity; envelope `state_hash` = writer-local integrity only. | 07 §2 | ✅ | ⬜ |
| Durability semantics | Synchronous persistence: a snapshot is committed to storage before the step returns, so process death after step return loses zero committed step state. Default SQLite = WAL + `synchronous=NORMAL` (process kills, not power loss). | 02-release T9 | ✅ | ⬜ |

## 3. Suspension & HITL

| Feature | Behavior | Detail | Py | Node |
|---|---|---|---|---|
| Pause gates | `pause(reason, payload, permitted_actions, reviewers, channel)` freezes state at the boundary, persists PAUSED durably (even if the body swallows the signal), mints token, notifies gateway (failures logged, never fatal), raises. Gate ids replay deterministically; decided gates fall through and re-apply the human patch. | 07 §9 | ✅ | ⬜ |
| Auto-pause | Breaker PAUSE decisions and body-level guardrail breaches suspend identically (`origin="auto"`, APPROVE/REJECT/EDIT, error payload), raised chained from the original error; plain bugs still fail the run. | 07 §9 | ✅ | ⬜ |
| Resume decisions | APPROVE (continue), REJECT (→ REJECTED, audited, raises), EDIT (patch redacted once → new state-of-record BASE; gate-origin seeds reviewed state, auto-origin seeds decided state), ESCALATE (fresh token, reviewers updated, still paused). | 07 §9 | ✅ | ⬜ |
| Resume tokens | msgpack claims + HMAC-SHA256, base64url unpadded, `body.sig`; scope-bound (run, step, actions); TTL 86400 s; **lifetime single-use nonces**; verification order fixed. Cross-SDK verifiable. | 07 §6 | ✅ | ⬜ |
| Token reissue | `reissue_token(run_id)` re-mints from the stored pause request (same scope, fresh nonce), re-notifies gateway. The recovery for lost/burnt tokens. | 07 §6 | ✅ | ⬜ |
| Audit log | Append-only provenance: actor, action, state hashes before/after, redacted patch, nonce, note. No delete API may exist on any adapter. | [05-hitl-gateway](research/05-hitl-gateway.md) §4 | ✅ | ⬜ |
| Gateway protocol | `notify → handle (persisted)`, `confirm(handle, decision, actor)`, `verify_ingress(raw bytes)`, `parse_action`; console + in-memory reference gateways. | [05-hitl-gateway](research/05-hitl-gateway.md) | ✅ | ⬜ |
| Slack / Teams / REST channels, role enforcement, N-of-M approval, delivery retry | Payload contracts researched, not built. `allowed_roles` carried in tokens, unenforced. | Phase 4 | 🔜 4 | 🔜 4 |

## 4. Guardrails

| Feature | Behavior | Detail | Py | Node |
|---|---|---|---|---|
| Loop tier 1 | Windowed hash set (k=5) over `content_hash({tool, kwargs})`; 3 repeats trip. Auto-fed by every step. | 07 §9 | ✅ | ⬜ |
| Loop tier 2 | Normalized Levenshtein over consecutive texts (cap 512 chars); ≥ 0.85 warns, ≥ 0.95 × 3 consecutive trips. Fed via `record_text`. | [04-guardrails](research/04-guardrails.md) | ✅ | ⬜ |
| Loop tier 3 | Delegation graph cycles (edges seen ≥ 2), iterative DFS. Fed via `record_transition`. | [04-guardrails](research/04-guardrails.md) | ✅ | ⬜ |
| Step ceiling | `max_steps_per_run` (default 25) per execution. | [04-guardrails](research/04-guardrails.md) §4 | ✅ | ⬜ |
| Budgets | `report_usage(Usage\|int)`; soft threshold (80%) warns once per dimension + optional callback; hard ceiling raises (→ auto-pause or abort per config); resumed runs re-seed spend from persisted usage. | 07 §9/§10 | ✅ | ⬜ |
| Zero-config defaults | The 15-field default table (window sizes, thresholds, retries, backoff, budget actions); guardrails can be disabled wholesale. | [04-guardrails](research/04-guardrails.md) §4 | ✅ | ⬜ |
| Semantic-embedding loop tier, model downgrade, alert fan-out | Excluded: require model calls / egress the engine never does. | 07 §13 | — | — |

## 5. Storage

| Feature | Behavior | Detail | Py | Node |
|---|---|---|---|---|
| Adapter contract | Runs/steps/snapshots CRUD-without-delete, `snapshots_for_resume`, `max_snapshot_index`, claim/release idempotency, durable named secrets, lifetime single-use nonces, blobs, append-only audit, gateway handles, `close`. Copy-on-read/write. | 07 §11 | ✅ | ⬜ |
| SQLite adapter (default) | `./.chowki/chowki.db`, WAL, busy_timeout 5 s, synchronous=NORMAL, process-level write lock, status/audit indices. Single-process by design (R7). | `chowki/storage/sqlite.py` docstring | ✅ | ⬜ |
| In-memory adapter | Full contract for tests; secrets live as long as the store. | 07 §11 | ✅ | ⬜ |
| Secret slots | `resume` (idempotency HMAC fallback), `redaction` (placeholder HMAC). Explicit `resume_secret` config wins for both keys + tokens; without it, tokens are ephemeral (warned). | 07 §12 | ✅ | ⬜ |
| Atomic transitions | Multi-write state changes (audit + run flip) commit atomically. | Phase 3 spec prep | 🔜 3 | ⬜ |
| Retention & GC | `delete_run` cascade, expired-nonce purge, blob sweep; audit stays immutable. | Phase 3 spec prep | 🔜 3 | ⬜ |
| Postgres / Redis adapters, multi-process leasing | — | Phase 5 | 🔜 5 | 🔜 5 |

## 6. Telemetry

| Feature | Behavior | Detail | Py | Node |
|---|---|---|---|---|
| Structured logging | Opt-in JSON/console structlog config; engine never configures logging on import. | [06-monorepo](research/06-python-monorepo-standards.md) | ✅ | ⬜ |
| OTel tracing + metrics | Per-step spans (opt-in `tracing_enabled`), counters + histograms for saves/sizes/steps/warnings/loops. API-only dependency; SDK is an extra. | [06-monorepo](research/06-python-monorepo-standards.md) | ✅ | ⬜ |

## 7. Configuration & operability

| Feature | Behavior | Detail | Py | Node |
|---|---|---|---|---|
| Engine + config | Storage/tenant/encryption/keyring/redaction-key/resume-secret/guardrails/gateway/blob-threshold/db-path/tracing; process-global `configure()`/`get_engine()`/`reset_engine()`; per-run pipeline memo dropped on terminal states. | source: `chowki/config.py` | ✅ | ⬜ |
| Public API surface | `step, workflow, pause, resume, aresume, rerun, inspect_run, configure, current_run, report_usage, record_text, record_transition, recover_runs, resumable_runs, reissue_token, release_step, complete_step` + types/errors. Pinned by a test. | `chowki/__init__.py` | ✅ | ⬜ |
| Workflow registry + resume-by-name | See §1. | 02-release T1 | ✅ | ⬜ |
| CLI | `runs list/show`, `resume` (sync + async), `reissue-token`, `release-step`, `complete-step`, `recover`, `rerun`. | 02-release T4 | ✅ | ⬜ |
| Inspection API | `inspect_run(run_id)`: record, steps, latest redacted state, audit — without touching live pipeline state. | 02-release T3 | ✅ | ⬜ |
| Embedded approval endpoints | Bring-your-own web app: documented FastAPI/Flask handlers calling `resume`/`aresume`, normative exception → HTTP mapping, background-execution pattern. chowki serves nothing; the token is the credential. | 02-release T5 | ✅ | ⬜ |
| User guide + flagship example + packaging | `docs/user-guide/`, the showcase agent (budget pause, approval gate, kill → `rerun`), CHANGELOG, PyPI release. | 02-release T6–T8 | ✅ (Local build, wheel smoke test, release workflow configured; PyPI publish pending maintainer tag) | — |
| Spec vectors + schemas | `spec/v1/` schemas for every wire struct + generated conformance vectors (canonical hashes, placeholders, tokens, args-hashes) with a CI drift gate. Node's conformance suite. | Phase 3 spec prep | 🔜 3 | consumes |
| Provider integrations (auto-usage), testing kit, outbox | — | Phase 6 | 🔜 6 | 🔜 6 |

---

## Cross-SDK conformance (how Node proves parity)

Phase 3's spec-prep produces `spec/v1/vectors/` — fixtures generated by the Python
SDK. The Node plan must end with a conformance task asserting, for every fixture:
identical `content_hash`, identical redaction placeholders (shared key), successful
verification of Python-minted resume tokens (and vice versa), and lossless decode of
Python-written snapshot envelopes. When those pass and every §1–§7 row shows ✅/✅,
the SDKs are at parity by definition — no memory required.

## Code map (Python reference implementation)

Where each subsystem lives, for agents orienting in the source. Performance budgets
are code, not prose: `python/chowki/tests/benchmarks/budgets.py` is the normative
registry, enforced by the benchmark suite with a 1.5× CI tolerance.

| Module | Contents |
|---|---|
| `chowki/config.py` | `ChowkiConfig`, `ChowkiEngine`, process-global `configure()`/`get_engine()`/`reset_engine()` |
| `chowki/core/` | run context (`context.py`), step decorator + operator escape hatches (`decorators.py`), workflow runner + pause/auto-pause/recovery (`runner.py`), warm resume (`resume.py`) |
| `chowki/state/` | snapshot pipeline (`pipeline.py`), redactor (`redact.py`), blobs (`blobs.py`), RFC 6902 deltas (`delta.py`), msgpack codec + envelope + migrations (`codec.py`), AES-GCM + KeyRing (`crypto.py`), canonical JSON + hashing (`canonical.py`) |
| `chowki/guardrails/` | defaults (`config.py`), loop tiers (`loops.py`), budgets (`budget.py`), breaker (`breaker.py`) |
| `chowki/hitl/` | gateway protocol + in-memory (`gateway.py`), console gateway (`console.py`), resume tokens (`tokens.py`), audit log (`audit.py`) |
| `chowki/storage/` | adapter protocol (`base.py`), SQLite (`sqlite.py` — its docstring is the concurrency contract), in-memory (`memory.py`) |
| `chowki/telemetry/` | structlog config (`logging.py`), OTel spans/metrics (`tracing.py`) |
| `chowki/types.py`, `chowki/errors.py` | wire structs (field order = format), error taxonomy + classifier |
