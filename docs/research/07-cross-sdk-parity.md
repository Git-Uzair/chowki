# Cross-SDK Parity Contract & Operational Semantics

**Project:** `chowki`
**Date:** 2026-08-11
**Document:** `docs/research/07-cross-sdk-parity.md`
**Status:** Normative — extracted from the verified Phase 1 Python implementation
**Audience:** The Phase 3 (`@chowki/core` Node/TypeScript) plan generator, and Phase 2's
`spec/v1/` formalisation. Where this document and older research prose disagree, this
document wins: it describes what the shipped, tested Python SDK actually does.

The Python SDK is the reference implementation. "MUST" below means: the Node SDK must
produce **byte-identical or verification-compatible** output, or explicitly document a
divergence in `spec/v1/`. Phase numbers refer to `docs/plans/00-roadmap.md`.

---

## 1. Canonical JSON and content hashing

`content_hash(value)` = `"sha256:" + hex(sha256(canonicalize(value)))`.

`canonicalize` is an RFC 8785 subset:

1. **NFC-normalize** every string key and value recursively. Two keys that collide
   after NFC normalization are an error (the hash refuses, it does not pick one).
2. **Key ordering:** if any dict key anywhere in the tree contains an astral character
   (> U+FFFF), sort keys by their UTF-16 big-endian code-unit encoding (RFC 8785
   order); otherwise plain code-point sort (identical for BMP-only keys).
3. Serialize with separators `","`/`":"`, no ASCII escaping (`ensure_ascii=False`),
   UTF-8 encode. Non-finite numbers are an error.
4. **Number formatting — KNOWN DIVERGENCE (Phase 3 work item):** Python emits
   `repr(float)`; RFC 8785 mandates ECMAScript `Number::toString`. These differ for
   extreme magnitudes (e.g. `1e21`, `1e-7`). Phase 3 MUST implement ES formatting on
   BOTH sides (marked `TODO(phase-3)` in `chowki/state/canonical.py`). Until then,
   cross-SDK content hashes are only guaranteed for trees whose floats round-trip
   identically — pin the ES algorithm in `spec/v1/` before any Node code exists.
5. Additional cross-language pins for `spec/v1/`: integers beyond 2^53 (Python ints
   are exact; JS needs BigInt policy — recommend: reject > 2^53-1 in canonical JSON),
   and `bytes` values never reach `canonicalize` (they are not JSON; step signatures
   fold them to type markers, §4).

`content_hash` is used for: blob argument dedup keys, tool-loop signatures, args-hash
(§4), and resume-time `state_hash_before/after`. It is the **identity** hash.

## 2. The two hash semantics (do not conflate)

- **`content_hash`** (§1): canonical, order-independent, comparable across SDKs.
- **`SnapshotEnvelope.state_hash`** = `"sha256:" + hex(sha256(payload))` where
  `payload` is the **pre-encryption MessagePack bytes as this writer encoded them**.
  MessagePack maps preserve insertion order, so this hash is writer-specific: it is an
  **integrity** check (verified on unseal), NOT a cross-SDK identity. Two SDKs holding
  the same logical state may produce different `state_hash` values legitimately. Any
  cross-SDK state comparison must go through `content_hash`.

## 3. Snapshot envelope, encryption, and pipeline order

Envelope fields (wire order is part of the format; `spec/v1/snapshot-envelope.schema.json`):
`v` (int, schema version, currently 1), `run_id`, `workflow`, `tenant_id`,
`step_index` (int, storage key — monotonic per run, never reused), `kind`
(`"base"`/`"delta"`), `created_at_utc` (ISO-8601, `Z` suffix), `state_hash` (§2),
`payload` (bytes), `parent_hash` (nullable), `key_id` (nullable), `nonce` (nullable
bytes), `codec` (`"msgpack"`).

Hot-path order (MUST be preserved): **redact + blob-extract (one walk) → base/delta
select → MessagePack-encode → hash → encrypt (optional) → dispatch**. Hash is over the
**plaintext** encoding; encryption replaces `payload` and sets `key_id` + `nonce`.

