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
- `chowki.core`: Run context management (`RunContext`), step and workflow decorators (`step`, `workflow`), control flow (`pause`, `recover_runs`, `resumable_runs`), and warm resume (`resume`).
- `chowki.state`: Hot-path snapshot pipeline (`SnapshotPipeline`), MessagePack codec, RFC 6902 delta patching (`DeltaChain`), automated secret redaction (`Redactor`), content-addressed blob storage (`BlobStore`), and key management (`KeyRing`).
- `chowki.guardrails`: Loop detection (`LoopDetector`), budget tracking (`BudgetTracker`), and anomaly breaker (`AnomalyBreaker`).
- `chowki.hitl`: Channel gateways (`ConsoleGateway`, `InMemoryGateway`), single-use HMAC token issuance (`TokenIssuer`), and audit logging.
- `chowki.storage`: Durable storage adapters (`SQLiteStorage`, `MemoryStorage`).
- `chowki.telemetry`: Structured JSON logging (`configure_logging`) and OpenTelemetry tracing and metrics (`span_for_step`, `record_snapshot_metrics`).

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
