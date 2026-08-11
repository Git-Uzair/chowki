# chowki — Roadmap

**File:** `docs/plans/00-roadmap.md`
**Date:** 2026-08-11
**Status:** Living document — the single authority on phase numbering and remaining scope.

---

## Phase numbering (canonical)

Earlier documents used three incompatible numbering schemes: `00-synthesis.md` labeled
*itself* "Phase 2" (research phase 1 = workstreams, phase 2 = synthesis), its open
questions implied Phase 3 = Node/TS and Phase 4 = gateway channels, and the foundation
plan's `TODO(phase-2)` markers meant simply "the next implementation phase". **The table
below supersedes all of those.** Research-process labels in `docs/research/*.md` headers
describe how those documents were produced, not this roadmap.

| Phase | Scope | Status |
|---|---|---|
| 0 | Repo scaffold (uv workspace, harness, CI, benchmarks) | **DONE** |
| 1 | `chowki-py` core MVP + correctness hardening | **DONE** |
| 2 | Python operability & the cross-SDK spec | next |
| 3 | Node/TypeScript SDK (`@chowki/core`) | planned |
| 4 | Reference HITL gateway channels (Slack, Teams, REST) | planned |
| 5 | Scale-out storage & security (Postgres/Redis, KMS, key lifecycle) | planned |
| 6 | Advanced durability & ecosystem (outbox, timers, signals, integrations) | planned |

`TODO(phase-N)` markers in code refer to this table.

---

## Phase 0 + 1 — DONE

Executed from the foundation plan (`docs/plans/01-foundation.md`, removed from the tree
when this roadmap landed — retrieve it from git history). All 23 tasks completed; the
suite is 464 tests + benchmark budget gates, `pyright --strict` and `mypy --strict`
clean.

A post-plan **correctness hardening pass** (2026-08-11, commits `19b537a..`) closed the
gaps found by review; these are part of the Phase 1 contract now and the Node SDK must
match them (see `docs/research/07-cross-sdk-parity.md`):

- **Durable blobs:** pipeline-extracted blobs write through to the storage adapter, so
  cross-process warm resume survives states holding strings over the blob threshold.
- **Recoverable idempotency:** a cleanly FAILED step retries on re-invocation; a
  mid-step death still refuses (side-effect fate unknown) but operators have
  `chowki.release_step` / `chowki.complete_step` as explicit escape hatches.
- **Real auto-pause (ADR-005):** breaker PAUSE decisions and body-level guardrail
  breaches durably suspend the run (PAUSED + resume token + gateway notify) as
  `WorkflowPaused` chained from the original error, instead of failing with an
  advisory attribute. `PauseRequest.origin` distinguishes `"gate"` from `"auto"`.
- **Binary redaction:** UTF-8-decodable bytes/bytearray/memoryview leaves are text-
  redacted; non-UTF-8 binary passes through by documented exemption; set/frozenset
  members are redacted.
- **Stable placeholders:** the redaction HMAC key persists per deployment
  (storage secret slot `"redaction"`), so placeholders are stable across restarts.
- **Token recovery:** `chowki.reissue_token(run_id)` re-mints a lost/burnt pause token
  from the stored `PauseRequest` and re-notifies the gateway.
- **Guardrail feeds:** `chowki.record_text` / `chowki.record_transition` expose loop
  tiers 2 and 3; `max_snapshot_index` / `release_idempotency_key` joined the storage
  adapter contract.

---

## Phase 2 — Python operability & the cross-SDK spec

Everything the Python SDK needs to be operable in anger, plus the language-neutral
contract Phase 3 will be generated from. Prerequisite for Phase 3.

- **Workflow registry:** `@chowki.workflow` optionally registers by name;
  `chowki.resume(...)` and recovery APIs accept a workflow name instead of a function
  reference (removes the `_invoke_workflow` TypeError heuristic in `core/resume.py`).
- **Minimal CLI** (`python -m chowki` / console script): `runs list`, `runs show`,
  `resume`, `reissue-token`, `release-step`, `complete-step`. Depends on the registry.