**Durability contract:** **synchronous dispatch is the contract** — state snapshots are flushed and committed synchronously to storage before a step returns; SIGKILL at any point after a step returns loses zero acknowledged step state.

Encryption: AES-256-GCM, 32-byte keys, 96-bit random nonce per encryption, AAD =
`"{tenant_id}:{run_id}:v{v}"` UTF-8 (binds tenant/run/schema — blocks cross-tenant
ciphertext transplantation). Decrypt failures are indistinguishable (wrong key / wrong
AAD / tampering). Keys come from a `KeyRing` (active key id, e.g. `"k1"`); the env
bootstrap is `CHOWKI_MASTER_KEY` = base64 of 32 bytes. Encryption is **opt-in and off
by default** (see amendment in `02-serialization.md`); redaction is always on.
Envelope decode MUST reject `v` newer than the SDK's `SCHEMA_VERSION` and run the
registered migration chain for older `v` (registry keyed by `from_version`, each hop
+1).

Schema-version unseal order: version check → integrity hash check → decode → migrate.

## 4. Step identity, args-hash, and memoisation

- **Step id** = `f"{name}#{ordinal}"` where `ordinal` counts calls of that `name`
  within one execution of the workflow body (per-name counter, resets every
  (re-)execution). Pause gates use the shared ordinal stream: `f"pause#{ordinal}"`
  from a per-run monotonic counter that also resets on re-execution. **Consequence
  (MUST document in both SDKs):** renaming a step, reordering calls, or adding a call
  before an existing one changes identity — completed work is then re-executed, and
  side effects re-fire. Workflow-definition versioning is a Phase 6 research item.
- **args-hash** = `content_hash({"name": name, "args": S(args), "kwargs": S(kwargs)})`
  where `S` sanitizes: NFC-normalize dict keys (via `str(key)`); lists/tuples → arrays;
  sets/frozensets → array sorted by the `repr` of the sanitized member (Node: define
  an equivalent total order in `spec/v1` — e.g. sort by canonical JSON of the member);
  non-finite floats → `"<float>"`-style type markers; container cycles → `"<cycle>"`;
  **any other type → expanded structurally, in this normative order:** shallow
  field-by-field unpacking of Structs and dataclasses (`structs.asdict` /
  `dataclasses.fields`) → `to_builtins` (attrs, enums, bytes, datetimes, UUIDs,
  Decimals) → `model_dump` (Pydantic, duck-typed, never imported) → `__dict__`
  (ordinary objects, non-empty only) → `f"<{TypeName}>"` marker. Every expansion is
  re-sanitized by `S` and wrapped as `{f"<{TypeName}>": expansion}`, so a Struct never
  hashes identically to a plain dict with equal fields. Three rules are normative,
  not incidental: **(1)** field unpacking MUST precede any whole-object
  conversion and MUST be shallow, so an unordered collection in a field reaches `S` as
  a set and is put in `S`'s total order rather than in the host runtime's iteration
  order (Python: `PYTHONHASHSEED` salts that order, so the resume key would otherwise
  move on every process restart); **(2)** whole-object conversion MUST precede
  attribute scraping, because an enum member's attribute table holds enum machinery
  (including its own class) instead of its value. Attribute probes MUST swallow *any*
  error a lazy proxy raises and fall through to the marker; **(3)** expansion MUST stop
  at a fixed nesting depth (Python: 100 levels) and emit the marker there, because
  expansion is recursive and a chain of objects each holding the next would otherwise
  exhaust the host stack. That cut MUST be made on the value's depth within the
  argument, never on the runtime's remaining stack headroom, or the same argument would
  hash differently depending on how deep the caller was. The Node SDK MUST reproduce
  the same wrapper shape, the same expansion order and the same depth cap (its
  equivalents: class fields → `toJSON` → plain-object conversion → own enumerable
  properties → marker).
