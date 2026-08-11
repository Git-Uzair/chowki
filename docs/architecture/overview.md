# Chowki Architecture Overview

`chowki` is an agent-native, in-process control plane and durable execution engine designed for Python and polyglot environments.

For complete research background and detailed designs, see the [Research Documents](../research/00-synthesis.md).

## Architectural Decision Records (ADRs)

- **[ADR-001](../research/00-synthesis.md#adr-001-polyglot-monorepo--language-neutral-spec-architecture):** Polyglot monorepo structure with language-neutral protocol schemas in `spec/v1/` as single source of truth.
- **[ADR-002](../research/00-synthesis.md#adr-002-compiled-serialization-engine-delta-persistence--canonical-hashing):** C-compiled `msgspec.Struct` MessagePack serialization, hybrid RFC 6902 delta persistence, and canonical SHA-256 blob extraction.
- **[ADR-003](../research/00-synthesis.md#adr-003-two-tiered-security-model-aead-encryption--automated-secret-redaction):** Mandatory two-tier secret redaction (regex + Shannon entropy) and AES-256-GCM AEAD encryption at rest bound with AAD headers.
- **[ADR-004](../research/00-synthesis.md#adr-004-in-process-lightweight-interceptor-model-chowkistep-chowkiworkflow--zero-waste-warm-resume):** Embedded in-process decorator SDK (`@chowki.step`, `@chowki.workflow`) providing zero-waste warm resume without external server daemons.
- **[ADR-005](../research/00-synthesis.md#adr-005-multi-tiered-loop--anomaly-guardrails-engine):** Multi-tiered loop detection (hash sets, Levenshtein, graph cycles) and dual soft/hard token/cost budget enforcement.
- **[ADR-006](../research/00-synthesis.md#adr-006-interactive-channel-gateway-architecture--state-patching):** Interactive Human-in-the-Loop channel gateway architecture (Console, Slack, Teams, REST) with HMAC/JWT verification and state patching.

## Module Map

- `chowki.config`: Process engine lifecycle, `ChowkiConfig`, `ChowkiEngine`, and process-global `configure()`.
- `chowki.core`: Run context management (`RunContext`), step and workflow decorators (`step`, `workflow`), control flow (`pause`, `recover_runs`, `resumable_runs`, `reissue_token`), warm resume (`resume`), and operator escape hatches for dead step attempts (`release_step`, `complete_step`).
- `chowki.state`: Hot-path snapshot pipeline (`SnapshotPipeline`), MessagePack codec, RFC 6902 delta patching (`DeltaChain`), automated secret redaction (`Redactor`), content-addressed blob storage (`BlobStore`, write-through backed by the storage adapter so blobs are as durable as the snapshots referencing them), and key management (`KeyRing`).
- `chowki.guardrails`: Loop detection (`LoopDetector`, fed via public `chowki.record_text` / `chowki.record_transition`), budget tracking (`BudgetTracker`, fed via `chowki.report_usage`), and anomaly breaker (`AnomalyBreaker`).
- `chowki.hitl`: Channel gateways (`ConsoleGateway`, `InMemoryGateway`), single-use HMAC token issuance (`TokenIssuer`), and audit logging.
- `chowki.storage`: Durable storage adapters (`SQLiteStorage`, `MemoryStorage`).
- `chowki.telemetry`: Structured JSON logging (`configure_logging`) and OpenTelemetry tracing and metrics (`span_for_step`, `record_snapshot_metrics`).

## Suspension Model

A run suspends durably (status `PAUSED`, single-use HMAC resume token, gateway
notification) through two paths, distinguished by `PauseRequest.origin`:

- **`"gate"`** — the workflow body called `chowki.pause()`. On resume, the re-execution
  falls through the gate, which re-applies any human `EDIT` patch at that point in the
  body.
- **`"auto"`** — a guardrail/breaker decision (ADR-005): a step that exhausted its
  retries, loop detection, or a hard budget breach with `hard_budget_action="PAUSE"`.
  `WorkflowPaused` is raised chained from the original error. There is no gate in the
  body, so resume seeds the re-execution with the decided (patched) state directly, and
  the failed step retries under the failed-step semantics below.

Idempotent step recovery is fail-safe: a cleanly `FAILED` attempt retries on
re-invocation (its record proves the attempt is accounted for), while a mid-step death
(`RUNNING` record) refuses to re-execute until an operator confirms the side effect's
fate via `chowki.release_step` (it did not happen) or `chowki.complete_step` (it did,
with the supplied result). A lost or burnt resume token is re-minted with
`chowki.reissue_token(run_id)`.

## Storage & Concurrency Model

`chowki` uses a pluggable `StorageAdapter` architecture for state persistence.

### SQLite Write Contention (Risk R7)

The default embedded `SQLiteStorage` adapter is configured for single-process concurrency and local durability:

- **Configuration:** Uses WAL mode (`PRAGMA journal_mode=WAL`), a 5 s busy timeout (`PRAGMA busy_timeout=5000`), `synchronous=NORMAL`, and process-level write locking (`threading.Lock`).
- **Single-Process Focus:** Process-level write locks manage thread safety within a single process.
- **Multi-Process Limitation:** Multi-process deployments operating against a single SQLite database file may encounter `database is locked` errors under concurrent write contention.
- **Pluggable Architecture:** Multi-process and distributed production deployments are intended to use pluggable `StorageAdapter` implementations (PostgreSQL or Redis adapters in roadmap Phase 5 — see `docs/plans/00-roadmap.md`). Connection pooling is intentionally omitted in `SQLiteStorage`.

## Performance Budgets

Every hot-path operation in `chowki` is bounded by normative execution budgets defined in `python/chowki/tests/benchmarks/budgets.py`. Benchmarks enforce these budgets with a `1.5x` CI tolerance multiplier:

| Metric | Budget | Description |
|---|---|---|
| `redaction_1mb_ms` | 0.8 ms | 1 MiB state tree secret redaction walk |
| `encode_1mb_ms` | 0.6 ms | MessagePack serialization of 1 MiB state |
| `canonical_hash_1mb_ms` | 0.35 ms | SHA-256 canonical payload hashing |
| `encrypt_1mb_ms` | 0.4 ms | AES-256-GCM AEAD payload encryption |
| `dispatch_ms` | 0.2 ms | Storage sink dispatch and telemetry metrics |
| `snapshot_total_1mb_ms` | 3.5 ms | End-to-end 1 MiB snapshot pipeline total |
| `delta_diff_1mb_ms` | 1.0 ms | RFC 6902 JSON Patch diff computation |
| `warm_resume_base_plus_10_deltas_ms` | 2.5 ms | Cold-path state reconstruction from 1 Base + 10 Deltas |
| `step_decorator_overhead_us` | 50.0 µs | Per-step decorator interceptor overhead |
| `loop_detect_step_us` | 100.0 µs | Per-step loop detection verification |
| `budget_track_step_us` | 20.0 µs | Per-step token and cost budget accounting |
