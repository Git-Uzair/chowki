# Durable Execution & Resume Semantics Architectural Analysis (2026)

**Project:** `chowki`  
**Date:** 2026-08-08  
**Scope:** Durable execution patterns, determinism constraints, code versioning, side-effect idempotency, transactional outbox pattern, crash recovery, HMAC secure resume tokens, and zero-waste warm resume for `chowki`.

---

## 1. Durable Execution Architectural Patterns

### 1.1 Comparative Pattern Analysis: Workflow History Replay vs. Step State Persistence vs. Event Sourcing

Durable execution engines ensure that multi-step workflows, agent operations, and background jobs complete reliably across crashes, network partitions, and process restarts. Three dominant architectural models exist:

| Architectural Metric | Workflow History Replay (Temporal / Restate) | Step State Persistence (DBOS / LangGraph / `chowki`) | Event Sourcing (Event-Driven Engines) |
| :--- | :--- | :--- | :--- |
| **Primary Mechanism** | Appends SDK actions/events to an execution journal; replays code from start on failure/resume, returning recorded results. | Saves step inputs, outputs, and state snapshots directly to a database upon step completion. | Appends state delta events to an immutable event store; reduces event history to compute current state. |
| **Determinism Requirement** | Strict. Workflow code must be 100% deterministic (no unmanaged randoms, dates, thread races, or direct non-deterministic I/O). | Low/None. Non-determinism is isolated inside step boundaries (`@chowki.step`). Workflow logic does not replay past steps. | Moderate. Event handlers and state reducers must deterministically process incoming event streams. |
| **Code Versioning Overhead** | High. Modifying code requires worker build pinning, immutable deployments, or explicit branching patches (`workflow.patched()`). | Low. Past completed step outcomes remain immutable in DB; active step execution resumes from the last incomplete step. | High. Event schema migration requires upcasters, projections, and versioned event handlers. |
| **Execution Overhead & Latency** | High replay bloat. Re-simulates prior steps on every resume/recovery cycle to reconstruct local variable state. | Zero-waste warm resume. Directly loads saved state delta and resumes execution at the unfulfilled step. | Replay bloat proportional to total event stream depth unless state snapshots are routinely taken. |
| **Infrastructure Dependency** | Heavy. Requires dedicated cluster components (Temporal Server, DB clusters, gRPC matching engines, or Restate Server). | Embedded / In-Process. Runs as a lightweight library within application processes, backed by SQLite or PostgreSQL. | Requires event brokers (Kafka, NATS, RabbitMQ) and dedicated event store databases. |