- **`<TypeName>` collapse caveat:** only a value none of those steps can describe (a C
  extension object, a socket), or one nested past the depth cap, collapses. Two instances of such a type still hash
  identically, so a memoised result can replay for logically different arguments —
  therefore SDKs MUST log a warning naming the run, the step and the sorted collapsed
  type names (Python: `chowki_step_args_opaque`) rather than collapsing silently. A
  COMPLETED record whose stored `args_hash` differs from the current call logs a warning
  and re-executes.
- Memoisation: only a COMPLETED record with equal `args_hash` and
  `result_replayable=true` short-circuits; its stored result (MessagePack) is decoded
  and returned. `result_replayable=false` (result was not encodable; a diagnostic
  marker `{"__chowki_unserializable__": "<TypeName>"}` is stored) means the body runs
  again without re-claiming.

## 5. Idempotency claims and recovery semantics

- **Key** = hex(HMAC-SHA256(`resume_secret`, `f"{run_id}|{step_id}|{args_hash}"`)).
  `resume_secret` is config-supplied or minted once into the storage secret slot
  `"resume"` (32 bytes) — it MUST survive process death, or a recovering process
  cannot recognise its own claims.
- Claim = atomic insert-if-absent keyed by the key, storing `args_hash`. Claiming an
  existing key with a **different** `args_hash` is a hard error (payload reuse).
- **Recovery matrix (normative, ADR-004 refinement):**
  - existing record COMPLETED + args match → memoise (never re-claim);
  - existing record **FAILED** + args match + claim refused → **proceed** (the claim
    provably belongs to this run's own finished attempt — the key embeds
    run_id|step_id|args_hash under our secret — and its fate is known);
  - existing record RUNNING, or claim with no record (mid-step death: side-effect
    fate unknown) → **refuse** with an error naming the escape hatches;
  - operator escape hatches: `release_step(run_id, step_id)` (deletes the claim —
    "the effect did not happen, run the body again") and
    `complete_step(run_id, step_id, result)` (records COMPLETED with the supplied
    result — "it happened; memoise this"). Both are public API and MUST exist in
    every SDK.
- Single-writer-per-run is assumed everywhere (pipeline state, ordinal counters);
  multi-process ownership/leasing is Phase 5.

## 6. Resume tokens (wire format is cross-SDK verifiable)

`token = b64url_nopad(msgpack(claims)) + "." + b64url_nopad(HMAC_SHA256(secret, body_ascii))`

Claims struct (field order fixed): `run_id`, `step_id`, `permitted_actions`
(array of strings), `nonce` (uuid4 hex), `iat` (unix seconds, int), `exp` (int),
`allowed_roles` (array, default empty; not yet enforced — Phase 4). Default TTL
86400 s. Verification order: signature (constant-time compare) → decode → expiry
(`exp <= now` fails) → requested action ∈ `permitted_actions` → **consume nonce**
(atomic insert; lifetime single-use — consumed nonces are never garbage-collected back
to replayability). A burnt or lost token is re-minted by `reissue_token(run_id)` from
the stored `PauseRequest` (same step, same actions, fresh nonce; re-notifies the
gateway by default). Note: today the nonce is consumed before the run-status check in
`resume()`, so a resume attempt against a non-paused run burns that token — recover
via `reissue_token`.

## 7. Redaction (two tiers + key-name tier) — placeholder derivation is normative

- **Placeholder** = `f"[REDACTED:{kind}:{short}]"` where `short` =
  hex(HMAC-SHA256(`redaction_key`, secret_utf8 (encode errors=replace)))[:8], and
  `kind` is the matching pattern's name lowercased with non-`[a-z0-9_]` → `"_"`.
  Regex: `\[REDACTED:[a-z0-9_]+:[0-9a-f]{8}\]`. Redaction is a **fixpoint**: existing
  placeholders are masked during re-scans and survive verbatim.
