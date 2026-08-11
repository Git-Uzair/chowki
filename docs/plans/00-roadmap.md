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
| 2 | **Release v0.1** — operability core, embedding, docs, packaging, launch | **DONE** (closing commit range `080caccd..a826cc3d`; PyPI publish & `v0.1.0` tag push pending maintainer execution) |
| 3 | Cross-SDK spec prep + Node/TypeScript SDK (`@chowki/core`) | planned |
| 4 | Reference HITL gateway channels (Slack first, Teams, full REST gateway) | planned |
| 5 | Scale-out storage & security (Postgres/Redis, KMS, key lifecycle) | planned |
| 6 | Advanced durability & ecosystem (outbox, timers, signals, integrations) | planned |

`TODO(phase-N)` markers in code refer to this table. (2026-08-11: the spec-prep items
that briefly lived in Phase 2 — sub-object blobs, spec drift CI — moved to Phase 3 and
their code markers were updated; release comes first.)

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

## Phase 2 — Release v0.1 — DONE

**Status:** **DONE** (closing commit range `080caccd..a826cc3d`; PyPI publish and `v0.1.0` tag push pending maintainer execution).

Executed from `docs/plans/02-release.md` (completed and removed per roadmap working agreement). Everything between Phase 1 and a public PyPI release that can be showcased. No wire-format changes in this phase — the v0.1 on-disk format is Phase 1's, unchanged.

- **Workflow registry + resume-by-name + `rerun`:** `chowki.resume(run_id, token,
  decision)` works without a function reference; recovered runs are re-runnable.
- **Async-aware resume (`aresume`):** verified bug — `resume()` on an async workflow
  returns an unawaited coroutine and the run never re-executes. Blocking for the
  embed-in-your-web-app story.
- **Inspection API** (`inspect_run`) and the **CLI** (`runs list/show`, `resume`,
  `reissue-token`, `release-step`, `complete-step`, `recover`, `rerun`).
- **Production resume guide:** copy-paste FastAPI/Flask handlers so users expose
  approval endpoints from their *existing* apps (chowki serves nothing); exception →
  HTTP status mapping; background-execution guidance; token-handling security notes.
- **User guide** (`docs/user-guide/`): concepts, warm resume + the R4 side-effect
  rule, guardrails + `report_usage` provider recipes, HITL, config/encryption, honest
  limits page.
- **Flagship example:** an LLM tool-use agent with a budget auto-pause, an approval
  gate, and the kill-mid-run → `rerun` demo (no re-spent tokens) — the launch GIF.
- **Packaging & release engineering:** CHANGELOG, `v0.1.0` tag, TestPyPI dry run
  through `release.yml`, wheel smoke test, README overhaul, publish.
- **Durability decision:** DECIDED — synchronous dispatch is the contract (state snapshots are committed synchronously to storage before a step returns; SIGKILL after step return loses zero acknowledged state).

## Phase 3 — Cross-SDK spec prep + Node/TypeScript SDK (`@chowki/core`)

First the spec-prep the Node port consumes, then the port itself.

**Spec prep** (moved from the pre-release Phase 2 scope, 2026-08-11):
- Adapter atomic-transition API (designed before the Phase 5 Postgres adapter).
- Retention & GC: `delete_run`, expired-nonce purge, blob sweep as explicit
  maintenance operations + CLI subcommands.
- Blob sub-object extraction (`TODO(phase-3)` in `state/blobs.py`) — a wire-format
  addition, versioned properly now that v0.1 is public.
- `spec/v1/` schemas for every wire struct + **conformance vectors** generated by the
  Python SDK with a CI drift gate (`TODO(phase-3)` in `.github/workflows/ci.yml`) —
  Node's parity test suite.

**Node SDK:** generated from `docs/features.md` (parity matrix) +
`07-cross-sdk-parity.md` + `spec/v1/`. Byte-level parity targets: canonical JSON +
content hashes (ES-number canonicalisation `TODO(phase-3)` in `state/canonical.py`
lands here, on the Python side too), redaction placeholders, resume-token
verification, envelope decode both directions, args-hash. Toolchain decisions per the
amendment in `06-python-monorepo-standards.md`.

## Phase 4 — Reference HITL gateway channels

The `ChannelGateway` protocol is designed against these payloads already
(`05-hitl-gateway.md`). **Slack ships first, immediately after the v0.1 launch** (the
approve-from-Slack GIF is the showcase follow-up), preferring **Socket Mode** for
ingress so the zero-infrastructure story holds (no public webhook URL); the HTTP
Block Kit path (HMAC v0 signature verification, `response_url` vs `chat.update`
policy) remains for teams that already have ingress. Then: Teams Adaptive Cards
1.5/1.6 (JWKS/RS256 ingress), the full hosted REST/webhook gateway with signed
callbacks (distinct from v0.1's embed-in-your-own-app recipes), delivery failure
handling (retry/redelivery/reminders for paused runs), and the approval-policy layer
(`allowed_roles` enforcement, N-of-M, self-approval prevention) that the tokens
already carry fields for.

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
