# Python Agent State Serialization & Safety Analysis (2026)

**Project:** `chowki`  
**Date:** 2026-08-08  
**Scope:** Phase 1 Workstream 2 — Safely Snapshotting Python Agent State  

---

## 1. Serialization Methods Comparison & Vulnerability Analysis

### 1.1 Pickle and Cloudpickle Vulnerability & Risk Profile

Python's native `pickle` and its distributed execution variant `cloudpickle` remain widely used in legacy agent prototypes due to their ability to serialize arbitrary Python functions, lambdas, and object graphs. However, using `pickle` or `cloudpickle` for persisting agent state in `chowki` introduces critical security vulnerabilities and operational hazards.

#### 1. Remote Code Execution (RCE) via Insecure Deserialization
`pickle` is an executable bytecode format interpreted by the Pickle Virtual Machine (PVM). During deserialization via `pickle.loads()`, custom object reconstruction hooks such as `__reduce__()`, `__reduce_ex__()`, and `__setstate__()` are evaluated *before* any application-level type checking or validation occurs. An attacker who tampers with a state snapshot can inject arbitrary system execution commands (e.g., `os.system` or `subprocess.Popen`) into the payload.

Recent 2026 security advisories demonstrate the severity of this vulnerability in production platforms:
* **CVE-2026-25874 (LeRobot Unauthenticated RCE):** Hugging Face's LeRobot robotics platform exposed gRPC handlers (`SendPolicyInstructions`, `SendObservations`) that called `pickle.loads()` on unauthenticated network input, enabling remote attackers to execute arbitrary shell commands.
* **Google ADK Security Advisory (#5634):** The Google Agent Development Kit (ADK) suffered from RCE in its session database runtime (`v0` schema) where poisoned BLOB rows containing pickle payloads executed malicious commands during session reads and database migrations (`adk migrate session`).
* **MLflow Deserialization Vulnerabilities (CVE CWE-502 / HiddenLayer Advisory):** MLflow custom object loading in TensorFlow and LightGBM modules utilized `cloudpickle.load()`, allowing malicious pickle payloads to achieve cluster compromise upon loading.
* **Fugue RPC Remote Execution (PYSEC-2026-1399):** Unsanitized `cloudpickle.loads()` calls in distributed RPC handlers enabled full node takeovers.

#### 2. Scanner Bypasses via Implementation Discrepancies
Security mitigations that attempt to safely inspect pickle payloads via static analysis or disassemblers (e.g., Hugging Face pickle scanners) are prone to parser differential bypasses. Research from *PICKLEFUZZER* (2026) identified 14 implementation discrepancies between CPython's three native pickle engines (`pickle` in Python, `_pickle` in C, and `pickletools` disassembler). Attackers can craft opcodes that pass scanner inspection under `pickletools` but execute malicious payloads when parsed by `_pickle.loads()`.

#### 3. Schema Drift and Brittle Module Bindings
`pickle` serializes object references by fully qualified module path and class name (e.g., `my_agent.tools.CustomTool`). If a developer renames a module, refactors a function signature, updates a class attribute, or upgrades Python minor versions, deserialization fails with `AttributeError`, `ImportError`, or `UnpicklingError`. This makes `pickle` unsuitable for long-term state storage across deployments.

#### 4. Explicit Maintainer Discouragement
The maintainers of `cloudpickle` explicitly state in their documentation:
> *"Using `cloudpickle` for long-term object storage is not supported and strongly discouraged."*

Furthermore, `pickle` payloads lack embedded version headers, checksums, or schema evolution capabilities, making backward-compatible state migrations impossible.

* [Source: https://github.com/cloudpipe/cloudpickle (Accessed: 2026-08-08)]
* [Source: https://www.resecurity.com/blog/article/cve-2026-25874-hugging-face-lerobot-unauthenticated-rce-via-pickle-deserialization (Accessed: 2026-08-08)]
* [Source: https://github.com/google/adk-python/issues/5634 (Accessed: 2026-08-08)]
* [Source: https://arxiv.org/html/2605.15084 (Accessed: 2026-08-08)]
* [Source: https://osv.dev/vulnerability/PYSEC-2026-1399 (Accessed: 2026-08-08)]

---

### 1.2 High-Performance Serialization Engines: `msgspec` vs `pydantic-v2` vs `orjson`

To eliminate the security risks of `pickle`, `chowki` evaluates three modern compiled serialization libraries: `msgspec`, `pydantic-v2` (`pydantic-core`), and `orjson`.

```
+---------------------------------------------------------------------------------------------------+
|                                 SERIALIZATION ENGINE PERFORMANCE                                  |
+-------------------+-----------------+-----------------------+---------------------+---------------+
| Engine            | Core Language   | Encoding Speed (rel)  | Decoding Speed (rel)| Memory Layout |
+-------------------+-----------------+-----------------------+---------------------+---------------+
| msgspec           | C               | 1.0x (Fastest)        | 1.0x (Fastest)      | Struct slots  |
| orjson            | Rust            | ~1.1x - 1.5x          | ~1.3x - 2.0x        | Dict buffer   |
| pydantic-v2       | Rust / CPython  | ~10.0x - 20.0x slower | ~5.0x - 15.0x slower| BaseModel dict|
+-------------------+-----------------+-----------------------+---------------------+---------------+
```

#### Detailed Library Profiles

1. **`msgspec` (C Engine - BSD-3 License):**
   * **Architecture:** Written in pure C, utilizing native `msgspec.Struct` types backed by C-level `__slots__` memory layouts.
   * **Single-Pass Decode & Validate:** `msgspec` validates types *during* JSON or MessagePack decoding in a single C-level pass. It decodes and validates structured data faster than `orjson` can decode raw JSON into untyped Python dictionaries alone.
   * **Memory Efficiency:** Uses up to **2.5x - 9x less memory** than `pydantic-v2` and `orjson` by avoiding temporary intermediate heap buffers and caching string keys in C memory.
   * **Formats:** Native high-performance support for both text JSON and binary MessagePack (`msgspec.msgpack`).

2. **`pydantic-v2` (`pydantic-core` Rust Engine - MIT License):**
   * **Architecture:** Core validation logic implemented in Rust (`pydantic-core`), while models inherit from `pydantic.BaseModel`.
   * **Validation vs Serialization Gap:** While `pydantic-v2` is 5x to 50x faster than `pydantic-v1`, `BaseModel.model_dump_json()` incurs significant CPU overhead on nested agent state structures due to field-by-field Python/Rust boundary conversions, UUID handling, and datetime ISO formatting.
   * **Strengths:** Unmatched ecosystem integration (FastAPI, JSON Schema export/import, complex custom field validators).

3. **`orjson` (Rust Engine - Apache-2.0 / MIT License):**
   * **Architecture:** Fast Rust-based JSON serializer supporting `dataclass`, `datetime`, `numpy`, and `UUID` objects natively.
   * **Limitations:** Performs raw JSON parsing into Python `dict` objects without schema validation. Decoding large JSON blobs requires copying payloads into temporary memory buffers, increasing RAM consumption on large agent conversation histories.

#### Tradeoffs & Capability Matrix

| Feature / Dimension | `msgspec` (0.21.1) | `pydantic-v2` (2.12+) | `orjson` (3.11+) |
| :--- | :--- | :--- | :--- |
| **Primary Language** | C | Rust (`pydantic-core`) | Rust |
| **Model Type** | `msgspec.Struct` (`__slots__`) | `pydantic.BaseModel` | Untyped `dict` / `dataclass` |
| **Serialization Formats** | JSON, MessagePack, TOML, YAML | JSON, Python `dict` | JSON |
| **Encode Speed (Relative)** | **Baseline (1x)** | ~10x - 20x slower | ~1.1x - 1.5x slower |
| **Decode + Validate Speed**| **Baseline (1x)** | ~5x - 15x slower | N/A (No schema validation) |
| **Memory Overhead** | **Lowest** (C struct layout) | High (`BaseModel` metadata) | Medium (Temporary buffer) |
| **Schema Validation** | Single-pass during decode | Post-decode Rust pass | None |
| **On-Disk Library Size** | **0.46 MiB** | 6.71 MiB (14.6x larger) | ~1.2 MiB |

* [Source: https://msgspec.dev/benchmarks (Accessed: 2026-08-08)]
* [Source: https://www.danilchenko.dev/posts/msgspec-vs-pydantic/ (Accessed: 2026-08-08)]
* [Source: https://gist.github.com/jcrist/d62f450594164d284fbea957fd48b743 (Accessed: 2026-08-08)]
* [Source: https://github.com/tktech/json_benchmark/ (Accessed: 2026-08-08)]

---

## 2. Schema Evolution, Delta Persistence & Content Addressing

### 2.1 Versioned, Forward-Compatible Snapshot Schemas

To prevent schema drift and enable zero-downtime upgrades when agent state structures evolve, `chowki` implements an explicit versioned snapshot header envelope.

#### Snapshot Envelope Schema
Every serialized state payload in `chowki` is wrapped in a strict metadata header:

```json
{
  "v": 1,
  "agent_id": "ag_9f823a1b",
  "step": 14,
  "created_at_utc": "2026-08-08T06:22:00Z",
  "state_hash": "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "payload": {
    "memory": {"user_goal": "Optimize pipeline"},
    "messages": [...]
  }
}
```

#### Migration Registry Architecture
`chowki` provides a deterministic schema migration registry. When reading an older snapshot version (e.g., `v=1`), `chowki` executes a sequential chain of migration functions to upgrade the payload to the current SDK schema version (`v=N`) prior to runtime instantiation:

```python
# chowki schema migration registry pattern
from typing import Callable, Dict, Any

MIGRATION_REGISTRY: Dict[int, Callable[[Dict[str, Any]], Dict[str, Any]]] = {}


def register_migration(from_version: int):
    def decorator(fn: Callable[[Dict[str, Any]], Dict[str, Any]]):
        MIGRATION_REGISTRY[from_version] = fn
        return fn

    return decorator


@register_migration(from_version=1)
def migrate_v1_to_v2(payload: Dict[str, Any]) -> Dict[str, Any]:
    # Transformation: Migrate flat 'memory' dict into structured 'agent_memory'
    payload["agent_memory"] = {"short_term": payload.pop("memory", {})}
    payload["v"] = 2
    return payload
```

* [Source: https://samlaycock.github.io/json-patch-to-crdt/ (Accessed: 2026-08-08)]

---

### 2.2 Delta Snapshots & Structural Sharing for Large Context Windows

Modern LLM agents frequently maintain conversation histories spanning 128k to 2M tokens (~500 KB to 8 MB of JSON text per step). Saving full state dumps at every reasoning step introduces prohibitive I/O latency and storage bloat.

#### Delta Persistence Strategy (RFC 6902 JSON Patch)
`chowki` employs a hybrid persistence mechanism:
1. **Base Snapshot:** A complete state dump generated at session initialization or periodic intervals.
2. **Delta Snapshots:** Subsequent execution steps persist only RFC 6902 JSON Patch operations (`add`, `remove`, `replace`, `move`, `copy`) representing the exact state diff relative to step $N-1$.

```json
[
  { "op": "add", "path": "/payload/messages/12", "value": {"role": "assistant", "content": "Tool result analyzed."} },
  { "op": "replace", "path": "/payload/step", "value": 15 }
]
```

#### Periodic Compaction & Threshold Baseline
To prevent unbounded patch chains (which degrade warm-resume reconstruction performance), `chowki` enforces a snapshot compaction rule:
* A new full **Base Snapshot** is forced every **50 steps** or when cumulative delta patch sizes exceed **20%** of the full base snapshot size.

| Snapshot Mode | Average Payload Size (100k Token Context) | Serialization Latency | Warm Resume Latency |
| :--- | :--- | :--- | :--- |
| **Full Snapshot Dump** | ~450 KB / step | ~3.8 ms / step | ~1.2 ms |
| **RFC 6902 Delta Patch**| **~1.5 KB / step** (99.6% reduction) | **~0.4 ms / step** | ~2.1 ms (Base + 10 deltas) |

* [Source: https://www.rfc-editor.org/rfc/rfc6902.html (Accessed: 2026-08-08)]
* [Source: https://pypi.org/project/jsonpatch/ (Accessed: 2026-08-08)]

---

### 2.3 Content Addressing & State Deduplication

To achieve zero-redundancy storage across concurrent agent runs, `chowki` uses SHA-256 content-addressable storage for large immutable state sub-trees (e.g., system prompts, vector document chunks, tool schemas).

#### Deterministic Canonical Hashing
Standard Python dictionary key ordering and whitespace variations can produce different hash digests for identical logical states. `chowki` enforces canonical JSON encoding prior to hashing:
1. Normalization of strings to Unicode NFC form.
2. Lexicographical sorting of JSON dictionary keys (RFC 8785 standard).
3. SHA-256 digest computation (`sha256:<hex>`).

> **Amendment (2026-08-11, normative — cross-language traps this section missed):**
> (a) RFC 8785 sorts keys by **UTF-16 code units** while a naive Python `sorted()`
> sorts by code points — these diverge for astral-plane keys; the shipped SDK detects
> astral keys and switches to UTF-16-BE ordering, and the Node SDK must match
> (`07-cross-sdk-parity.md` §1). (b) **Number formatting is unspecified here and is
> the #1 parity trap:** Python `repr(float)` differs from ECMAScript
> `Number::toString` for extreme magnitudes; full ES formatting on both SDKs is a
> Phase 3 work item (`TODO(phase-3)` in `chowki/state/canonical.py`). (c) Duplicate
> keys after NFC normalization are an error, not last-wins. (d) The canonical
> `content_hash` is the only cross-SDK identity hash; the snapshot envelope's
> `state_hash` is a writer-local integrity hash over its own MessagePack bytes and
> must never be compared across SDKs (`07-cross-sdk-parity.md` §2).

#### Deduplication Store Architecture
Large state attributes exceeding 4 KB are stripped from the main snapshot payload, replaced with a content-addressed reference string (`ref:sha256:<hash>`), and stored in a shared global blob store (`chowki_blob_store`):

```
+---------------------------------------------------+
|               MAIN SNAPSHOT PAYLOAD               |
|  "system_prompt": "ref:sha256:8f93...a1b2"        |
|  "tool_definitions": "ref:sha256:3c12...f901"     |
+---------------------------------------------------+
                          |
                          v
+---------------------------------------------------+
|            CONTENT-ADDRESSED BLOB STORE           |
|  sha256:8f93...a1b2 -> "You are a helpful..."     |
|  sha256:3c12...f901 -> [{"name": "execute_sql"}]  |
+---------------------------------------------------+
```

* [Source: https://pypi.org/project/knurl/ (Accessed: 2026-08-08)]
* [Source: https://www.rfc-editor.org/rfc/rfc8785.html (Accessed: 2026-08-08)]

> **Amendment (2026-08-11, normative — learned the hard way):** the blob store is not
> an in-process cache; it is part of the durability contract. **A blob must be durable
> in the storage adapter before any snapshot referencing it is dispatched**, because a
> persisted `ref:sha256:…` whose bytes died with the process makes every later warm
> resume fail with an integrity error. Phase 1 initially wired an in-memory-only blob
> store next to a durable-but-unused `blobs` table, and cross-process resume broke for
> any state holding a string over the 4 KB threshold. The write-through rule, escape
> prefix (`ref-lit:`), and extraction thresholds are pinned in
> `07-cross-sdk-parity.md` §8 — any future plan generation must carry an explicit
> task wiring blob durability end-to-end, not just a blob-store data structure.

---

## 3. Security & Safety Infrastructure

### 3.1 Encryption at Rest

To guarantee confidentiality and tamper resistance for persisted agent state snapshots across local storage, cloud buckets, and database backends, `chowki` mandates Authenticated Encryption with Associated Data (AEAD).

> **Amendment (2026-08-11, normative — reconciles this section with the shipped SDK):**
> "Mandates" describes the AEAD design when encryption is enabled, not a default.
> Phase 1 resolved the posture: **encryption at rest is opt-in and OFF by default**
> (`ChowkiConfig.encrypt_at_rest`); a library that silently requires key management is
> unusable out of the box. **Redaction is the non-negotiable half of ADR-003 and is
> always on.** Every SDK must implement the same default. Full parity details:
> `07-cross-sdk-parity.md` §3.

```
+--------------------------------------------------------------------------------------------------+
|                                    AUTHENTICATED ENCRYPTION FRAMEWORK                            |
+--------------------------------------------------------------------------------------------------+
| Plaintext Payload ---> [ AES-256-GCM / ChaCha20-Poly1305 ] <--- 96-bit Nonce (os.urandom(12))    |
|                                         |                                                        |
| Associated Data (AAD) ------------------+---> Ciphertext + 128-bit Authentication Tag             |
| (agent_id:tenant_id:version)                                                                     |
+--------------------------------------------------------------------------------------------------+
```

#### 1. AEAD Algorithm Selection: AES-256-GCM vs ChaCha20-Poly1305
`chowki` implements AEAD via the Python `cryptography.hazmat.primitives.ciphers.aead` module:

* **AES-256-GCM (Primary Choice):** Utilizes 256-bit symmetric keys with 96-bit nonces and 128-bit authentication tags. On modern server x86_64 and ARM64 CPUs with AES-NI hardware instructions, AES-GCM achieves hardware-accelerated throughput exceeding 3–5 GB/s with minimal CPU overhead.
* **ChaCha20-Poly1305 (Fallback Choice):** Specified in RFC 7539, providing strong stream-cipher security on environments without dedicated AES hardware acceleration (e.g., small edge devices or legacy containers).

#### 2. Nonce Generation & Reuse Prevention
Nonce reuse in AES-GCM completely destroys confidentiality and authentication guarantees. `chowki` enforces 96-bit nonces generated via cryptographically secure random bytes (`os.urandom(12)`) per encryption operation. Nonces are prefixed directly to the output ciphertext stream.

#### 3. Cryptographic Session Binding via Associated Authenticated Data (AAD)
To prevent cross-session ciphertext transplantation attacks (where an attacker swaps encrypted snapshot blobs between different agent instances or tenants), `chowki` binds non-encrypted header metadata into the AEAD authentication calculation:

```python
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import os

key = AESGCM.generate_key(bit_length=256)
aesgcm = AESGCM(key)

nonce = os.urandom(12)
plaintext_snapshot = b'{"payload": "sensitive agent memory"}'

# AAD binds tenant_id, agent_id, and schema version
aad_metadata = b"tenant_492:agent_ag_9f823a1b:v1"

ciphertext = aesgcm.encrypt(nonce, plaintext_snapshot, aad_metadata)
# Output structure: [12-byte nonce] + [ciphertext] + [16-byte authentication tag]
```

During decryption, any mismatch in `aad_metadata` causes `aesgcm.decrypt()` to raise `InvalidTag`, preventing unauthorized cross-tenant state restoration.

#### 4. Zero-Downtime Key Rotation Architecture
`chowki` provides a versioned `KeyRing` for seamless, zero-downtime encryption key rotation. The active key index is stored in unencrypted snapshot headers. When decrypting, `chowki` retrieves the matching historical key from the `KeyRing`. During background state compaction, older snapshots are lazily re-encrypted with the active primary key.

* [Source: https://cryptography.io/en/latest/hazmat/primitives/aead/ (Accessed: 2026-08-08)]
* [Source: https://pythonhowtoprogram.com/how-to-use-python-cryptography-for-encryption-and-decryption/ (Accessed: 2026-08-08)]

---

### 3.2 Automated Secret Redaction Pipeline

LLM agents dynamically ingest user prompts, execute API tool calls, and receive responses containing API keys, bearer tokens, or connection strings. Persisting raw state without scrubbing leads to credential leakage in storage logs and snapshots.

`chowki` integrates a mandatory, high-throughput, two-tier secret redaction engine directly into the state capture pipeline before serialization.

```
+--------------------------------------------------------------------------------------------------+
|                                  TWO-TIER SECRET REDACTION ENGINE                                |
+--------------------------------------------------------------------------------------------------+
|  Raw Agent State Dict                                                                            |
|         |                                                                                        |
|         v                                                                                        |
|  [ LAYER 1: High-Precision Compiled Regex ] ---> Matches OpenAI (sk-*), AWS (AKIA*), JWTs, etc. |
|         |                                                                                        |
|         v (unmatched candidates)                                                                 |
|  [ LAYER 2: Shannon Entropy Analysis ] ----> Flags H(X) >= 4.5 bits/char (len >= 12)            |
|         |                                    Filters out UUIDs, hex hashes, paths                |
|         v                                                                                        |
|  Sanitized State (Secrets replaced with [REDACTED:<type_hash>])                                  |
+--------------------------------------------------------------------------------------------------+
```

#### Layer 1: High-Precision Compiled Regex Patterns
Pre-compiled regular expressions match well-known credential formats across keys, headers, and values:

* **OpenAI / Anthropic Keys:** `sk-[A-Za-z0-9\-_]{20,}`, `sk-proj-[A-Za-z0-9\-_]{40,}`, `sk-ant-[A-Za-z0-9\-_]{40,}`
* **AWS Credentials:** `(?:AKIA|ASIA)[0-9A-Z]{16}`, `aws_secret_access_key\s*[:=]\s*[A-Za-z0-9/+=]{20,}`
* **GitHub / Slack / Stripe Tokens:** `ghp_[A-Za-z0-9]{36}`, `xox[baprs]-[A-Za-z0-9\-_]{10,}`, `(?:sk|pk)_(?:live|test)_[A-Za-z0-9]{20,}`
* **Bearer & Authorization Headers:** `Bearer\s+[A-Za-z0-9\-._~+/]{10,}=*`, `Basic\s+[A-Za-z0-9+/]{10,}={0,2}`
* **JWT Tokens:** `\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*`
* **Private Key Blocks:** `-----BEGIN[A-Z \-]*PRIVATE KEY-----[\s\S]*?-----END[A-Z \-]*PRIVATE KEY-----`
* **Database Connection URIs:** `(?<=://)[^\s'\"]*:[^\s'\"@]+(?=@)` (redacts `user:password` in URIs)

#### Layer 2: Shannon Entropy Analysis
To detect custom API keys or unknown random tokens that do not match standard regex signatures, `chowki` evaluates the Shannon entropy $H(X)$ of string tokens $\ge 12$ characters:

$$H(X) = -\sum_{i=1}^{n} P(x_i) \log_2 P(x_i)$$

* **Threshold:** Tokens with $H(X) \ge 4.5 \text{ bits/character}$ and length $\ge 12$ characters are flagged as potential secrets.
* **Safe-Pattern Filters (False Positive Reduction):** To avoid falsely redacting legitimate data, `chowki` skips:
  1. Standard UUIDs (`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-...`)
  2. Pure hex SHA-1/256 digests (`len in {40, 64}`)
  3. File system paths and URL resource paths.

#### Redaction & Masking Semantics
When a secret is detected, `chowki` replaces the sensitive value with a deterministic HMAC-blinded placeholder: `[REDACTED:<type>:<short_hash>]`. This ensures that state state diffs remain debuggable without exposing raw credentials.

> **Amendment (2026-08-11, normative):** the placeholder derivation this paragraph
> left unspecified is now pinned in `07-cross-sdk-parity.md` §7 — `short_hash` is the
> first 8 hex chars of HMAC-SHA256 under a **storage-persisted per-deployment key**
> (secret slot `"redaction"`), `<type>` is the pattern name lowercased with
> non-`[a-z0-9_]` folded to `_`, and redaction is a fixpoint over its own
> placeholders. Two SDKs sharing storage must produce identical placeholders.
> Also normative there: entropy is computed over **code points** (not UTF-16 units),
> the effective minimum entropy-candidate length is `max(12, ceil(2^4.5)) = 23`, and
> the **binary boundary** — UTF-8-decodable bytes-like values are text-redacted and
> re-encoded, non-UTF-8 binary passes through by documented exemption, set/frozenset
> members are redacted with container type preserved.

* [Source: https://github.com/BerriAI/litellm/blob/main/litellm/litellm_core_utils/secret_redaction.py (Accessed: 2026-08-08)]
* [Source: https://mend.github.io/mend-guardrails-python/ref/modules/secret_keys/ (Accessed: 2026-08-08)]
* [Source: https://vrk.sh/docs/mask/ (Accessed: 2026-08-08)]
* [Source: https://github.com/secretgate/secretgate/blob/main/docs/supported-patterns.md (Accessed: 2026-08-08)]

---

## 4. Recommendation & Performance Budget

### 4.1 Recommended Serialization Stack for `chowki` Python SDK

Based on the empirical performance benchmarks, security vulnerabilities of `pickle`, and strict memory constraints, `chowki` adopts the following architecture for its Python SDK:

```
+--------------------------------------------------------------------------------------------------+
|                                  RECOMMENDED CHOWKI SERIALIZATION STACK                          |
+--------------------------------------------------------------------------------------------------+
| 1. STATE MODEL         : msgspec.Struct (C-based memory layout with __slots__)                   |
| 2. REDACTION ENGINE    : Native Compiled Regex + Shannon Entropy (H >= 4.5) Filter                 |
| 3. PRIMARY ENCODER     : msgspec.msgpack (Binary MessagePack) / msgspec.json (Text fallback)     |
| 4. DELTA PERSISTENCE   : RFC 6902 JSON Patch (Diffs relative to Base Snapshot)                   |
| 5. CONTENT DEDUP       : SHA-256 Content-Addressed Blob Store (RFC 8785 Canonical JSON)          |
| 6. ENCRYPTION AT REST  : AES-256-GCM via cryptography.hazmat (96-bit nonce + AAD session binding)|
| 7. BOUNDARY ADAPTER    : Pydantic v2 TypeAdapter bridge for external framework interoperability  |
+--------------------------------------------------------------------------------------------------+
```

#### Key Architectural Decisions
1. **`msgspec.Struct` as Core State Model:** All internal `chowki` snapshot headers, message frames, and execution metadata use `msgspec.Struct`. This delivers a **10x - 15x speedup** over `pydantic.BaseModel` during serialization and reduces RAM overhead by ~2.5x.
2. **Binary MessagePack as Default Wire Format:** `chowki` uses `msgspec.msgpack` for persistent storage and inter-process transport. MessagePack reduces payload size by **30% - 50%** compared to JSON and eliminates string parsing overhead.
3. **Pydantic v2 Boundary Adapter:** For external developers passing existing Pydantic models into `chowki` agent state, `chowki` provides a zero-copy converter that wraps models into `msgspec` structs at the SDK boundary.
4. **Strict Rejection of `pickle` / `cloudpickle`:** `chowki` entirely omits `pickle` and `cloudpickle` from its runtime execution path, completely eliminating remote code execution vulnerabilities.

---

### 4.2 Explicit Performance Budget & Benchmark Targets

To maintain zero noticeable impact on LLM agent loop execution times, `chowki` establishes an explicit per-step snapshot performance budget target.

#### Per-Step Snapshot Latency Budget Target (1 MB Agent State Payload)

$$\text{Total Per-Step Overhead Target} \le \mathbf{2.0 \text{ ms}}$$

```
+--------------------------------------------------------------------------------------------------+
|                                PER-STEP SNAPSHOT LATENCY BUDGET                                  |
+-----------------------------------+--------------------+-----------------------------------------+
| Pipeline Component                | Budget Target (ms) | Tech / Implementation                   |
+-----------------------------------+--------------------+-----------------------------------------+
| 1. Secret Redaction & Scanning    | < 0.8 ms           | C-compiled regex + fast Shannon entropy |
| 2. Struct Encoding (MessagePack)  | < 0.3 ms           | msgspec C struct encoder                |
| 3. Canonical Hashing (SHA-256)    | < 0.3 ms           | hashlib / msgspec C digest              |
| 4. AES-256-GCM Encryption         | < 0.4 ms           | cryptography.hazmat C/OpenSSL (AES-NI)  |
| 5. Storage Dispatch              | < 0.2 ms           | Synchronous write-through storage dispatch |
+-----------------------------------+--------------------+-----------------------------------------+
| TOTAL STEP OVERHEAD BUDGET        | < 2.0 ms           | Synchronous in-process persistence      |
+-----------------------------------+--------------------+-----------------------------------------+
```

> **Amendment (2026-08-11, normative):** Synchronous write-through storage dispatch is ratified and enforced — state snapshots are flushed and committed synchronously to storage before a step returns. SIGKILL at any point after a step returns loses zero acknowledged step state.

#### Storage Size Overhead Budget Target

| Metric | Target Goal | Strategy |
| :--- | :--- | :--- |
| **Snapshot Size Reduction** | **> 75% size reduction** | MessagePack binary compression + RFC 6902 delta patches |
| **Blob Deduplication Ratio** | **> 90% redundant content saved** | SHA-256 content addressing for system prompts & schemas |
| **Max Delta Chain Depth** | **50 steps** | Automatic compaction trigger forces new Base Snapshot |

* [Source: https://msgspec.dev/benchmarks (Accessed: 2026-08-08)]
* [Source: https://cryptography.io/en/latest/hazmat/primitives/aead/ (Accessed: 2026-08-08)]
* [Source: https://www.danilchenko.dev/posts/msgspec-vs-pydantic/ (Accessed: 2026-08-08)]