- **`redaction_key`** defaults to the storage secret slot `"redaction"` (32 bytes,
  persisted per deployment) so placeholders are stable across restarts; explicit
  config overrides. Cross-SDK placeholder equality requires sharing this key (same
  storage → same key → same placeholders).
- **Tier 1 patterns** (combined alternation; names are the `kind` values):
  `private_key`, `jwt`, `openai_proj`, `sk-proj-…`, `anthropic` (`sk-ant-…`),
  `openai` (`sk-…`), `stripe`, `aws_access` (AKIA/ASIA), `aws_secret`, `github`
  (`ghp_`), `slack` (`xox[baprs]-`), `bearer`, `basic`, `uri_userinfo`
  (`scheme://user:pass@host` → userinfo only, host survives). Port the exact
  expressions from `chowki/state/redact.py` and mind flavor differences (Python `re`
  vs V8 RegExp: lookbehind in `uri_userinfo`, `[\s\S]` idioms). User-supplied
  `extra_patterns` disable the inert-screen fast path.
- **Tier 2 entropy:** candidate tokens match `[A-Za-z0-9+/=_\-.!@#$%&*]{N,}` with
  `N = max(min_token_len=12, ceil(2^threshold))` (threshold 4.5 → N = 23); token must
  contain an ASCII digit; safe-filters exempt path-like strings (`/` or `\`),
  UUIDs (len 36), pure hex of len 16/32/40/64, parseable numbers, and
  `REDACTED:`-prefixed text. Shannon entropy is computed over **code points**
  (Node MUST NOT compute over UTF-16 units), threshold ≥ 4.5 bits/char. Strings
  longer than `entropy_max_scan_bytes` (4096) skip tier 2 and increment a counter.
- **Structure tier:** dict keys ≥ 3 chars matching
  `(?i)(api[_-]?key|secret|token|password|passwd|auth(?:orization)?|credential|private[_-]?key|access[_-]?key)`
  redact the **whole value** as kind `key_name` (placeholder-valued values are kept).
  Safe-key set (`role`, `content`, `messages`, …) and safe-value set (`user`,
  `assistant`, `system`, …) skip scanning; strings under 8 chars are never scanned.
- **Binary boundary (normative):** bytes-like leaves that decode as strict UTF-8 are
  text-redacted and re-encoded (bytearray stays bytearray; memoryview lands as
  bytes); non-UTF-8 binary passes through unchanged — the documented exemption. Set
  and frozenset members are redacted, container type preserved.
- Human patches are redacted **once**, judging each op's value under the key its
  `path` targets (RFC 6901 last segment, unescaped), and only that redacted form is
  applied, hashed, and audited (`redact_patch`).

## 8. Blobs and delta chains

- Blob ref = `"ref:sha256:" + hex(sha256(blob_bytes))`. User strings that already
  start with a ref/escape prefix are escaped with `"ref-lit:"` (strip one level on
  inline). Extraction rule: only string leaves; skip if `len(str) <= threshold//4`
  (a code point is ≤ 4 UTF-8 bytes); extract if UTF-8 length (`surrogatepass`)
  > threshold (default 4096). Dict keys are never blob-extracted.
- **Durability ordering (normative, from the D1 fix):** a blob MUST be durable in the
  storage adapter before any snapshot referencing it is dispatched. The engine's
  BlobStore writes through to `storage.put_blob` on first sight and falls back to
  `storage.get_blob` on cache miss; a truly missing blob is an integrity error.
- Delta chain: diffs are RFC 6902 emitted with `add`/`replace`/`remove` only; apply
  accepts all six ops (human patches may use `test`/`move`/`copy`). Compaction to a
  new BASE when chain depth ≥ 50 or accumulated encoded delta bytes >
  0.20 × base bytes. Resume loads newest BASE + subsequent DELTAs
  (`step_index >= max(base index)`).

## 9. Suspension, auto-pause, and resume seeding

- `PauseRequest.origin` ∈ {`"gate"`, `"auto"`} (appended field, default `"gate"`).
- **Gate** (`chowki.pause()`): freezes state (snapshot at the boundary), persists
  PAUSED + pause request + usage, mints token, notifies gateway (notify failures are
  logged, never fatal), raises `WorkflowPaused`. On resume, the re-execution falls
  through the decided gate and **re-applies the human patch at the gate** (patches
  come from the audit log, so every later resume re-applies them too).
- **Auto** (ADR-005 wired): fires for exactly two shapes — an exception a step's
  breaker stamped `chowki_action=PAUSE` (bound to the failed step via
  `chowki_step_id`), and a bare `InfiniteLoopDetected`/`BudgetExceeded` raised outside
  any step (breaker consulted at the workflow boundary). Suspends identically
  (`permitted_actions = APPROVE/REJECT/EDIT`, payload carries error class + message),
  raises `WorkflowPaused` **chained from the original error**. Everything else stays
  FAILED. If suspension itself fails, fall back to the plain failure path.
- **Resume seeding rule (normative):** gate-origin resumes seed the replay with the
  state the human **reviewed** (the gate re-applies the patch in-body); auto-origin
  resumes seed the **decided (patched, redacted)** state — there is no gate to apply
  it. An EDIT whose patch changes the state also persists a fresh BASE as the state
  of record before re-execution.
- Breaker action matrix: RATE_LIMIT/TOOL_EXECUTION → RETRY (full-jitter backoff:
  `uniform(0, min(retry_max=30s, base=1s × 2^attempt))`) until `max_auto_retries=3`,
  then PAUSE; VALIDATION → REASK until `max_validation_reasks=2`, then PAUSE;
  CONTEXT_WINDOW → SUMMARIZE once, then ABORT; INFINITE_LOOP → PAUSE; BUDGET →
  `hard_budget_action` (`"PAUSE"` default, `"ABORT"` opt-in). REASK/SUMMARIZE remain
  application signals (attached to the exception), not engine behaviors.
- Budget re-breach on resume: a resumed run re-seeds its budget from persisted usage,
  so an unchanged ceiling re-pauses on the next report — the operator raises the
  budget (new engine config) and approves. Guardrail feeds are public API:
  `report_usage(Usage|int)`, `record_text(text)`, `record_transition(src, dst)`.
- Run statuses: PENDING/RUNNING/PAUSED/COMPLETED/FAILED/ABORTED/REJECTED.
  `recover_runs` re-arms RUNNING → PENDING only (never FAILED). Ordinals reset on
  every (re-)execution; **snapshot indices never do** — they continue above the max
  stored index so replays cannot overwrite history.
- **`RunRecord.inputs` (wire field, appended, default absent/null).** MessagePack of
  `{"args": [...], "kwargs": {...}}`, the arguments of the call that opened the run,
  **redacted before encoding** (§7) exactly like every other persisted payload. Written
  **only when the run record is created** — re-invoking an existing `run_id` is a warm
  resume and MUST NOT overwrite it — and durable before the first step runs. Every
  re-invocation (`resume`/`aresume`/`rerun`) replays it as
  `workflow_fn(*args, run_id=run_id, **kwargs)`, which is what makes a resumed step hit
  the same `args_hash` (§4) instead of a default. Absent/null means "fall back to the
  signature defaults": the run took no arguments, predates the field, or passed a value
  the codec cannot encode (log `chowki_workflow_args_not_persisted`, never fail the
  run). A re-invocation with no stored arguments whose signature still has a required
  parameter is a state error, not a language-level TypeError. Types round-trip through
  MessagePack (tuple → list), same as step results.

## 10. Money, usage, and time

- `Usage` = `input_tokens`, `output_tokens`, `reasoning_tokens`,
  `cached_input_tokens`, `cost_usd` (float64). `billable_tokens` excludes cached
  input. Accumulation is plain addition; runs carry cumulative usage across pauses.
- **`cost_usd` is an IEEE-754 double by contract.** Both SDKs use doubles (Python
  float ≡ JS number), so parity holds, but `spec/v1/` SHOULD note the accumulation-
  error caveat and that a fixed-point representation (micro-USD int) is the escape
  path if it ever matters. Where per-model prices come from is an open Phase 6 item.
- Timestamps: ISO-8601 UTC with `Z` suffix (`2026-08-11T12:00:00.000000Z` shape);
  token `iat`/`exp` are unix **seconds** (int).

## 11. Storage adapter contract (summary for the Node port)

`put_run/get_run/list_runs(status?)`, `put_step/get_step/list_steps` (ordered by
ordinal), `put_snapshot/list_snapshots/snapshots_for_resume/max_snapshot_index`,
`claim_idempotency_key(key, args_hash)` (atomic, single winner, payload-reuse error) /
`release_idempotency_key(key)`, `get_or_create_secret(name)` (32 bytes, first writer
wins, durable), `consume_nonce(nonce, expires_at)` (lifetime single-use),
`put_blob/get_blob` (content-addressed, idempotent), `append_audit/list_audit`
(append-only — no delete API may exist), `put_gateway_handle/get_gateway_handle`,
`close()`. Records are stored as opaque encoded blobs plus queryable columns
(run_id, status, ordinal, step_index, kind); adapters MUST copy on read/write (no
shared mutable state with callers). `RunRecord.inputs` (§9) needs no adapter change: it
is a field on an existing struct inside the opaque blob, not a new operation. Known gap for Phase 2: multi-write transitions
(resume = audit + run + snapshot) are not atomic today; the contract needs an atomic
transition API before the Postgres adapter.

## 12. Secret slots (storage-persisted, 32 bytes each)

| Slot | Purpose |
|---|---|
| `"resume"` | HMAC key for step idempotency keys when no explicit `resume_secret` is configured |
| `"redaction"` | HMAC key for redaction-placeholder short hashes (unless configured explicitly) |

Both are minted on first use (first writer wins) and MUST outlive any process.
Precedence: an explicitly configured `resume_secret` is used verbatim for **both**
idempotency keys and token signatures. Without one, idempotency keys fall back to the
durable `"resume"` slot (crash recovery still works), but resume tokens are signed
with an **ephemeral per-process key** and the engine warns that they will not verify
after a restart — supply a `resume_secret` in production.

## 13. Explicitly OUT of this contract (do not invent in Phase 3)

The following are known absences, not oversights. The Node SDK must ship **without**
them, matching Python, until the owning roadmap phase designs them once for both SDKs:

- **Concurrency inside one run** — `Promise.all`/`asyncio.gather` over steps is
  UNDEFINED BEHAVIOR: step ordinals, the shared state dict, and the linear delta chain
  all assume one step at a time. Single-writer-per-run, one task at a time, is the
  contract. Parallel steps need deterministic branch keys (Phase 6). Document this
  loudly in the Node README — `Promise.all` is far more idiomatic there than `gather`
  is in Python, so Node users will hit it first.
- **Durable timers / sleep, external signals or events** waking a run (the only wake
  paths are human decisions and re-invocation) — Phase 6.
- **Child workflows, cancellation (`cancel_run`), run-listing/query API beyond
  `list_runs(status)`** — Phases 2/6.
- **Retention/GC** — nothing deletes runs, snapshots, blobs, nonces, or audit rows
  yet; Phase 2 owns the maintenance operations. Adapters must still never expose an
  audit delete.
- **Transactional outbox, downstream `Idempotency-Key` propagation, `PENDING` claim
  states** — Phase 6.
- **Role enforcement / approval policy** (`allowed_roles` is carried, unchecked),
  channel adapters (Slack/Teams/REST) — Phase 4.
- **Multi-process ownership/leasing** — Phase 5; `recover_runs` on boot assumes the
  booting process owns everything it finds.
- **Model-call features**: auto-summarize on context overflow, model downgrade on
  soft budget, semantic-embedding loop detection — Phase 6, if ever; all require
  model calls the engine deliberately never makes.
