# Limits & Operational Boundaries

To maintain high performance and simplicity without external orchestration servers, `chowki` enforces explicit architectural boundaries and trade-offs.

---

## 1. Single-Writer Per Run Boundary (No `asyncio.gather` Over Steps)

Within a single workflow run, step execution and state persistence are strictly sequential:
- **No Concurrent Steps in One Run:** You must not run multiple `@chowki.step` functions concurrently using `asyncio.gather()` or thread pools within the same workflow run.
- **Why:** State snapshots rely on strict step ordering and linear delta patching (RFC 6902). Concurrent steps within a single run would introduce non-deterministic step ordinals and state race conditions.
- **Concurrent Runs Allowed:** You can execute as many independent workflow runs in parallel as your host system can handle.

---

## 2. SQLite Boundary & Scale-Out Path

By default, `chowki` uses SQLite with Write-Ahead Logging (WAL) enabled:
- **Local Write Capacity:** Excellent for single-instance applications, worker processes, and microservices performing hundreds of state writes per second.
- **Multi-Process Concurrency:** SQLite handles multiple readers and a single concurrent writer across processes cleanly.
- **Phase 5 Scale-Out:** For distributed cluster deployments requiring multi-region persistence or thousand-node horizontal scaling, `chowki` Phase 5 introduces the PostgreSQL storage adapter (`PostgreSQLStorage`).

---

## 3. Secret Redaction Non-UTF-8 Exemption

`chowki`'s automatic secret redactor inspects string values and text fields in state payloads for known secret patterns (API keys, tokens, passwords):
- **Text & UTF-8 Bytes Redaction:** Redaction scans UTF-8 string values as well as `bytes` and `bytearray` values that decode cleanly as UTF-8.
- **Non-UTF-8 Binary Payloads Exempt:** Non-UTF-8 binary payloads (bytes sequences that fail UTF-8 decoding, such as compressed archives or raw binary blobs) are exempt from regex redaction scanning to avoid corrupting binary data structure.

---

## 4. Argument Hashing of Complex Objects

When computing step idempotency keys, `chowki` hashes step input arguments using a deterministic sanitizer:
- Standard primitive arguments (`None`, `bool`, `int`, `float`, `str`), dictionaries, sets, and sequences (`list`, `tuple`) are sanitized and hashed by value.
- Complex objects are expanded structurally and hashed by value too: `msgspec.Struct` instances and dataclasses field by field; attrs classes, enums, `bytes`, `datetime`, `UUID` and `Decimal` values via `msgspec.to_builtins`; Pydantic models via their `model_dump()`; ordinary class instances via their `__dict__`. Each expansion is hashed under a `{"<TypeName>": ...}` wrapper, so a `Struct` never hashes identically to a plain dict with the same fields. A `set` or `frozenset` held in a field stays a set through the expansion and is sorted into a total order, so the hash of a Struct or dataclass argument is the same in the process that resumes the run as it was in the process that started it.
- **Only a value with no exposable structure** (a C extension object, an open socket, a bare `object()`) collapses to a `<TypeName>` marker. When that happens the step logs `chowki_step_args_opaque` with the `run_id`, the `step_id`, and the offending type names.
- **Impact:** Two different instances of a collapsed type share an argument hash, so a memoised result can replay for logically different arguments. The warning is the signal that a step is exposed to that.
- **Best Practice:** Pass something with structure — a Struct, dataclass, model, or dict — instead of an opaque object. Note that an object holding per-process values (a connection id, a socket handle) hashes differently in a recovering process, which costs a cache miss on resume rather than a wrong answer.

---

## 5. What Redaction Does and Does Not Guarantee

Redaction in `chowki` provides **defense in depth**, not an absolute security guarantee:

### What Redaction Guarantees
- Detects and replaces secrets using a multi-tiered strategy: sensitive key name matching (`api_key`, `secret`, `token`, `password`), pattern regex matching for known provider keys (OpenAI, Anthropic, GitHub, AWS, Slack, RSA/SSH keys), and high Shannon entropy text scanning.
- Prevents plaintext API keys from leaking into SQLite database files or state snapshot deltas.

### What Redaction Does NOT Guarantee
- **Obfuscated or Fragmented Secrets:** Does not detect secrets that have been split across multiple variables or encoded (e.g., base64, URL-encoded).
- **Binary Secrets in Bytes:** Non-UTF-8 binary blobs are not scanned.
- **Third-Party External Logging:** Redaction applies to `chowki` state storage. Print statements or logger calls outside `chowki` are not redacted.

Always treat secret redaction as an additional layer of security rather than a replacement for proper secrets management and least-privilege IAM roles.

---

## 6. Synchronous Persistence & Durability Contract

- **Synchronous Persistence:** State snapshots are committed synchronously before `@chowki.step` returns. Process death, SIGKILL, or an unhandled exception after a step returns loses zero committed step state.
- **Zero Loss on Acknowledged Steps:** Once `@chowki.step` finishes execution, its state snapshot and idempotency record are already committed to storage, so a later crash resumes from them. A step whose return value the MessagePack codec cannot encode is the documented exception — it is recorded `COMPLETED` but not replayable, so its body runs again on warm resume (see [concepts.md](concepts.md)).
- **Durability Scope:** The default SQLite storage runs WAL mode with `PRAGMA synchronous=NORMAL`, which guarantees durability against process kills. Surviving an OS crash or sudden power loss additionally depends on the operating system flushing its own disk cache, so the last commits before a power cut are not guaranteed by the default adapter.

---

## What Can Go Wrong

1. **Running `asyncio.gather()` Over Steps:** Wrapping `@chowki.step` functions in `asyncio.gather()` causes state delta corruption or race conditions. Run steps sequentially within a workflow, or run independent workflows concurrently.
2. **Passing Structureless Step Arguments:** An argument chowki cannot expand (a C extension object, an open socket) collapses to `<TypeName>` and logs `chowki_step_args_opaque`; two such instances then share an argument hash and can collide in the step cache. Pass a Struct, dataclass, model, or dict instead.
3. **Assuming Redaction Obfuscates Non-UTF-8 Binary Blobs:** Passing API keys inside raw non-UTF-8 binary bytes objects (that fail UTF-8 decoding) bypasses regex redaction scanning.