* [Source: https://docs.temporal.io/ (Accessed: 2026-08-08)]
* [Source: https://docs.restate.dev/services/versioning (Accessed: 2026-08-08)]
* [Source: https://docs.dbos.dev/architecture (Accessed: 2026-08-08)]
* [Source: https://docs.langchain.com/oss/python/langgraph/persistence (Accessed: 2026-08-08)]

### 1.2 Determinism and Code Versioning Models in Modern Durable Execution Frameworks

#### Temporal
* **Determinism Model:** Temporal relies on replay-based recovery. All workflow logic must be strictly deterministic. Non-deterministic operations (network calls, DB queries, UUID generation, random numbers, current time) must be encapsulated within Activities.
* **Code Versioning Strategy:** Temporal uses **Worker Versioning** (binding workers to unique Build IDs) so in-flight workflows remain pinned to the worker build that started them. For code modifications within active workflows, developers must use the `workflow.patched()` API, surrounding code changes in `if/else` version gates. These patch gates must be maintained indefinitely until all old workflow instances complete.
* [Source: https://docs.temporal.io/ (Accessed: 2026-08-08)]
* [Source: https://restate.dev/blog/solving-durable-executions-immutability-problem/ (Accessed: 2026-08-08)]

#### Restate
* **Determinism Model:** Restate records every context operation (`ctx.run`, service calls, sleep, random generation) in an execution journal. During recovery or resumption, Restate replays the handler code and matches context calls against journal entries. Non-deterministic code outside `ctx.run` causes journal mismatches (`RT0016` error) and halts execution.
* **Code Versioning Strategy:** Restate enforces **Immutable Deployments**. Every code deployment receives a unique, immutable endpoint (e.g. AWS Lambda ARN or unique Kubernetes container URL). Restate pins in-flight invocations to their original deployment endpoint. When a new version is registered, new invocations route to the latest endpoint while old invocations drain on their original pinned endpoint. Emergency fixes rely on pause-and-resume operations across deployment endpoints.
* [Source: https://docs.restate.dev/services/versioning (Accessed: 2026-08-08)]
* [Source: https://www.restate.dev/blog/dealing-with-versioning-in-long-running-agents (Accessed: 2026-08-08)]

#### DBOS
* **Determinism Model:** DBOS enforces deterministic workflow functions. All non-deterministic I/O, database access, API calls, and random number generation must occur inside steps (`@DBOS.step()`).
* **Code Versioning Strategy:** DBOS tags every workflow instance with an application version (computed automatically from source code hashes or explicit versions). During recovery, DBOS only resumes workflows matching the active application version. For code upgrades, DBOS supports blue-green deployment draining across application versions or explicit workflow patching (`DBOS.patch()`).
* [Source: https://docs.dbos.dev/python/tutorials/upgrading-workflows (Accessed: 2026-08-08)]
* [Source: https://www.dbos.dev/blog/async-python-is-secretly-deterministic (Accessed: 2026-08-08)]

### 1.3 The Lightweight `chowki` Decorator SDK Model (`@chowki.step`, `@chowki.workflow`)

`chowki` bridges the gap between complex durable execution engines and simple in-process Python/TypeScript agent frameworks. It eliminates Temporal's infrastructure bloat and Restate's strict replay journal constraints through state-boundary step persistence:

1. **No Replay Engine Bloat:** Rather than running an external orchestrator cluster or replaying history logs line-by-line from start to finish, `chowki` records step outcomes and state deltas directly into a local or primary database (SQLite or PostgreSQL) at step boundaries.
2. **Flexible Agent Execution Without Sandbox Constraints:** LLM-based agent loops are inherently non-deterministic (varying token responses, dynamic tool selections, adaptive reasoning). In `chowki`, agent steps wrapped in `@chowki.step` save their output upon completion. If an agent workflow pauses for human input or recovers from a crash, `chowki` re-hydrates the latest state snapshot and resumes at the next pending step without re-evaluating completed steps or failing due to history journal mismatches.
3. **Decoupled Code Evolution:** Because `chowki` resumes directly from saved state snapshots rather than replaying past code lines, changing prompt templates, adding loggers, or modifying downstream step logic in `@chowki.workflow` does not invalidate past completed steps.

> **Amendment (2026-08-11, normative — the claim above needs its boundary stated):**
> "Decoupled code evolution" holds only for changes that preserve **step identity**,
> which Phase 1 pinned as `f"{name}#{per-run call ordinal}"` with an args-hash guard
> (`07-cross-sdk-parity.md` §4). Renaming a step, removing or reordering calls, or
> inserting a call before existing ones shifts identity between a pause and a resume:
> completed work re-executes and side effects re-fire. Both SDKs must derive identity
> and args-hashes identically or cross-SDK resume silently re-runs side effects.
> Workflow-definition versioning (Temporal Build-ID-style) remains an open Phase 6
> design item; until then this hazard is documented, not solved.

---

## 2. Side Effects, Idempotency, and Transactional Consistency

### 2.1 Idempotency Keys for Side-Effectful Tool Calls

When an AI agent executes side-effectful tool calls (e.g. sending emails, executing database writes, triggering payment APIs, issuing webhooks), network timeouts or process crashes can leave the client uncertain whether the downstream service processed the request.

```
Client / Agent -> POST /tools/execute (Header: Idempotency-Key: uuid) -> Primary Store (Atomic Reserve)
                                                                             |
                                                                             +--> (New Key): Run Tool & Save Outcome
                                                                             +--> (Existing Key): Return Cached Outcome
```

To prevent duplicate side effects, `chowki` mandates client-generated idempotency keys:

* **Header & Key Generation:** Every state-modifying tool call includes an explicit `Idempotency-Key` header (UUIDv4 or cryptographically derived token like `HMAC(workflow_id, step_name)`).
* **Atomic Claim Primitive:** Idempotency key reservation uses an atomic database statement (`INSERT INTO idempotency_keys ... ON CONFLICT DO NOTHING`). This eliminates check-then-act (TOCTOU) race conditions when concurrent retries arrive within milliseconds.
* **Payload Fingerprinting:** To prevent key collisions across conflicting parameters, `chowki` computes a SHA-256 hash of the request payload. Reusing an idempotency key with a modified payload is rejected with `409 Conflict` or `422 Unprocessable Entity`.
* [Source: https://newsletter.securepatterns.dev/p/designing-api-idempotency-keys-to-prevent-duplicate-writes (Accessed: 2026-08-08)]
* [Source: https://www.flowverify.co/blog/idempotency-keys-patterns-race-conditions (Accessed: 2026-08-08)]

### 2.2 Exactly-Once Claims vs. At-Least-Once Reality

In distributed systems, true end-to-end "exactly-once" execution over unreliable networks is impossible [Akkoyunlu et al., 1975]. What distributed frameworks market as "exactly-once" is **at-least-once delivery combined with idempotent processing**:

1. **Network Partition & Partial Failure Handling:** If a worker executes a tool call successfully against an external service but crashes before writing the outcome to its local store, a subsequent retry will re-issue the request.
2. **Downstream Idempotency Propagation:** `chowki` forwards its internal step idempotency key to downstream external APIs (e.g. Stripe's `Idempotency-Key` header or OpenAI's request seed/idempotency keys). If the downstream service already processed the side effect, it returns the cached response without repeating the action.
3. **Status Locking & Pending Timeouts:** When an idempotency key is claimed, its status is set to `PENDING`. Concurrent requests arriving while the key is `PENDING` receive `409 Conflict` with a `Retry-After` header, preventing retry storms.
* [Source: https://backendbytes.com/articles/idempotency-patterns-distributed-systems/ (Accessed: 2026-08-08)]
* [Source: https://matheuspalma.com/blog/idempotency-keys-and-safe-retries (Accessed: 2026-08-08)]

> **Amendment (2026-08-11, normative — claim lifecycle and recovery):** a claim
> primitive without a recovery story bricks runs: Phase 1 initially held every claim
> forever, so ANY failed idempotent step (the default) made its run permanently
> unrecoverable. The shipped lifecycle (`07-cross-sdk-parity.md` §5): the key is
> `HMAC-SHA256(resume_secret, run_id|step_id|args_hash)`; a refused claim next to a
> **FAILED** record with matching args is this run's own finished attempt and the step
> **retries**; a **RUNNING** record or claim-without-record (mid-step death,
> side-effect fate unknown) refuses, with mandatory operator escape hatches
> `release_step(run_id, step_id)` ("it did not happen") and
> `complete_step(run_id, step_id, result)` ("it happened; memoise this"). Every SDK
> must ship both. The `PENDING`/`Retry-After` HTTP protocol and downstream
> `Idempotency-Key` header propagation described above remain unimplemented design
> targets (Phase 6, with the outbox).

### 2.3 Transactional Outbox Pattern for External Side Effects

For external side effects that do not support native idempotency keys, `chowki` employs the **Transactional Outbox Pattern**:

```
[ Database Transaction Boundary ]
1. Update Application / Step State
2. Insert Side-Effect Intent into `outbox` Table
------------------------------------------------- (Commit)
                                |
                        (Polling / CDC)
                                v
                   Async Outbox Worker
                                |
                  Calls External Service (e.g., Email / Webhook)
```

1. **Single Database Transaction:** State transitions and outbox records (containing target endpoint, payload, and correlation ID) are written in a single ACID database transaction.
2. **Asynchronous Hand-off:** An outbox worker or CDC (Change Data Capture) relay polls the `outbox` table, executes the external side effect, and marks the outbox record as `DELIVERED`.
3. **Guaranteed Local-External Consistency:** If the system crashes before committing the transaction, neither the state update nor the outbox record persists. If it crashes after commit, the outbox worker guarantees at-least-once delivery to the external service.
* [Source: https://james-carr.org/posts/2026-01-15-transactional-outbox-pattern/ (Accessed: 2026-08-08)]
* [Source: https://github.com/maheshkukreja/secure-patterns/tree/main/patterns/api_idempotency_keys (Accessed: 2026-08-08)]

---

## 3. Crash Recovery and Secure Resume Semantics

### 3.1 Crash Recovery Process in `chowki`

When an application node hosting a `chowki` agent crashes or restarts, active workflows recover through warm state evaluation:

1. **Incomplete Step Detection:** Upon process initialization, `chowki` queries the step store for workflow instances marked as `RUNNING`, `PENDING`, or `PAUSED_FOR_INPUT`.
2. **State Re-Hydration:** The workflow runner loads the latest deserialized state dictionary and step execution history from the relational database.
3. **Warm Execution Resume:** Execution jumps directly to the unfulfilled step. Succeeded steps (`COMPLETED`) are skipped entirely, eliminating duplicate LLM token consumption and re-executing zero redundant tool side effects.

### 3.2 Secure Resume Tokens (Human-In-The-Loop & Asynchronous Callbacks)

When an agent workflow pauses for human approval, user input, or external callbacks, `chowki` issues a secure, cryptographically signed **Resume Token**:

```
Resume Token Structure:
HMAC-SHA256(Secret, workflow_id | step_id | nonce | scope | exp)
```

* **HMAC Signing & Verification:** Resume tokens are signed using `HMAC-SHA256` with a server-side secret (`PRESIGN_SECRET`). Verification is stateless: the server re-computes the signature over the token payload and validates it using constant-time comparison (`timingSafeEqual`) to prevent timing attacks.
* **Token Expiration:** Every token includes an explicit expiration timestamp (`exp`). Tokens past their expiration window are immediately rejected.
* **Single-Use Nonce Validation:** Each token includes a cryptographically secure random nonce (`jti`). Upon token redemption, `chowki` registers the nonce in an atomic single-use store (`INSERT INTO used_nonces ...`). Replay attempts carrying a previously consumed nonce are blocked.
* **Scope Restriction:** Resume tokens carry explicit scope claims (e.g. `scope: "approve:transfer_funds"`). The resume endpoint checks that the token's scope permits the exact action being submitted.
* [Source: https://anam.ai/cookbook/ephemeral-webhook-tools (Accessed: 2026-08-08)]
* [Source: https://datatracker.ietf.org/doc/draft-thallapelly-oasnt/ (Accessed: 2026-08-08)]
* [Source: https://datatracker.ietf.org/doc/draft-coetzee-oauth-spt-txn-tokens/ (Accessed: 2026-08-08)]
* [Source: https://arxiv.org/html/2604.24920v2 (Accessed: 2026-08-08)]

### 3.3 Zero-Waste Warm Resume

Standard workflow engines (like Temporal or Restate) process human input by injecting external signals into a running workflow history, forcing replay of historical logs to reach the pending node.

`chowki` achieves **Zero-Waste Warm Resume**:

```
[ Paused State Snapshot in DB ]
               |
  Human Input / Fix Injected via Secure Resume Token
               |
               v
[ Deserialized State Memory ] ---> Resumes Directly at Exact Step
                                  (No Redundant LLM Calls / Zero Replay)
```

1. **Direct State Injection:** Human corrections, parameters, or approvals submitted via secure resume tokens are merged directly into the deserialized state context at the pending step boundary.
2. **Instant Execution Continuations:** The agent resumes execution instantly at the paused step.
3. **Zero Token & Compute Waste:** Because historical steps are persisted as immutable step records rather than re-simulated through code history replay, zero LLM prompt tokens are wasted re-processing previous turns, and zero duplicate side effects are executed.

---

## 4. Summary & Architectural Recommendations for `chowki`

1. **Adopt Step State Persistence over History Replay:** Use database-backed step persistence (`@chowki.step`, `@chowki.workflow`) to avoid Temporal's heavyweight cluster operations and strict code determinism rules.
2. **Enforce Database-Layer Idempotency Keys:** Require `Idempotency-Key` headers for all side-effectful tool calls and execute key reservations using atomic `INSERT ON CONFLICT` database operations.
3. **Deploy Transactional Outbox for Side Effects:** Combine state mutation and outbox record insertion inside a single database transaction, with an async outbox worker delivering external side effects.
4. **Utilize HMAC-Signed Single-Use Resume Tokens:** Secure human-in-the-loop approval workflows with HMAC-SHA256 tokens carrying explicit expirations, single-use nonces, and scope restrictions.
5. **Implement Zero-Waste Warm Resume:** Merge human inputs directly into deserialized step states to resume agent workflows instantly without replay bloat or wasted LLM prompt tokens.
