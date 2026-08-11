# Configuration & Security

`chowki` is configured globally using `chowki.configure()` or by instantiating `ChowkiConfig`.

---

## `ChowkiConfig` Field Reference

| Field Name | Type | Default | Description |
|---|---|---|---|
| `db_path` | `Path` | `./.chowki/chowki.db` | Local SQLite database file path relative to CWD. Created automatically if missing. |
| `tenant_id` | `str` | `"default"` | Tenant identifier recorded on every `RunRecord` and bound into AES-GCM state encryption as Associated Authenticated Data (AAD). |
| `resume_secret` | `bytes \| str \| None` | `None` | HMAC secret key used to sign and verify pause tokens. Configured via `ChowkiConfig(resume_secret=...)` in Python or `CHOWKI_RESUME_SECRET` in CLI. |
| `encrypt_at_rest` | `bool` | `False` | Master switch for AES-256-GCM envelope state encryption at rest. Requires `CHOWKI_MASTER_KEY` environment variable or `keyring`. |
| `keyring` | `KeyRing \| None` | `None` | Custom cryptographic key management ring. |
| `redaction_hmac_key` | `bytes \| None` | `None` | HMAC key for deterministic secret placeholder hashing. If omitted, initialized automatically from persistent storage. |
| `guardrails` | `GuardrailConfig` | `GuardrailConfig()` | Guardrail and loop detection configuration thresholds. |
| `storage` | `StorageAdapter \| None` | `None` | Custom storage implementation (defaults to `SQLiteStorage`). |
| `gateway` | `ChannelGateway \| None` | `None` | Optional channel gateway adapter for multi-channel HITL notifications. |
| `blob_threshold_bytes` | `int` | `4096` | Threshold size in bytes above which state sub-objects are extracted and stored separately as binary blobs. |
| `tracing_enabled` | `bool` | `False` | Enable detailed execution tracing. |

---

## Programmatic Configuration Example

```python
from pathlib import Path
import chowki

chowki.configure(
    db_path=Path("/var/lib/chowki/app.db"),
    tenant_id="org_acme_corp",
    resume_secret="super-secret-hmac-key-min-32-chars",
    encrypt_at_rest=True,
)
```

---

## `resume_secret` in Production

In production environments (especially multi-process or containerized deployments):
- If `resume_secret` is omitted, `chowki` generates an ephemeral 32-byte secret per process and emits a `UserWarning`.
- **Problem:** Resuming a run after a service restart or on a different worker process will fail with `InvalidResumeToken` because the ephemeral key changed.
- **Solution:** Always set `CHOWKI_RESUME_SECRET` environment variable or pass `resume_secret` to `chowki.configure()`.

---

## Encryption at Rest (`CHOWKI_MASTER_KEY`)

When sensitive data (API keys, user PII, customer payloads) flows through agent workflows, enable encryption at rest:

1. Generate a 32-byte base64-encoded master key:
   ```bash
   python -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"
   ```
2. Set the key in your environment:
   ```bash
   export CHOWKI_MASTER_KEY="your-base64-encoded-key-here"
   ```
3. Enable encryption in `chowki`:

```python
import chowki

chowki.configure(encrypt_at_rest=True)
```

State envelopes are encrypted with AES-256-GCM. The AEAD Associated Authenticated Data (AAD) binds the `tenant_id`, `run_id`, and schema version, preventing ciphertext manipulation or cross-tenant key swapping attacks.

---

## Tenant Isolation (`tenant_id`)

In multi-tenant applications, pass `tenant_id` to `chowki.configure()` or specify it per engine:
- **Run Metadata:** `tenant_id` is recorded on every `RunRecord` written to the database (`RunRecord.tenant_id`).
- **Cryptographic Binding:** When encryption at rest is enabled, the snapshot AAD is `f"{tenant_id}:{run_id}:v{schema_version}"`, so a snapshot written under one tenant cannot be decrypted as another tenant's — a wrong tenant ID fails authentication with `DecryptionError`.
- **No Query Filtering:** `chowki` does **not** filter reads by tenant. `list_runs()` (and therefore `resumable_runs()`, `recover_runs()` and the CLI's `runs list`) returns every run in the database regardless of `tenant_id`; the only filter it accepts is `status`. Give each tenant its own `db_path`, or filter on `RunRecord.tenant_id` in your own code, if a process must not see another tenant's runs.

---

## What Can Go Wrong

1. **Unconfigured `resume_secret` Across Restarts:** Omitting `resume_secret` causes pause tokens issued before a deploy or restart to fail verification afterwards (`InvalidResumeToken`).
2. **Missing `CHOWKI_MASTER_KEY`:** Setting `encrypt_at_rest=True` without setting `CHOWKI_MASTER_KEY` or providing a `keyring` raises `ChowkiConfigError`.
3. **Database Lock Contention:** Placing `db_path` on a network file system (NFS/SMB) rather than local SSD can cause SQLite POSIX locking failures.
