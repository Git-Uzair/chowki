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

## 4. `<TypeName>` Argument Hash Collapse

When computing step idempotency keys, `chowki` hashes step input arguments using a deterministic sanitizer:
- Standard primitive arguments (`None`, `bool`, `int`, `float`, `str`), dictionaries, sets, and sequences (`list`, `tuple`) are sanitized and hashed by value.
- Complex objects (like `msgspec.Struct` instances, Pydantic models, or custom class instances) are not expanded by the sanitizer and collapse to `<TypeName>` (e.g., `<MyStruct>`, `<UserModel>`).
- **Impact:** Passing two different instances of a `msgspec.Struct` or Pydantic model with the same class name as step arguments will collapse to the same `<TypeName>` string in the argument hash.
- **Best Practice:** To ensure distinct step argument hashes when using complex objects, pass primitive values, dicts, or convert models to dictionaries (e.g., `msgspec.structs.asdict(obj)` or `model.model_dump()`) before passing them to `@chowki.step`.

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

- **Synchronous Persistence:** State snapshots are committed to storage before a step returns. SIGKILL after step return loses no state.
- **Zero Loss on Acknowledged Steps:** Once `@chowki.step` finishes execution, its state snapshot and idempotency record are guaranteed durable in persistent storage.

---

## What Can Go Wrong

1. **Running `asyncio.gather()` Over Steps:** Wrapping `@chowki.step` functions in `asyncio.gather()` causes state delta corruption or race conditions. Run steps sequentially within a workflow, or run independent workflows concurrently.
2. **Passing Un-serializable Step Arguments:** Depending on `<TypeName>` argument hash collapse for custom object instances can cause step cache collisions if argument values differ but their class names match.
3. **Assuming Redaction Obfuscates Non-UTF-8 Binary Blobs:** Passing API keys inside raw non-UTF-8 binary bytes objects (that fail UTF-8 decoding) bypasses regex redaction scanning.
