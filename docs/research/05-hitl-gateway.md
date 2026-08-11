# Human-in-the-Loop (HITL) Gateway & Interactive Channel Flows Architecture (2026)

**Project:** `chowki`  
**Date:** 2026-08-08  
**Scope:** Interactive channel integrations (Slack Block Kit, MS Teams Adaptive Cards 1.5/1.6, REST Gateway), security verification, token handling, approval UX patterns, RFC 6902 state patching, and provenance tracking.

---

## Executive Summary

The `chowki` Human-in-the-Loop (HITL) Gateway acts as the bidirectional bridge between suspended AI agent workflows and human reviewers across chat surfaces (Slack, Microsoft Teams) and web interfaces. When a `chowki`-managed workflow hits a human intervention boundary (e.g. `@chowki.pause()`), the state engine serializes execution context, emits interactive UI elements to the target channel, and halts execution without holding compute resources. Upon human interaction (approval, rejection, inline edit, or escalation), the gateway validates request authenticity, translates human inputs into RFC 6902 JSON Patch deltas, applies state overrides, and triggers the `chowki` warm-resume engine.

---

## 1. Channel Integrations & Interactive Formats (2026 API State)

### 1.1 Slack Block Kit Interactive Workflows

Slack Block Kit provides a structured JSON UI framework for rendering interactive components inside Slack channels, Direct Messages, and modal dialogs.

```
+-----------------------------------------------------------------------+
|  chowki Agent Suspension Alert                                         |
|  Task ID: task_8f91a2 | Step: execute_sql_query                        |
|                                                                       |
|  Proposed Query:                                                      |
|  `UPDATE vendor_payouts SET status = 'APPROVED' WHERE id = 1042;`    |
|                                                                       |
|  [ Approve ]   [ Reject ]   [ Edit & Resume ]   [ Escalate ]          |
+-----------------------------------------------------------------------+
                                   |
                                   v (User clicks "Edit & Resume")
+-----------------------------------------------------------------------+
| Modal: Edit SQL Query Arguments                                       |
| +-------------------------------------------------------------------+ |
| | UPDATE vendor_payouts SET status = 'APPROVED' WHERE id = 1042;   | |
| +-------------------------------------------------------------------+ |
| [ Cancel ]                                             [ Submit ]     |
+-----------------------------------------------------------------------+
```

#### Core Components & Identifiers
* **`block_id` & `action_id`:** Every interactive element (button, select menu, plain-text input) requires a `block_id` (identifying the UI layout block) and an `action_id` (identifying the specific action). When clicked, Slack dispatches an HTTP POST request to the registered Request URL containing both identifiers.
* **Button Payloads (`value`):** Buttons carry a `value` string (max 2000 characters). `chowki` embeds compact, cryptographically signed tokens containing `task_id`, `step_id`, `permitted_action`, and an anti-replay nonce directly inside `value`.
* **Modal Dialogs & `trigger_id`:** Complex review flows (such as "Edit & Resume") trigger modal views via `client.views.open`. Modals require a `trigger_id` extracted from the initial button interaction payload. `trigger_id` values expire in **3 seconds** and are strictly single-use.
* **Payload Types:**
  * `block_actions`: Dispatched immediately when a user interacts with a button or input control.
  * `view_submission`: Dispatched when a user submits a modal form.
  * `view_closed`: Dispatched when a modal is dismissed.

#### Response Mechanisms: `response_url` vs `chat.update`
`chowki` evaluates two distinct mechanisms for updating interactive Slack messages post-decision:

| Capability / Property | `response_url` Webhook | `chat.update` Web API |
| :--- | :--- | :--- |
| **Invocation Model** | HTTP POST to ephemeral, unique URL provided in interaction payload | Web API call (`chat.update`) using bot OAuth token (`xoxb-...`) |
| **Addressing Requirements** | Requires only the payload's `response_url` | Requires explicit `channel` ID and message timestamp (`ts`) |
| **Lifespan & Usage** | Valid for up to **5 invocations** within **30 minutes** of payload receipt | Permanent capability (usable anytime as long as message exists) |
| **Scope / Permissions** | Bypasses standard channel posting scopes (acts as return pathway) | Requires `chat:write` OAuth scope |
| **In-place Modification** | Requires `replace_original: true` in POST body | Modifies message content via `channel` and `ts` parameters |
| **Deletion Support** | Can delete original message via `delete_original: true` | Requires separate `chat.delete` call with `chat:write` scope |
| **Best Practice Usage in `chowki`** | Primary path for immediate response (<30 min) to handle button clicks | Fallback path for delayed updates (>30 min) or asynchronous state Sync |