- **Inspection API:** first-class read surface (`chowki.inspect(run_id)` → status,
  steps, latest redacted state, audit trail) — the "inspect" leg of the control-plane
  pitch; today callers must reach into `engine.storage`.
- **Retention & GC:** `delete_run`, blob reference counting or mark-and-sweep against
  live snapshots, expired-nonce purge as an explicit maintenance operation, audit
  rotation policy. Nothing in storage may grow unboundedly without an operator story.
- **Spec codegen automation (ADR-001):** `spec/scripts/` generation for Python and TS
  models from `spec/v1/`, CI drift gate (`git diff --exit-code`), replacing the
  `TODO(phase-2)` in `.github/workflows/ci.yml`.
- **Cross-SDK parity spec:** encode the normative algorithms from
  `docs/research/07-cross-sdk-parity.md` into `spec/v1/` (placeholder derivation,
  canonical JSON incl. number formatting, args-hash sanitisation, step identity,
  token format, envelope AAD, blob refs, cost representation).
- **Adapter atomicity:** an atomic multi-write transition API on the storage contract
  (resume currently writes audit + run + snapshot as separate autocommits) — designed
  now so the Phase 5 Postgres adapter is not retrofitted.
- **Blob sub-object extraction:** extract large sub-*objects*, not only strings
  (`TODO(phase-2)` in `state/blobs.py`).
- **Write-behind dispatch (decide, then do or drop):** the research budget line calls
  the storage dispatch an "async in-process memory queue"; Phase 1 ships a synchronous
  sink deliberately. Decide the durability semantics (what may be lost on SIGKILL),
  implement the bounded queue with backpressure + flush-on-pause/close if kept, and
  align the research docs either way.

## Phase 3 — Node/TypeScript SDK (`@chowki/core`)

Generated from the research docs (incl. `07-cross-sdk-parity.md`) and `spec/v1/`.
Byte-level parity targets: canonical JSON + content hashes, redaction placeholders,
resume-token verification, snapshot envelope decode (both directions), args-hash. The
ES-number canonicalisation `TODO(phase-3)` in `state/canonical.py` lands here, on the
Python side too. Toolchain decisions (package manager, build, test runner, benchmark
harness) are open research items — `06-python-monorepo-standards.md` has no Node
equivalent yet.

## Phase 4 — Reference HITL gateway channels

The `ChannelGateway` protocol is designed against these payloads already
(`05-hitl-gateway.md`): Slack Block Kit adapter (HMAC v0 signature verification,
`response_url` vs `chat.update` policy), Teams Adaptive Cards 1.5/1.6 (JWKS/RS256
ingress), REST/webhook gateway with signed callbacks and an optional workflow-registry
integration, delivery failure handling (retry/redelivery/reminders for paused runs),
and the approval-policy layer (`allowed_roles` enforcement, N-of-M, self-approval
prevention) that the tokens already carry fields for.

## Phase 5 — Scale-out storage & security

PostgreSQL and Redis adapters (async-first; multi-process run ownership/leasing and
orphan recovery), connection/contention model beyond single-process SQLite (R7),
cloud KMS extras (`chowki[aws]`, GCP, Vault), ChaCha20-Poly1305 fallback for AES-NI-less
hardware (`TODO(phase-5)` in `state/crypto.py`), key-rotation re-encryption sweep.

## Phase 6 — Advanced durability & ecosystem

Transactional outbox for non-idempotent side effects (ADR-004's named element),
durable timers/`chowki.sleep`, external signals/events waking paused runs, child
workflows, parallel steps within a run (deterministic branch keys), cancellation
(`cancel_run`), provider integrations that auto-report usage (Anthropic/OpenAI
wrappers), framework examples (FastAPI, LangGraph), and a public testing kit
(fake gateway, deterministic clock, run-to-step-N helpers).

---

## Working agreement

One plan document per phase, generated the way `01-foundation.md` was: from the
research docs, with TDD tasks, Done-when gates, and budget assertions. A phase's plan
is deleted from the tree when every task is complete and verified; this roadmap entry
flips to DONE with a pointer to the closing commit range.
