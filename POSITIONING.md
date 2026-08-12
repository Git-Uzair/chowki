# chowki: Competitive Position & Discoverability

Research notes, 2026-08-12. Covers the durable-execution landscape, where chowki wins and
loses against it, and concrete metadata changes for search discoverability.

> **This file trips `scripts/check_layout.py`.** The guard bans the literal string
> `check` + `point` in every text file in the repo, and this document has to discuss that
> word to be useful. Add `POSITIONING.md` to `.gitignore`, or keep this file outside the
> tree. See Finding 0.

---

## Contents

- [Finding 0: the banned word is an own goal](#finding-0-the-banned-word-is-an-own-goal)
- [The landscape](#the-landscape)
- [What chowki offers that the others don't](#what-chowki-offers-that-the-others-dont)
- [Where chowki lacks](#where-chowki-lacks)
- [Deep dive: "resumable workflows take no required arguments"](#deep-dive-resumable-workflows-take-no-required-arguments)
- [Discoverability](#discoverability)
- [Prioritised action list](#prioritised-action-list)
- [Evidence and open questions](#evidence-and-open-questions)

---

## Finding 0: the banned word is an own goal

The layout guard treats `check` + `point` as a banned product term and fails CI on any
occurrence, anywhere, including documentation and comparison prose.

The discipline is defensible for *product vocabulary*. chowki's mechanism genuinely is not
the same thing as a LangGraph state saver, and inventing a distinct name for it ("warm
resume") is good positioning. Keep that.

The problem is that the word is also the **primary term the target user already has in their
head**. LangChain's own documentation says, verbatim:

> "Nodes after the [state save point] re-execute, including any LLM calls, API requests, and
> interrupts."

A developer reads that sentence, realises it means they will pay for those LLM calls twice,
and then searches. They search using LangChain's vocabulary, because that is the vocabulary
they just learned. Every query in that moment contains the banned word. A repo that has
CI-enforced zero occurrences of it cannot appear.

**Recommendation.** Keep the ban as the default, but allowlist the surfaces where you are
deliberately speaking the reader's language rather than your own:

```python
# scripts/check_layout.py
BANNED_WORD_ALLOWLIST = {
    Path("README.md"),
    Path("python/chowki/README.md"),
    Path("docs/comparison.md"),
    Path("POSITIONING.md"),
}
```

The rule then means what you actually want it to mean: *do not use the competitor's word for
our own mechanism* — rather than *never acknowledge the concept our users came here for*.

---

## The landscape

Surveyed for the specific requirements: crash-resume from last completed step, pause and
resume on demand, not re-paying for completed API calls, and rewind/fork from step N. Weighted
towards library-only, local-state options.

| | Server? | Local store | Crash-resume | Pause/resume | Skips paid calls | Fork from step N | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **DBOS Transact** (`dbos`) | no | SQLite, default | yes | yes | yes | yes | 2.29.0, Jul 2026, ~1.5k stars, weekly releases |
| **chowki** | no | SQLite, default | yes | yes | yes | no | 0.1.0 alpha |
| **Burr** (`apache-burr`) | no | SQLite persister | yes | yes | per-action only | yes | 0.42.0, ~2.5k stars |
| **LangGraph** + `SqliteSaver` | no | SQLite | yes | yes (`interrupt()`) | per-node only | yes (time travel) | mature |
| **Temporal** (`temporalio`) | yes, one binary | SQLite via `--db-filename` | yes | yes | yes | reset-to-event | mature |
| **Prefect 3** | yes, or ephemeral | SQLite + `~/.prefect` | partial | yes | with care | no | mature |
| **Restate** | yes, one binary | own store | yes | yes | yes | yes | maturing |
| **Hatchet** | yes + Postgres | none | yes | yes | yes | yes | maturing |
| `diskcache` / `joblib.Memory` | no | disk | no | no | yes | no | stable / stale |

### The competitor that matters

**DBOS Transact is the direct threat.** As of 1.13.0 (Sept 2025) the Python SDK defaults to
a SQLite file, so `pip install dbos` genuinely needs no Postgres, no Docker, and no worker.
It has documented crash-resume, `cancel_workflow`/`resume_workflow`, step-level memoisation,
`fork_workflow(id, start_step)`, and a first-class plain-Python agent story at
`docs.dbos.dev/ai/`. It also has a one-environment-variable upgrade path to Postgres for
multi-machine deployment.

Read honestly: **DBOS beats chowki on every pure durability axis.** chowki cannot win the
"durable execution" fight on durability features. It can only win on the things DBOS has no
interest in doing — approval gates with cryptographic provenance, redaction, and agent-shaped
guardrails. That should drive the positioning.

### Ones to dismiss

- **`resonate-sdk`** — the no-server "local mode" is in-memory only. Zero crash durability
  without running their Rust server. Not a competitor in this niche.
- **`edda-framework`** — closest feature match on paper, but 8 stars, alpha, single
  maintainer, idle since May 2026. Note the naming trap: PyPI `edda` is an unrelated
  abandoned 2012 MongoDB tool.
- **Hatchet** — requires Postgres plus a Go engine. Different weight class.

---

## What chowki offers that the others don't

Four differentiators are real. The rest are nice but not load-bearing.

### 1. No determinism tax

DBOS and Temporal both require a replay-safe workflow body — no `random`, no `time.time()`,
no direct I/O — and punish violations with journal-mismatch errors (Restate's `RT0016`,
Temporal's non-determinism failures). chowki isolates non-determinism at the step boundary and
rehydrates state from a snapshot rather than replaying history, which deletes that entire
error class.

`docs/research/03-durable-execution.md` already frames this as the design axis and is right to.
It is the cheapest adoption path in the category, and worth stating on the first screen as a
positive claim rather than leaving it buried in research notes.

### 2. Human-in-the-loop as a cryptographic primitive

This is the strongest feature and it is under-sold.

DBOS does HITL with `send`/`recv`. LangGraph has `interrupt()`. Both are *messages*. Neither
gives you:

- a **single-use HMAC-SHA256 token** scoped to one run and one gate, that you can safely put
  in a Slack button, an email link, or a webhook payload;
- **four decisions** where `EDIT` is an RFC 6902 patch that becomes the new state-of-record
  BASE snapshot;
- an **append-only audit log** with, per the spec, no delete API permitted on any adapter;
- `reissue_token` for the case where the approval email got lost.

That is not a durability feature, it is a **provenance and compliance feature**. "Who approved
this $40,000 refund, when, and can you prove the agent could not have proceeded without
them" is a question a regulated buyer has to answer and currently has no library for. Lead
with this.

### 3. Always-on secret redaction in the persistence path

Temporal offers a payload codec you write yourself. Nobody in this space ships three-tier
redaction — sensitive key names, a combined provider-credential regex covering
OpenAI/Anthropic/AWS/GitHub/Slack/Stripe/JWT/Bearer/Basic/private keys/URI userinfo, plus
Shannon entropy at ≥4.5 bits per code point — applied to step arguments, state snapshots, and
logs *before* anything is persisted, with HMAC-blinded stable placeholders so diffs stay
readable without exposing the secret. That it cannot be disabled is a feature, not a
limitation, and should be phrased that way.

### 4. Agent-shaped guardrails as an engine subsystem

DBOS and Temporal are domain-agnostic; they will cheerfully let an agent loop four hundred
times and bill you for it. chowki ships:

- token and cost budgets with a soft-warning threshold at 80%, re-seeded from persisted usage
  on resume;
- three tiers of loop detection — windowed content-hash repeats, normalised Levenshtein
  similarity, and delegation-graph cycle detection;
- `max_steps_per_run`;
- a six-class agent error taxonomy with a breaker matrix: `RATE_LIMIT` retries then pauses,
  `VALIDATION` re-asks twice then pauses, `CONTEXT_WINDOW` summarises once then aborts,
  `LOOP` pauses.

**The demand here is more emotional than the durability demand.** Real thread titles from
r/LangChain: "Woke up to a massive API bill. My LangGraph agent looped…", "I analyzed why
LangGraph agents burn $50 on infinite loops", "I got tired of my AI agents getting stuck in
loops and burning API credits". People search for this in a state of active pain, and the
niche is less crowded than durable execution.

### Secondary strengths

- **Auto-pause on guardrail breach** converts a crash into a human decision point with a
  token attached, and works with `gateway=None`.
- **Operator escape hatches as public API** — `release_step` ("the effect did not happen")
  and `complete_step` ("it did, memoise this") address the mid-step-death unknown-fate case
  explicitly. Temporal and DBOS make you resolve that in a web UI.
- **Encryption at rest** with AES-256-GCM and tenant+run bound in as AAD. DBOS on SQLite
  gives you a plaintext file.
- **Composes rather than replaces.** Wrapping *tools* means the framework decorator goes
  outside and `@chowki.step` underneath. DBOS asks you to make your loop its workflow.
- **Engineering discipline as a trust signal** — 523 tests, three OSes × three Python
  versions, a lowest-dependency job, performance budgets as executable code, a layout guard,
  a cross-SDK conformance spec. For an alpha library asking to sit in someone's payment path,
  this is a real adoption argument and is currently buried in the Development section.

---

## Where chowki lacks

Ordered by "would stop me adopting it."

### 1. Parallelism inside a run is undefined behaviour

`features.md` calls concurrency within a run (`asyncio.gather`, `Promise.all`) undefined
behaviour until Phase 6 branch keys. `limits.md` says it causes state delta corruption,
because snapshots rely on strict step ordering and linear RFC 6902 patching.

Agents are going parallel quickly — fan-out to N tools, N sub-agents, N documents. Every
competitor supports this. And "undefined behaviour that corrupts state" is a worse thing to
publish than "not supported yet".

**Minimum fix before anyone adopts:** detect concurrent step entry within a single run and
raise. A loud error costs a day; silent state corruption costs your reputation on the first
bug report.

### 2. No timers and no signals, which undermines the best feature

A resumable workflow cannot sleep, and a paused run cannot be woken by an external event —
the caller must re-invoke it. But the entire premise of an approval gate is that the human
takes three days to respond.

Right now the HITL story requires the adopter to build their own polling loop and their own
reminder logic. Durable timers plus "external signal wakes a paused run" is scheduled for
Phase 6. Given that HITL is the wedge, it belongs much earlier.

### 3. Resumable workflows must take no required arguments

See the dedicated section below. This is worse than the docs make it sound, because the
documented workaround has a silent-failure mode.

### 4. Complex arguments collapse to `<TypeName>` in the args hash

Complex objects — msgspec Structs, Pydantic models, custom classes — are not expanded by the
sanitiser and collapse to `<TypeName>`. Two *different* instances of the same class therefore
produce the same `args_hash`, and a step returns the wrong memoised result.

This is the one item on the list that is a **correctness bug rather than a limitation**, and
it is currently documented as a "pass primitives instead" advisory. It should either hash
structurally or raise on an unhashable argument type. Silent wrong answers in a library whose
entire value proposition is trustworthy memoisation is the worst possible failure mode.

### 5. Recovery is manual and footgun-shaped

- `recover_runs` makes no liveness check and cannot distinguish a crashed process's run from
  one a live worker is executing right now.
- `rerun()` does not refuse a run still marked `RUNNING`, so calling it against a live fleet
  executes the same workflow twice.

Both are documented, which is honest, but DBOS recovers automatically on `launch()`. Even
single-process, a heartbeat column plus a lease TTL would let you say "safe by default"
instead of "run this only when no worker is active."

### 6. No fork-from-step-N

DBOS has `fork_workflow(id, start_step)`; Burr has `fork_from_sequence_id`; LangGraph has time
travel. For agents this is the killer debugging affordance: step 5 succeeded but returned
garbage, so re-run from step 5 with a better prompt and keep steps 1–4 paid for.

You already have per-step-name ordinals, snapshot indices that always continue above the
stored max, and delta patching. This looks like days of work for a headline capability, and it
is currently the most visible feature gap against DBOS.

### 7. Non-serializable returns silently degrade

A step whose return value MessagePack cannot encode is recorded `COMPLETED` with a diagnostic
marker and flagged not replayable — so the body runs again on the next warm resume and its
side effect happens twice. This is a documented exception to the zero-loss claim that most
readers will skim past. Consider making it opt-in loud: warn on first occurrence, or offer a
strict mode that raises.

Related: a `msgspec.Struct` returned by a step comes back from a replay as a plain `dict`, so
attribute access and `isinstance` break on resumed results. Correct, documented, and still
going to surprise everyone once.

### 8. Single-process SQLite only

Fine for the stated audience. But there is no "graduate to production" path today, and DBOS's
one-environment-variable swap from SQLite to Postgres is a strong competitive answer you have
no reply to. Phase 5 is the right place for it; just be aware this is the objection you will
hear most from anyone evaluating seriously.

### 9. Smaller items

- **Not power-loss safe.** `synchronous=NORMAL` means the last commits before a power cut are
  not guaranteed. Documented, minor, but worth a line in the README rather than only in
  `limits.md`.
- **`max_steps_per_run` defaults to 25**, which will surprise anyone running a long agent.
- **`allowed_roles` is carried in tokens but unenforced** until Phase 4. A security-shaped
  field that does nothing is a liability; either enforce it or drop it from the token until
  you do.
- **`ABORTED` is never set by the engine** — an aborting breaker records `FAILED`. A status in
  a public enum that the system never produces will confuse anyone building a dashboard.
- **The Slack "not shipped" caveat appears three times** across the two READMEs. Once is
  honest; three times reads as apologising for your own roadmap in a document whose job is to
  build confidence.
- **Five features under one name makes the pitch fuzzy.** "In-process control plane for LLM
  agents" is a category nobody searches for. Lead with one problem, list the rest underneath.

---

## Deep dive: "resumable workflows take no required arguments"

### What is actually happening

chowki's resume model is *re-execution*, not replay. When you call `resume()` or `rerun()`, it
does not restore a suspended stack or step through a recorded history. It **calls your
workflow function again, from line 1**, and relies on `@chowki.step` returning cached results
so the completed work does not repeat.

To call your function again, it needs the arguments. And it does not have them:

```python
# python/chowki/src/chowki/core/resume.py
def _invoke_workflow(workflow_fn: Callable[..., Any], run_id: str) -> Any:
    ...
```

`resume()` and `rerun()` invoke the workflow as `workflow_fn(run_id=run_id)`. That keyword is
the **only** argument they pass. The arguments of the original call were never written into the
run record, so they cannot be replayed.

So this workflow can be started but never resumed:

```python
@chowki.workflow
def billing_agent(invoice_id: str) -> str:      # required parameter, no default
    return pay_invoice(fetch_invoice(invoice_id))

billing_agent(invoice_id="inv-999", run_id="run-1")   # runs, then pauses at the gate
chowki.resume(run_id="run-1", token=token, decision=chowki.Decision.APPROVE)
# TypeError: billing_agent() missing 1 required positional argument: 'invoice_id'
```

`resume` tried to call `billing_agent(run_id="run-1")`. Python has nothing to bind
`invoice_id` to. The run is now stuck: it is `PAUSED` in storage, holding a valid token, and
every resume attempt raises `TypeError` before the body executes.

### Why the documented workaround is a trap

The docs say to give every parameter a default, and the README example does exactly that:

```python
@chowki.workflow
def billing_agent(invoice_id: str = "inv-1") -> str: ...
```

That default is not cosmetic — it is load-bearing, and it is where it gets dangerous. Consider
the realistic case where the default is a placeholder and the real call passes something else:

```python
billing_agent(invoice_id="inv-999", run_id="run-1")
```

First execution:

- `fetch_invoice("inv-999")` runs, is recorded as `fetch_invoice#0` with `args_hash("inv-999")`
- the run pauses at the approval gate

Resume:

- chowki calls `billing_agent(run_id="run-1")`
- `invoice_id` binds to the **default**, `"inv-1"`
- the body reaches `fetch_invoice("inv-1")` → step identity `fetch_invoice#0`, but
  `args_hash("inv-1")` does not match the stored `args_hash("inv-999")`
- **cache miss.** The step re-executes — with the wrong invoice.

No exception. No warning. The run resumes successfully and processes a completely different
invoice from the one the human approved. In a payments workflow that is the worst class of bug
this library could possibly have: memoisation silently stops working *and* the approved
decision is applied to the wrong entity.

The `TypeError` from a missing default is a good failure. This is a bad one, and it is the
outcome the documentation currently recommends.

### The correct pattern

Per-run inputs belong in **run state**, not in the signature — because state *is* restored
from the last snapshot before the body re-executes. Read the input from state on resume, and
only trust the parameter on the first execution:

```python
@chowki.workflow
def billing_agent(invoice_id: str | None = None) -> str:
    state = chowki.current_run().state

    if invoice_id is not None:          # first execution: record the input
        state["invoice_id"] = invoice_id
    invoice_id = state["invoice_id"]    # resume: read it back from the snapshot

    return pay_invoice(fetch_invoice(invoice_id))
```

Now resume calls `billing_agent(run_id="run-1")`, `invoice_id` is `None`, and the real value
comes back out of restored state. `args_hash` matches, the cache hits, and `fetch_invoice`
does not run again.

Two caveats on this pattern. It has not been executed against the current implementation, so
verify that mutating `current_run().state` before the first step is captured in that step's
delta — `concepts.md` warns that in-place mutation of *outer-scope* variables is not captured,
and this relies on `state` being the tracked object. And a `None` default on a
domain-required field weakens your type signature, which is annoying in a library that is
otherwise strict about typing.

### What should change in chowki

This is fixable and should be fixed, because every user hits it in the first ten minutes.

1. **Persist the original call arguments in the run record** and replay them on resume. DBOS
   does this; it is why DBOS workflows can take normal arguments. It requires the arguments be
   serialisable, which is already true of everything else you snapshot.
2. **Failing that, detect the trap at decoration time.** `@chowki.workflow` already inspects
   the signature — it raises if a parameter is named `run_id` or `tenant_id`. Extend that: if
   any parameter has no default, either raise immediately ("this workflow cannot be resumed")
   or emit a warning. Turning a runtime `TypeError`-on-resume into an import-time error is
   cheap and removes the whole class of confusion.
3. **Warn on the silent case.** If a resumed execution produces an `args_hash` miss on step
   ordinal `#0` when a completed record exists for that step id, that almost certainly means
   arguments drifted between executions. Log it loudly. That single warning would catch the
   wrong-invoice scenario above.
4. **Document the state-based pattern as the primary recipe**, with the default-value approach
   demoted to "only safe when the default is the real value."

---

## Discoverability

The core problem is not that the vocabulary is missing. Both READMEs already say
`exactly-once`, `idempotent`, `crash recovery`, `audit log`, `loop detection`, `token budgets`.
**None of it has been promoted into the fields that search engines and PyPI actually index.**
That is the cheapest available win.

Secondary problem: the docs consistently use British *memoised*. Search volume is
overwhelmingly American *memoization*. Use the American spelling in the summary, keywords, and
at least one heading; keep both in body prose.

### PyPI summary — the highest-leverage 200 characters

Current:

```
In-process agent state preservation, guardrails, and warm resume.
```

`state-preservation` and `resumption` are terms you invented. Nobody searches them. Replace:

```toml
description = "Durable execution for LLM agents: crash recovery, step memoization, human-in-the-loop approval gates, secret redaction, and token budgets. No server, no worker — just SQLite."
```

### Keywords

```toml
keywords = [
  "durable-execution", "crash-recovery", "resume", "replay", "idempotency",
  "memoization", "exactly-once", "human-in-the-loop", "hitl", "approval-workflow",
  "audit-log", "guardrails", "circuit-breaker", "loop-detection", "token-budget",
  "cost-control", "secret-redaction", "llm", "agents", "ai-agents", "llmops",
  "langchain", "langgraph", "sqlite",
]
```

Add the banned word to this list once the guard is allowlisted; it belongs here more than any
other single term.

### Classifiers to add

Everything currently present is correct. The gap is that the package carries **no AI signal at
all**. These are the Trove strings to add — `uv build` hard-fails on an invalid classifier, so
the build is the verification step:

```toml
"Topic :: Scientific/Engineering :: Artificial Intelligence",
"Topic :: System :: Recovery Tools",
"Topic :: System :: Distributed Computing",
"Topic :: Security :: Cryptography",
"Topic :: System :: Monitoring",
"Topic :: Database",
"Intended Audience :: System Administrators",
```

There is no `Framework :: LangChain` classifier, so framework affinity has to live in keywords
and prose.

On PyPI: classifiers render as links into faceted search, and `keywords` render as "Tags" on
the project page. The `description` field is the highest-weighted free text you control, ahead
of the README. Warehouse's own maintainers acknowledge that "solution searches" are poorly
served because project metadata is "sometimes lacking or misleading" — which is the
opportunity, since most packages in this space have not bothered.

### GitHub "About" box

Indexed, and it is what renders in GitHub search results:

> Durable execution for LLM agents — crash recovery, step memoization, human approval gates,
> secret redaction, and token budgets. Decorators, not infrastructure. No server,
> SQLite-backed.

### GitHub topics

Cap is 20. Ordering below is judgement, not measured traffic — the topic repo-counts were not
verified:

```
durable-execution   crash-recovery   idempotency        exactly-once
human-in-the-loop   approval-workflow  audit-log        guardrails
circuit-breaker     llm              ai-agents          agents
llmops              langchain        langgraph          crewai
sqlite              python           workflow-engine    observability
```

### The wedge sentence for the first screen

Add this near the top of both READMEs, because it is the one sentence that explains why chowki
exists next to LangGraph:

> State savers restore *state*. chowki memoises *step results* — so on resume, the LLM calls
> and API requests that already succeeded do not happen again.

This works because it answers a question the reader has already been asked by LangChain's own
documentation, in LangChain's own words.

### Queries to target

Pulled from LangChain forums, r/LangChain, r/LLMDevs, and GitHub issues. Each should appear
close to verbatim as an H2 or FAQ line, because those become indexed anchors:

**Durability**

- resume a LangGraph agent after a crash
- don't re-execute completed steps on resume
- avoid re-running LLM calls on retry
- preserve agent state across restarts
- durable workflow without Temporal
- exactly-once side effects in an LLM agent
- crash recovery for a Python agent with SQLite

**Approval / HITL**

- pause an agent for human approval before sending an email
- approve an agent action before it executes
- Slack approve/reject buttons for an agent action
- audit trail of agent decisions — who approved what

**Secrets**

- redact API keys from LLM logs
- prevent secrets leaking into LLM context
- encrypt agent state at rest

**Cost and loops**

- stop runaway agent retry loops
- detect an agent infinite loop before it drains credits
- hard spend cap per agent run
- token budget limit for an LLM agent

The cost-and-loops cluster is where the emotional demand is highest and the competition
thinnest. Consider whether the *first* thing a visitor reads should be about money rather
than durability.

### The name

"Chowki" collides with furniture (a low wooden stool), South Asian place names, and *police
chowki*. The bare name will never rank organically. The only mitigation that works is to never
ship it alone — always `chowki — durable execution for LLM agents` in the GitHub About, the
PyPI summary, the README masthead area, the social card, and any Show HN title. Let the
descriptor phrase do the ranking and the disambiguation.

### Two structural moves worth more than any keyword

**Add `docs/comparison.md`.** chowki vs DBOS vs Temporal vs LangGraph state savers vs Burr,
with an honest "use DBOS instead if…" row. Comparison pages rank extremely well, and
counterintuitively, sending people away when you are the wrong fit is what makes the
recommendation credible when you are the right one. You have the research to write this
already, in `docs/research/01-landscape.md`.

**Publish.** The roadmap notes the v0.1.0 PyPI push is still pending maintainer execution,
`dist/` holds only `.dev` builds, and `main` has no upstream configured locally. Until the
release lands, the README's PyPI badge renders as "package not found" — and that badge is the
first thing anyone looks at.

---

## Prioritised action list

**Before telling anyone about it**

1. Raise on concurrent step entry within a run, instead of corrupting state.
2. Fix the `<TypeName>` args-hash collapse, or raise on unhashable argument types.
3. Raise at decoration time when a workflow has a parameter without a default.
4. Publish v0.1.0 so the badge resolves.

**Metadata, one sitting**

5. Rewrite the PyPI `description`.
6. Replace `keywords`; add the AI and recovery classifiers.
7. Set the GitHub About box and the 20 topics.
8. Add the wedge sentence to both READMEs; cut the Slack caveat to one occurrence.
9. Allowlist the banned word in README and comparison surfaces.

**Next release**

10. Persist and replay original workflow arguments.
11. `fork_workflow`-equivalent: re-run from step N, keeping earlier steps.
12. `docs/comparison.md`.
13. Move the test/CI/perf-budget credibility signals out of the Development section and into
    a visible badge row or a "Why trust an alpha" note.

**Roadmap reshuffle to consider**

14. Pull durable timers and external-signal-wakes-paused-run forward from Phase 6, because
    they are what make the HITL wedge actually usable.
15. Either enforce `allowed_roles` or remove it from the token until you do.

---

## Evidence and open questions

**Verified this session.** DBOS SQLite default and version (docs.dbos.dev, PyPI, GitHub
release 1.13.0); DBOS `cancel_workflow`/`resume_workflow`/`fork_workflow` semantics; LangGraph
`SqliteSaver` and the "nodes after the [save point] re-execute" wording; Resonate local mode
being in-memory; Temporal dev server being a single SQLite-backed binary; Prefect's
`RUN_ID`-scoped cache default; the real query vocabulary from LangChain forums, r/LangChain
threads, and langgraph issue #6731; PyPI's own maintainers on search-quality limits. All
chowki claims read directly from this repo.

**Not verified.** GitHub topic repo-counts and the exact topics used by competitor repos —
Firecrawl's daily quota ran out. The Trove classifier strings above are from prior knowledge,
not re-read from `pypi.org/classifiers`; `uv build` will reject any that are wrong. The
warehouse search field-weighting is a hypothesis — `warehouse/search/queries.py` is the file
to read. No SERP check was run on the name "chowki". The recommended state-based argument
pattern has not been executed against the current implementation.