* [Source: https://docs.slack.dev/interactivity/handling-user-interaction/ (Accessed: 2026-08-08)]
* [Source: https://docs.slack.dev/reference/interaction-payloads/ (Accessed: 2026-08-08)]

---

### 1.2 Microsoft Teams Adaptive Cards (1.5 / 1.6) & Universal Action Model

Microsoft Teams renders interactive UI via Adaptive Cards JSON schemas. Supported versions (1.5 and 1.6) enforce the **Universal Action Model**, standardizing backend action processing across Teams, Outlook, and Microsoft 365 Copilot surfaces.

```json
{
  "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
  "type": "AdaptiveCard",
  "version": "1.5",
  "body": [
    {
      "type": "TextBlock",
      "text": "chowki Action Required: High-Risk Execution",
      "weight": "Bolder",
      "size": "Medium"
    },
    {
      "type": "TextBlock",
      "text": "Agent 'deployment-bot' requested database migration on Production."
    }
  ],
  "actions": [
    {
      "type": "Action.Execute",
      "title": "Approve",
      "verb": "chowki_approve",
      "data": {
        "task_id": "task_99120",
        "action_token": "eyJhbGciOiJIUzI1Ni..."
      }
    },
    {
      "type": "Action.Execute",
      "title": "Reject",
      "verb": "chowki_reject",
      "data": {
        "task_id": "task_99120",
        "action_token": "eyJhbGciOiJIUzI1Ni..."
      }
    }
  ]
}
```

#### Universal Actions: `Action.Execute` vs Legacy `Action.Submit`
* **`Action.Execute`:** Introduced in Adaptive Cards 1.4 and baseline in 1.5/1.6. Replaces legacy `Action.Submit` (Teams) and `Action.Http` (Outlook). When invoked, it dispatches an `adaptiveCard/action` Invoke activity to the target Azure Bot Service backend.
* **`verb` and `data` Payload:** `verb` defines the string action key (e.g. `chowki_approve`), while `data` passes structured JSON metadata including signed security tokens and task references.
* **Synchronous Refresh & Sequential Workflows:** Upon processing `Action.Execute`, the bot backend can respond directly in the HTTP 200 response body with a replacement Adaptive Card (`application/vnd.microsoft.card.adaptive`). This allows `chowki` to immediately replace the pending approval card with a "Task Approved by @User" confirmation view without sending duplicate messages.
* **User-Specific Views (`refresh`):** Adaptive Cards support `refresh` blocks configured with `userIds`. This allows `chowki` to render custom card states depending on who views the card (e.g. showing "Pending Your Review" to designated approvers and "Awaiting Manager Approval" to standard channel members).
* **Bot Framework Webhook Contract:** Requests arrive as HTTP POST requests to `/api/messages` containing activity objects (`type: "invoke"`, `name: "adaptiveCard/action"`).

* [Source: https://learn.microsoft.com/en-us/adaptive-cards/authoring-cards/universal-action-model (Accessed: 2026-08-08)]
* [Source: https://learn.microsoft.com/en-us/microsoftteams/platform/task-modules-and-cards/cards/universal-actions-for-adaptive-cards/overview (Accessed: 2026-08-08)]

---

### 1.3 Web / REST Gateway & Interactive Review Interface

For environments where Slack or Teams are unavailable, `chowki` exposes a standalone, lightweight Web / REST Review Gateway.

```
+-----------------------------------------------------------------------------------+
| chowki Web Review Dashboard                                              [User: Alice] |
+-----------------------------------------------------------------------------------+
| Active Suspensions (1)                                                            |
|                                                                                   |
| Task ID: task_77182a | Workflow: payroll_processing | Step: transfer_funds        |
| Suspended At: 2026-08-08T06:15:00Z                                                 |
|                                                                                   |
| Proposed Execution Parameters:                                                    |
| {                                                                                 |
|   "recipient_iban": "DE89370400440532013000",                                     |
|   "amount_eur": 14500.00                                                          |
| }                                                                                 |
|                                                                                   |
|  [ Approve ]   [ Reject ]   [ Modify & Resume ]                                   |
+-----------------------------------------------------------------------------------+
```

#### Architecture & Endpoint Contracts
* **REST Review Endpoint (`GET /api/v1/chowki/tasks/{task_id}`):** Returns current task status, step context, proposed execution arguments, and available decision schemas.
* **Action Decision Endpoint (`POST /api/v1/chowki/tasks/{task_id}/decision`):**
  * Accepts structured approval responses containing human decisions (`APPROVE`, `REJECT`, `EDIT`, `ESCALATE`).
  * Accepts optional RFC 6902 JSON Patch arrays inside the payload to override suspended state parameters.
* **Webhook Callbacks:** Applications running `chowki` can register outbound webhook URLs (`callback_url`). When human review is requested or completed, `chowki` sends signed HTTP POST webhooks to notify downstream orchestration systems.

---

## 2. Security & Verification

```
Incoming Request (Slack / Teams / REST)
       |
       v
+-------------------------------------------------------------------+
| 1. Timestamp Skew Verification                                    |
|    |Now - Timestamp| <= 300s (Reject Replay Attacks)            |
+-------------------------------------------------------------------+
       | Pass
       v
+-------------------------------------------------------------------+
| 2. Cryptographic Signature Verification                           |
|    - Slack: HMAC-SHA256(v0:timestamp:raw_body, Secret)           |
|    - Teams: OpenID Discovery -> JWKS -> JWT RS256 Check           |
|    - REST:  HMAC-SHA256(timestamp:raw_body, Webhook Secret)       |
|    (Enforce timing-safe comparison: timingSafeEqual)              |
+-------------------------------------------------------------------+
       | Pass
       v
+-------------------------------------------------------------------+
| 3. Action Token & Anti-Replay Nonce Check                         |
|    - Verify JWT signature & expiration (exp)                      |
|    - Check single-use nonce against chowki Nonce Store            |
+-------------------------------------------------------------------+
       | Pass
       v
Execute State Patch & Warm-Resume Engine
```

### 2.1 Slack Webhook Signing & HMAC Verification

Slack authenticates requests by sending a signature calculated using HMAC-SHA256 over a canonical base string.

#### Headers & Signing Scheme
* **Headers:** `X-Slack-Signature` (format: `v0=<hex_digest>`) and `X-Slack-Request-Timestamp` (Unix epoch seconds).
* **Signing Secret:** 32-character hex string issued in Slack App Basic Information.
* **Canonical Base String Format:**
  $$\text{base\_string} = \text{"v0:"} \parallel \text{timestamp} \parallel \text{":"} \parallel \text{raw\_body}$$

#### Verification Procedure
1. **Timestamp Skew Check:** Read `X-Slack-Request-Timestamp`. Compute $\Delta t = |t_{\text{current}} - t_{\text{request}}|$. If $\Delta t > 300\text{ seconds}$ (5 minutes), reject immediately to defend against replay attacks.
2. **Canonical String Construction:** Concatenate literal `v0:`, the header timestamp, `:`, and the verbatim HTTP request body bytes (captured prior to JSON/form parsing).
3. **HMAC Calculation:** Compute $\text{HMAC-SHA256}(\text{SigningSecret}, \text{base\_string})$. Format as `v0=<hex>`.
4. **Timing-Safe Comparison:** Compare expected signature with `X-Slack-Signature` header using a constant-time comparison function (`hmac.compare_digest` in Python, `crypto.timingSafeEqual` in Node.js) to prevent timing side-channel attacks.

* [Source: https://api.slack.com/authentication/verifying-requests-from-slack (Accessed: 2026-08-08)]
* [Source: https://tools.jarhalab.com/guides/how-to-verify-slack-request-signatures (Accessed: 2026-08-08)]

---

### 2.2 Microsoft Teams Bot Framework JWT Authentication

Microsoft Teams authentication relies on Bearer JWT tokens delivered in the HTTP `Authorization` header of incoming Bot Framework activities.

#### Verification Procedure
1. **Token Extraction:** Extract Bearer token from `Authorization: Bearer <token>`.
2. **OpenID Metadata Discovery:** Retrieve OpenID configuration from `https://login.botframework.com/v1/.well-known/openidconfiguration` (or tenant-specific Microsoft Entra ID metadata for SingleTenant bots).
3. **JWKS Key Retrieval:** Fetch valid public keys from the `jwks_uri` specified in OpenID metadata.
4. **JWT Cryptographic & Claim Validation:**
   * **Signature:** Validate cryptographic signature against retrieved JWKS public keys using algorithm specified in JWT header (RS256).
   * **Issuer (`iss`):** Confirm `iss` equals `https://api.botframework.com` (MultiTenant) or `https://sts.windows.net/{tenantId}/` (SingleTenant).
   * **Audience (`aud`):** Confirm `aud` matches `chowki` Bot's Microsoft App ID (Application GUID).
   * **Expiration (`exp` & `nbf`):** Confirm token is within valid lifetime, allowing maximum 5 minutes clock skew.
   * **Service URL Validation:** Ensure `serviceUrl` claim matches the `serviceUrl` property present in incoming Activity object.

* [Source: https://learn.microsoft.com/en-us/azure/bot-service/rest-api/bot-framework-rest-connector-authentication (Accessed: 2026-08-08)]

---

### 2.3 Custom Webhook Verification for REST Gateway

For generic incoming REST webhooks, `chowki` enforces an HMAC-SHA256 signature protocol:
* **Headers:** `X-Chowki-Signature` (`sha256=<hex_digest>`) and `X-Chowki-Timestamp`.
* **Signing:** $\text{HMAC-SHA256}(\text{Secret}, \text{timestamp} \parallel \text{"."} \parallel \text{raw\_body})$.
* **Timing-Safe Match:** Validate using constant-time comparison algorithms after verifying that timestamp skew does not exceed 300 seconds.

---

### 2.4 Secure Token Handling, Nonces & TTL Expiration

To prevent parameter tampering, authorization bypass, and replay attacks, `chowki` enforces token-based action authorization:

#### Scope-Bound Signed Tokens
Actions embedded in UI buttons do not rely on plain task IDs. `chowki` issues compact signed JWTs or HMAC action tokens containing:
```json
{
  "task_id": "task_8f91a2",
  "step_id": "execute_sql_query",
  "permitted_actions": ["APPROVE", "REJECT", "EDIT"],
  "allowed_roles": ["finance_admin"],
  "nonce": "c9a4b2e1-8012-4fdf-9b22-81102931bc11",
  "iat": 1786179300,
  "exp": 1786265700
}
```

#### Anti-Replay Nonce Tracking
* Each token includes a cryptographically random UUIDv4 `nonce`.
* When an action POST is received, `chowki` checks its atomic Key-Value Nonce Store (Redis, SQLite, or In-Memory KV).
* If the `nonce` has already been recorded, `chowki` rejects the request with HTTP 409 Conflict ("Action Already Processed").
* If unrecorded, the `nonce` is written with a TTL matching token expiration (`exp`).

#### TTL Expiration
* Action tokens default to a strict TTL (e.g. 24 hours).
* Expired tokens trigger automatic task escalation or timeout handlers in the `chowki` execution engine.

> **Amendment (2026-08-11, normative — token lifecycle as shipped):** the wire format
> is `b64url_nopad(msgpack(claims)) + "." + b64url_nopad(HMAC_SHA256(secret, body))`
> — pinned with the exact claims struct and verification order in
> `07-cross-sdk-parity.md` §6, so either SDK can verify the other's tokens. Three
> corrections to the prose above from the implementation: (a) nonces are **lifetime
> single-use** — consumed rows are never garbage-collected on expiry, because deleting
> an expired row would make an already-consumed token replayable; reclamation is an
> explicit Phase 2 maintenance operation. (b) "Expired tokens trigger automatic
> escalation or timeout handlers" presumes a timer subsystem no document designs —
> a pure in-process library has no scheduler; durable timers are a Phase 6 item and
> today expiry simply fails verification. (c) A lost or burnt token (e.g. a resume
> attempt that consumed the nonce and then failed) is recovered with
> **`chowki.reissue_token(run_id)`** — re-mints from the stored `PauseRequest` with
> identical scope and a fresh nonce, re-notifying the gateway; every SDK must ship
> it, and `PauseRequest` now carries `origin` (`"gate"` | `"auto"`) which resume
> seeding depends on (`07-cross-sdk-parity.md` §9). `allowed_roles` is carried but
> **not yet enforced** — the authorization layer is Phase 4.

---

## 3. Approval UX Patterns & State Patching

### 3.1 Interactive Approval UX Patterns

`chowki` standardizes four core human interaction patterns across all gateway channels:

```
                  +--------------------------+
                  | Workflow Suspended       |
                  | (@chowki.pause)          |
                  +--------------------------+
                               |
            +------------------+------------------+
            |                  |                  |
            v                  v                  v
       [ Approve ]        [ Reject ]       [ Edit & Resume ]
            |                  |                  |
            v                  v                  v
   Unconditional Resume  Abort Execution   Apply RFC 6902 Patch
   (Pass original state) (Trigger Fallback) (Inject State Override)
                                                  |
                                                  v
                                         Warm-Resume Engine
```

1. **Approve:**
   * **Intent:** Unconditional approval of proposed agent step output or tool execution.
   * **State Effect:** Zero state modification. `chowki` flags human approval as `true` in metadata and resumes workflow execution from saved snapshot.
2. **Reject:**
   * **Intent:** Explicit denial of proposed step execution.
   * **State Effect:** Halts current execution branch. Sets task status to `REJECTED`, raises `chowki.exceptions.HumanRejectedError`, or triggers alternative fallback branches defined in workflow logic.
3. **Edit & Resume (State Override):**
   * **Intent:** Human reviewer inspects proposed LLM tool arguments or intermediate outputs, modifies specific attributes (e.g. altering target email recipient or fixing SQL parameters), and resumes execution.
   * **State Effect:** Generates an RFC 6902 JSON Patch mapping changes between original proposed state and human-edited state. Applies patch atomically to state snapshot before warm-resuming.
4. **Escalate:**
   * **Intent:** Reviewer re-assigns task to higher authority or requests additional context.
   * **State Effect:** Suspends state remains active, updates assignment metadata, and dispatches secondary notification cards to escalation channels.

---

### 3.2 RFC 6902 JSON Patching & State Override Injection

`chowki` uses **RFC 6902 (JSON Patch)** as the standard protocol for expressing human state modifications.

#### RFC 6902 Operations
RFC 6902 defines six atomic patch operations: `add`, `remove`, `replace`, `move`, `copy`, and `test`.

```json
[
  { "op": "test", "path": "/step_input/status", "value": "PENDING_REVIEW" },
  { "op": "replace", "path": "/step_input/recipient_email", "value": "verified_vendor@company.com" },
  { "op": "add", "path": "/metadata/human_reviewer_note", "value": "Corrected vendor domain per policy" }
]
```

#### State Override Injection Workflow in `chowki`
1. **Suspension Point:** Agent workflow reaches `@chowki.pause()`. Engine serializes state snapshot $S_{\text{orig}}$ to durable storage.
2. **Human Modification:** Human reviewer edits input fields in Slack modal or Web UI. Frontend computes delta between $S_{\text{orig}}$ and $S_{\text{modified}}$, outputting RFC 6902 JSON Patch $P$.
3. **Atomic Patch Application:** Upon receiving $P$, `chowki` executes atomic patch application (`apply_patch(S_{\text{orig}}, P)`). The `test` operation guarantees optimistic concurrency (verifying original state hasn't changed).
4. **Warm-Resume Injection:** Updated state snapshot $S_{\text{patched}}$ is saved, and `chowki` warm-resumes workflow execution instantly at the step boundary.

* [Source: https://datatracker.ietf.org/doc/html/rfc6902 (Accessed: 2026-08-08)]
* [Source: https://docs.ag-ui.com/concepts/state (Accessed: 2026-08-08)]

---

### 3.3 Audit Logging, Governance & Provenance Tracking

To maintain enterprise governance and regulatory compliance, `chowki` logs every human intervention into an append-only, tamper-evident audit log.

```json
{
  "audit_id": "aud_01J4K82M9A11",
  "timestamp": "2026-08-08T06:22:00Z",
  "task_id": "task_8f91a2",
  "step_id": "execute_sql_query",
  "actor": {
    "platform": "slack",
    "user_id": "U019A82BC11",
    "email": "alice@company.com",
    "ip_address": "192.0.2.45"
  },
  "action": "EDIT_AND_RESUME",
  "original_state_hash": "sha256:8f2a1b94c...",
  "patched_state_hash": "sha256:3a91e028b...",
  "json_patch": [
    { "op": "replace", "path": "/step_input/amount", "value": 5000.00 }
  ],
  "verification_details": {
    "signature_type": "slack_hmac_sha256",
    "nonce": "c9a4b2e1-8012-4fdf-9b22-81102931bc11",
    "signature_verified": true
  }
}
```

#### Governance Principles
* **State Hash Chain:** Every audit record links `original_state_hash` to `patched_state_hash` (SHA-256), establishing strict lineage provenance.
* **Actor Attribution:** Non-repudiable mapping connecting human interaction payloads to authenticated user IDs (Slack Member ID, Entra User Object ID, or OAuth subject).
* **Immutability:** Audit records are persisted to append-only storage adapters (e.g. PostgreSQL audit tables, S3 WORM storage, or local JSONL files).

---

## 4. Architectural Synthesis & Channel Comparison Matrix

| Technical Axis | Slack Block Kit | Microsoft Teams Adaptive Cards | Web / REST Gateway |
| :--- | :--- | :--- | :--- |
| **Payload Schema** | Block Kit JSON (`blocks`) | Adaptive Cards Schema v1.5/1.6 | JSON Schema / REST OpenAPI v3 |
| **Action Execution** | `block_actions`, `view_submission` | `Action.Execute` (`adaptiveCard/action`) | HTTP POST `/tasks/{id}/decision` |
| **In-place Update Model** | `response_url` (`replace_original`) or `chat.update` | Synchronous HTTP 200 Invoke card replacement | HTTP 200 response + SSE / WebSockets |
| **Security / Auth** | HMAC-SHA256 (`X-Slack-Signature`) | JWT RS256 (Bot Framework OpenID/JWKS) | HMAC-SHA256 or OAuth2 Bearer JWT |
| **Replay Defense** | `X-Slack-Request-Timestamp` (5-min skew) | JWT `exp` / `nbf` + 5-min clock skew | `X-Chowki-Timestamp` (5-min skew) |
| **State Override Capability** | Modals (`views.open`) | Input fields in Card + `Action.Execute` | Interactive Form / JSON Editor |
| **Primary Use Case** | Operations & Dev Teams in Slack | Enterprise M365 Corporate Workflows | Custom Admin Consoles & External APIs |

---

## 5. `chowki` HITL Implementation Blueprint

```
+-----------------------------------------------------------------------------------+
|                                 chowki Engine                                     |
|                                                                                   |
|   @chowki.pause(channel="slack", reviewers=["U12345"])                            |
|   1. Serialize State Snapshot                                                     |
|   2. Generate Signed Action Token + Nonce                                         |
|   3. Dispatch Interactive UI Payload (Slack / Teams / REST)                       |
+-----------------------------------------------------------------------------------+
                                          |
                                          v (Awaiting Human Response)
+-----------------------------------------------------------------------------------+
|                              chowki HITL Gateway                                  |
|                                                                                   |
|   1. Ingress Listener (/api/chowki/webhooks/slack | /teams | /rest)              |
|   2. Verify Request Signature (HMAC / JWT) & Timestamp Skew                       |
|   3. Validate Action Token & Check Anti-Replay Nonce Store                        |
|   4. Parse Human Decision (APPROVE / REJECT / EDIT / ESCALATE)                    |
|   5. Generate RFC 6902 JSON Patch (if state modified)                             |
|   6. Write Immutable Audit Log Event                                              |
+-----------------------------------------------------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
|                           chowki Warm-Resume Engine                               |
|                                                                                   |
|   1. Apply RFC 6902 State Patch to Snapshot                                       |
|   2. Instantly Resume Execution at Step Boundary                                  |
|   3. Issue In-Place UI Confirmation Update to Channel                             |
+-----------------------------------------------------------------------------------+
```

### Key Integration Directives
1. **Raw Body Retention:** Incoming webhook endpoints MUST capture raw request bytes prior to framework body parsing to enable accurate HMAC-SHA256 signature verification.
2. **Constant-Time Comparison:** All HMAC digest comparisons MUST use timing-safe equality functions (`hmac.compare_digest` / `crypto.timingSafeEqual`).
3. **Atomic Nonce Tracking:** Every interaction token MUST consume a single-use nonce stored in `chowki`'s state backend to eliminate double-submit and replay vulnerabilities.
4. **RFC 6902 Delta Engine:** State modifications submitted via human review interfaces MUST be transformed into RFC 6902 JSON Patch arrays, applied atomically with `test` pre-conditions before warm-resuming task execution.
5. **Product Name Consistency:** All modules, protocols, and documentation MUST refer to `chowki` exclusively.
