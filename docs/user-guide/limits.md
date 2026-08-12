# Limits & Operational Boundaries

To maintain high performance and simplicity without external orchestration servers, `chowki` enforces explicit architectural boundaries and trade-offs.

---

## 1. Single-Writer Per Run Boundary (No `asyncio.gather` Over Steps)

Within a single workflow run, step execution and state persistence are strictly sequential:
- **Concurrent Steps in One Run Are Refused:** `chowki` does not let two `@chowki.step` functions of one run execute at the same time. A second step entering the run from a different task or thread while another is still running raises `ChowkiConcurrencyError` immediately — before any step ordinal is allocated or any record is written — instead of silently corrupting the run.
- **What Is Detected:** Any concurrent entry that carries the run's context, which is what `asyncio.gather()` and `asyncio.to_thread()` do (the first copies the context per task, the second per thread). A step launched through a bare `threading.Thread` or a `ThreadPoolExecutor.submit()` call does *not* inherit the run `ContextVar`: it runs outside the run entirely, so it is neither refused nor recorded — the decorated function executes as a plain function call, with no memoisation, no idempotency claim, and no snapshot. Copy the context explicitly (`contextvars.copy_context().run(...)`) if you want such a call inside the run, and it will be refused like any other fan-out.
- **The Error Is Not Retried:** `ChowkiConcurrencyError` is permanent, not transient, so the guardrail breaker never retries it and never converts it into an auto-pause. It propagates out of the enclosing step and out of the workflow unchanged, and the run is recorded `FAILED`.
- **Why:** State snapshots rely on strict step ordering and linear delta patching (RFC 6902). Concurrent steps within a single run would interleave step ordinals and diff each snapshot against another step's document, so the damage would only surface on the next resume.
- **Nesting Is Not Concurrency:** A step that calls another step (`pay_invoice` → `_transfer`) runs on the same task and thread and is unaffected.
- **Concurrent Runs Allowed:** You can execute as many independent workflow runs in parallel as your host system can handle.
- **Known False Positive:** A step invoked through `asyncio.to_thread()` *from inside another step* is sequential, but it is refused too — `chowki` cannot tell it apart from a real fan-out. Do the offload outside the step, or leave the inner function undecorated.
- **Parallel Steps Are Phase 6:** Real fan-out within a run needs deterministic branch keys; until then, run the steps sequentially or run independent runs concurrently.

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
- **Expansion stops at 100 levels of nesting.** An object found deeper than that — a link far down a linked list, an ORM row reached through a long parent chain — collapses to the same `<TypeName>` marker and logs the same warning, instead of expanding until the interpreter stack runs out. The cut is made on the argument's own depth, so the hash of a given argument is the same wherever the step is called from.
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

1. **Running `asyncio.gather()` Over Steps:** Wrapping `@chowki.step` functions in `asyncio.gather()` or `asyncio.to_thread()` raises `ChowkiConcurrencyError` at the second step's entry, unretried — the run fails loudly rather than writing a snapshot chain no resume can rebuild. Run steps sequentially within a workflow, or run independent workflows concurrently.
2. **Calling a Step From a Bare Thread:** A step submitted to a `ThreadPoolExecutor` or started on a `threading.Thread` without copying the context runs outside the run: no refusal, but also no memoisation, no idempotency claim, and no snapshot. Keep steps on the run's own task, or copy the context and run them sequentially.
3. **Passing Structureless Step Arguments:** An argument chowki cannot expand (a C extension object, an open socket) collapses to `<TypeName>` and logs `chowki_step_args_opaque`; two such instances then share an argument hash and can collide in the step cache. Pass a Struct, dataclass, model, or dict instead.
4. **Assuming Redaction Obfuscates Non-UTF-8 Binary Blobs:** Passing API keys inside raw non-UTF-8 binary bytes objects (that fail UTF-8 decoding) bypasses regex redaction scanning.
