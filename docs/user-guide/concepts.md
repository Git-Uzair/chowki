# Core Concepts

`chowki` provides durable execution and state preservation for Python workflows without requiring external orchestration servers or background daemons.

---

## Mental Model Diagram

```text
  +-----------------------------------------------------------------------+
  |                        Workflow Function Body                         |
  |  [Start Run] --> @step 1 (memoised) --> @step 2 (executes) --> [Done] |
  +-----------------------|----------------------|------------------------+
                          |                      |
                          v                      v
                +-------------------+  +--------------------+
                |  Base Snapshot    |  |   Delta Snapshot   |
                | (Full State v1)   |  |  (RFC 6902 Patch)  |
                +-------------------+  +--------------------+
                          |                      |
                          +----------+-----------+
                                     |
                                     v
                        +-------------------------+
                        |  SQLite Storage Engine  |
                        +-------------------------+
```

---

## 1. Runs

A **Run** represents a single invocation of a `@chowki.workflow` function. Each run is assigned a unique UUID (`run_id`) and tracked through explicit lifecycle statuses:
- `PENDING`: Initialized but not yet executing.
- `RUNNING`: Actively executing steps.
- `PAUSED`: Suspended at a human-in-the-loop gate or auto-pause guardrail.
- `COMPLETED`: Finished execution and returned a final result.
- `FAILED`: Terminated with an unhandled exception.
- `ABORTED`: Canceled via administrative intervention.
- `REJECTED`: Declined by a human reviewer.

---

## 2. Steps

A **Step** is a discrete, atomic block of work decorated with `@chowki.step`. Steps are the unit of memoisation and durability in `chowki`:
- When a step executes successfully, its return value is serialized and persisted to storage alongside its idempotency key.
- On warm resume or crash replay, completed steps are recognized by their step identity and skipped, returning the previously recorded result instantly.

```python
import chowki


@chowki.step
def fetch_user_profile(user_id: str) -> dict[str, str]:
    # Atomically memoised step
    return {"user_id": user_id, "role": "admin"}
```

---

## 3. State & Snapshots

State in `chowki` includes step execution history, variable context, token/cost budgets, loop detection windows, and pending human pause requests.

`chowki` compacts and persists state using two snapshot kinds:
- **Base Snapshots:** Full state serializations produced at the beginning of a run or after compaction thresholds.
- **Delta Snapshots:** Compact RFC 6902 JSON Patch state differences recorded at step boundaries to keep disk I/O minimal.

All state payloads are serialized using C-accelerated `msgspec` Structs in MessagePack format.

---

## 4. The Engine (`ChowkiEngine`)

The **Engine** assembles storage adapters, keyrings, redactor filters, and snapshot pipelines:
- Manages connections to SQLite or custom storage adapters.
- Handles HMAC token generation and validation.
- Coordinates automatic secret redaction before writing state to disk.
- Exposes `chowki.inspect_run()` for real-time run introspection.

---

## What Can Go Wrong

1. **Un-stepped Side Effects:**
   Side effects executed directly in the workflow body outside a `@chowki.step` will re-execute on every warm resume replay.
2. **Non-Serializable Step Results:**
   Step return values must be serializable (primitives, dicts, lists, msgspec Structs, Pydantic models). Un-serializable objects like raw open sockets or database connections cannot be persisted.
3. **In-Place State Mutation Without Steps:**
   Mutating global or outer-scope mutable variables outside step boundaries will not be captured in snapshot deltas. Pass data explicitly through step arguments and return values.
