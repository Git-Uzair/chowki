# chowki — Phase 2 Plan: Release v0.1

**Plan file:** `docs/plans/02-release.md`
**Date:** 2026-08-11
**Covers:** Roadmap Phase 2 — everything between the current tree and a public,
showcasable PyPI release. Nothing else: spec prep, GC, atomicity, and wire changes
moved to Phase 3 (`docs/plans/00-roadmap.md`).
**Sources of truth:** `docs/features.md` (rows marked 🔜 2),
`docs/research/07-cross-sdk-parity.md`, `AGENTS.md`.

**Release invariant:** Phase 2 makes **no wire-format changes**. The v0.1 on-disk
format is Phase 1's, exactly. Any task that seems to need one is out of scope.

**Versioning:** the tag is `v0.1.0` — "v1" in conversation means *first public
release*, not semver 1.0.0. `1.0.0` is reserved for API stability (post Node parity);
until then minor versions may adjust APIs with CHANGELOG notice.

## Conventions binding on every task

- **TDD is mandatory** (`AGENTS.md` §6): write the named tests first, watch them fail
  for the stated reason, then implement.
- One commit per task; flip the task's `**Status:**` marker in the same commit.
- Changes under `chowki/state/` or `chowki/core/` run the benchmark suite
  (`AGENTS.md` §5); budgets are non-negotiable.
- **Update `docs/features.md`** rows 🔜→✅ as tasks land (`AGENTS.md` §8).
- `pyright` strict + `mypy --strict` + `ruff check` + `ruff format --check` +
  `scripts/check_layout.py` green before a task is done.

Task order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10. Docs tasks 5–7 may run in
parallel once 1–2 are in; Task 8 requires all code tasks done.

---

## Task 1 — Workflow registry, resume-by-name, and `rerun`

**Status:** COMPLETED
**Failed Verify Cycles:** 1
**Attempt Ledger:**
- attempt 1: implemented registry, resume-by-name, rerun -> FAIL (resolution happened after state mutation, README quickstart mirror missed, docs/features.md public API list missed, duplicate lookup logic in resume.py)
- attempt 2: resolve workflow before state mutation, added _resolve_workflow helper, updated README.md and docs/features.md -> PASS

**Goal:** callers stop passing workflow function references around; recovered runs
become re-runnable. This is the enabler for both the CLI and the embed-in-your-app
resume story. Removes the `TypeError` heuristic in `core/resume.py`
(`_invoke_workflow`).

**Files:** new `python/chowki/src/chowki/core/registry.py`,
new `python/chowki/tests/unit/test_registry.py`; modified `core/runner.py`,
`core/resume.py`, `chowki/__init__.py`, `tests/unit/test_public_api.py`,
`examples/python/quickstart.py` (+ its README mirror).

**Behavior:**
1. `registry.py`: process-global `dict[str, Callable[..., Any]]` with
   `register_workflow(name, fn)` (re-registering a different function under an
   existing name logs a `structlog` warning and replaces — test suites redefine
   workflows constantly), `get_workflow(name) -> Callable | None`,
   `registered_workflows() -> dict` (copy), `clear_registry()` (test isolation;
   autouse fixture in `conftest.py`).
2. `@chowki.workflow` gains `register: bool = True` and registers the **wrapper**
   under the effective workflow name at decoration time.
3. `resume()`: `workflow_fn` becomes optional, new keyword `workflow: str | None`.
   Resolution order: explicit `workflow_fn` → `workflow` name via registry →
   `run.workflow` via registry. Unresolvable → `ChowkiConfigError` telling the caller
   to import the defining module or pass `workflow_fn`. Delete `_invoke_workflow`'s
   message-sniffing fallback.
4. New public `chowki.rerun(run_id, *, engine=None)`: loads the run record, resolves
   its workflow from the registry, invokes it with `run_id=` (memoisation does the
   rest). This is what you call on `recover_runs()` output. For an async workflow it
   returns the coroutine (caller awaits); document alongside Task 2.

**Tests first** (`test_registry.py`): decoration registers; `register=False` opts
out; `resume(run_id=..., token=..., decision=...)` with **no** `workflow_fn`
completes a paused run; resolution by explicit `workflow=` name; unresolvable name
raises `ChowkiConfigError`; `rerun` completes a recovered PENDING run without
re-executing memoised steps (call-count assert); replacement warning fires once.
Update the pinned public-API set (`rerun` joins `__all__`).

**Done-when:** unit suite green; the quickstart drops its `workflow_fn=` argument.

---

## Task 2 — Async-aware resume: `aresume` (fixes a verified bug)

**Status:** COMPLETED
**Failed Verify Cycles:** 1
**Attempt Ledger:**
- attempt 1: implemented aresume and _decide helper -> FAIL (docs/features.md public API row omitted aresume)
- attempt 2: added aresume to docs/features.md public API row -> PASS

**Goal:** `resume()` on an **async** workflow currently returns an unawaited
coroutine: the body never re-executes, the run is left RUNNING, and
`ResumeResult.value` is a coroutine object (verified by repro 2026-08-11). Every
async web app — the primary embedding target — hits this immediately.

**Files:** modified `core/resume.py`, `chowki/__init__.py`,
`tests/unit/test_public_api.py`; new `python/chowki/tests/unit/test_aresume.py`.

**Behavior:**
1. Factor `resume()`'s decision handling (token verify, state load, patch/redact,
   audit, gateway confirm, status flip, seeding) into a shared `_decide(...)` that
   ends just before workflow invocation, returning what the invocation needs.
   REJECT/ESCALATE raise from inside `_decide` exactly as today.
2. `resume()` (sync): after `_decide`, if the resolved workflow is a coroutine
   function (`inspect.iscoroutinefunction`, checked **before** any state mutation so
   a misuse leaves the run PAUSED and resumable), raise `ChowkiConfigError`
   ("async workflow: use chowki.aresume(...)"). Otherwise invoke and return
   `ResumeResult` as today.
3. New `async def aresume(...)` with the identical signature and semantics: awaits
   the workflow invocation. Sync workflows are allowed through `aresume` (invoked
   directly — result is not a coroutine).
4. `WorkflowPaused` (next gate / escalate) and `HumanRejectedError` propagate
   identically from both.

**Tests first** (`test_aresume.py`): async workflow pause → `aresume` APPROVE
completes and returns the real value; EDIT patch applies to the re-execution; REJECT
raises and marks REJECTED; a second gate raises `WorkflowPaused` with a fresh token;
`resume()` on an async workflow raises `ChowkiConfigError` **and the run is still
PAUSED and resumable afterwards** (the bug's regression test); `aresume` on a sync
workflow works. `aresume` joins `__all__` and the pinned API test.

**Done-when:** the repro script's scenario passes via `aresume`; unit suite green.

---

## Task 3 — Inspection API: `inspect_run`

**Status:** COMPLETED

**Goal:** the "inspect" leg of the control-plane pitch: one call returning everything
about a run, without disturbing live pipeline state.

**Files:** new `python/chowki/src/chowki/core/inspection.py`,
new `python/chowki/tests/unit/test_inspection.py`; modified `chowki/__init__.py`,
`tests/unit/test_public_api.py`.

**Behavior:**
1. `RunInspection` (frozen `msgspec.Struct`): `run: RunRecord`,
   `steps: list[StepRecord]` (ordinal order), `state: JSONValue | None` (latest
   **redacted** state from `snapshots_for_resume`; `None` when no snapshots),
   `audit: list[JSONObject]`, `pause: PauseRequest | None`, `resumable: bool`
   (status ∈ {PENDING, RUNNING, PAUSED}).
2. `chowki.inspect_run(run_id, *, engine=None) -> RunInspection`; unknown run raises
   `ChowkiStateError`.
3. Reconstruction uses a **fresh throwaway `SnapshotPipeline`** built from the
   engine's redactor/blobs/keyring — never `engine.pipeline_for()` — so inspecting an
   active run cannot corrupt its delta baseline. Module named `inspection.py` (not
   `inspect.py`) to avoid stdlib-name confusion.

**Tests first:** inspect a COMPLETED run (state + steps match); a PAUSED run
(`pause` populated, `resumable` true); secrets appear only as placeholders; unknown
run raises; inspect-then-resume still works (pipeline isolation); encrypted-at-rest
run inspects via the engine keyring.

**Done-when:** unit suite green; `inspect_run` in `__all__` and the pinned API test.

---

## Task 4 — CLI: `chowki` console script

**Status:** COMPLETED
**Failed Verify Cycles:** 3
**Attempt Ledger:**
- attempt 1: implemented cli.py, __main__.py, test_cli.py -> FAIL (configure/imports outside try block, import order before engine build, console.py hint missing -m / --db options, test_gateway.py test missing explicit assertion)
- attempt 2: wrapped imports and engine build in try block, imported modules before configure, updated console.py hints with -m/--db, added gateway test assertion -> FAIL (console.py called get_engine() which creates default .chowki db, and failed to get db path from active run context / explicit engine)
- attempt 3: (opus-coder) resolved DB path from current_run().engine or active_engine() -> FAIL (unquoted paths with spaces in hint, reissue-token line missing --db/-m, __main__ module handling missing, cli.py DEFAULT_DB_PATH duplicate)
- attempt 4: (opus-coder) constructed argv list & shell quoting (_format_command list2cmdline/shlex.join), common prefix for resume and reissue-token lines, script stem handling for __main__, imported DEFAULT_DB_PATH in cli.py, split_command helper in tests -> PASS

**Goal:** the operator surface for the launch demo, zero new dependencies (argparse).

**Files:** new `python/chowki/src/chowki/cli.py`, new
`python/chowki/src/chowki/__main__.py`, new
`python/chowki/tests/integration/test_cli.py`; modified
`python/chowki/pyproject.toml` (`[project.scripts] chowki = "chowki.cli:main"`),
`hitl/console.py` (print the real CLI command — it exists now).

**Behavior:**
1. Global options: `--db PATH` (default `./.chowki/chowki.db`), `--module/-m IMPORT`
   (repeatable; imports user modules so `@chowki.workflow` registration runs — the
   registry is why the CLI can resume at all), `--json` for machine-readable output,
   `--version`. Engine built once per invocation; `CHOWKI_MASTER_KEY` respected;
   `CHOWKI_RESUME_SECRET` read when set.
2. Subcommands: `runs list [--status S]`, `runs show RUN_ID` (renders
   `inspect_run`), `resume RUN_ID --token T --decision D [--patch JSON] [--note N]`
   (async workflows driven via `asyncio.run(aresume(...))`), `reissue-token RUN_ID`,
   `release-step RUN_ID STEP_ID`, `complete-step RUN_ID STEP_ID --result JSON`,
   `recover`, `rerun RUN_ID`.
3. Exit codes: 0 success; 1 with a one-line stderr message on any `ChowkiError`.
   Tokens print to stdout exactly once — never logged.

**Tests first** (integration, subprocess against a tmp SQLite db): `runs list/show`;
pause a run in-process then `resume` **via the CLI** with `-m` pointing at a fixture
module written to `tmp_path` (PYTHONPATH injected, argv lists + `sys.executable`,
`pathlib` only — Windows-safe); async-workflow resume via CLI; `reissue-token` then
resume with the reissued token; `release-step` unblocks a claim-refused run; `--json`
output parses.

**Done-when:** integration suite green; `uv run chowki --help` exits 0; console
gateway message matches reality.

---

## Task 5 — Production resume guide: approvals from your own web app

**Status:** COMPLETED
**Failed Verify Cycles:** 1
**Attempt Ledger:**
- attempt 1: implemented resuming-in-production.md, fastapi_approvals.py, test_embedding_recipes.py -> FAIL (fastapi route params typed req: Any instead of ResumeRequest/BackgroundTasks, test file duplicated handler instead of testing fastapi_approvals.py, permitted_actions scoping note missing in guide)
- attempt 2: annotated FastAPI route params req: ResumeRequest and background_tasks: BackgroundTasks, imported process_resume/aprocess_resume directly in test_embedding_recipes.py, added permitted_actions note and test coverage for EDIT/ESCALATE -> PASS

**Goal:** the "REST endpoint" story, correctly scoped: **chowki serves nothing** —
users add a route to the app they already run and call `resume`/`aresume` in the
handler. The resume token is the authorization (scope-bound, single-use, HMAC-signed),
so the recipe is small; this task makes it copy-paste.

**Files:** new `docs/user-guide/resuming-in-production.md`; new
`examples/python/fastapi_approvals.py`; new
`python/chowki/tests/integration/test_embedding_recipes.py`.

**Behavior (the guide must cover):**
1. Copy-paste handlers: FastAPI (async, `await chowki.aresume(...)`) and Flask
   (sync, `chowki.resume(...)`), each ~20 lines, taking `{run_id, token, decision,
   patch?, note?}` from the request body.
2. Exception → HTTP mapping table (normative for user apps):
   `InvalidResumeToken` → 401, `ExpiredResumeToken` → 410, `ReplayedNonceError` →
   409, `ChowkiStateError` (not paused / unknown run) → 404, `HumanRejectedError` →
   200 with `{"outcome": "rejected"}`, `WorkflowPaused` → 202 with the **new** token
   and step (the run hit another gate or was escalated), success → 200 with
   `{"outcome": "completed", "value": ...}`.
3. Long re-executions: a resume re-runs the workflow to the next boundary, which may
   take minutes — show the background-task variant (FastAPI `BackgroundTasks`;
   "return 202 immediately, poll `inspect_run`") next to the inline variant.
4. Security notes: the token is the credential — still authenticate the route as you
   would any admin surface; never log tokens; `reissue_token` for lost ones; where
   the token comes from (gateway notice / `WorkflowPaused.token` / CLI).
5. Where this is heading: the Phase 4 hosted REST gateway with signed callbacks, for
   teams that want chowki to own the endpoint.

**Tests first:** `test_embedding_recipes.py` exercises the *documented handler logic*
as plain functions (no web framework dependency — the recipe's core is
"payload dict in → status code + body out"): every row of the mapping table gets a
case, driven through a real paused run on `MemoryStorage`, sync and async variants.
The FastAPI example file is import-checked and its handler function unit-tested the
same way (FastAPI itself stays out of the dependency tree; the example guards the
import).

**Done-when:** guide + example committed; recipe tests green; mapping table also
linked from `docs/features.md` (new "Embedded approval endpoints" row → ✅).

---

## Task 6 — User guide

**Status:** COMPLETED
**Failed Verify Cycles:** 3
**Attempt Ledger:**
- attempt 1: implemented user guide pages -> FAIL (API signature mismatches in docs: pause reason positional vs kwarg, recover_runs engine param, db_path default, step identity per-name counter, args_hash collapse, ConsoleGateway & retry matrix details)
- attempt 2: corrected pause(reason=...), recover_runs(engine), step identity per-name counter, args_hash collapse, db_path CWD relative default, ConsoleGateway section, CLI -m/--db examples, retry matrix section -> FAIL (get_engine import path, retry_max_seconds default value 30.0, rerun behavior, recover_runs non-terminal run behavior, tenant_id AAD detail, pause token HMAC statelessness detail)
- attempt 3 (escalated): closed the *class* of failure instead of the instances — test_user_guide.py now resolves every documented `chowki.<name>` / `from chowki... import` against the package and compares every quoted default against `ChowkiConfig`/`GuardrailConfig`; then fixed get_engine import, retry_max_seconds/full-jitter formula, rerun and recover_runs semantics, tenant_id (AAD, no query filtering), stateless HMAC tokens with consumed nonces, plus source-checked corrections to ABORTED/FAILED, loop tiers, budget ceiling and unserializable-result behavior -> FAIL (workflow parameters in examples need default values for resume fn(run_id=...), tenant_id AAD tampering explanation, auto-pause gateway=None behavior, duplicate line in plan)
- attempt 4 (escalated): machine-checked the two remaining *behavioral* claims instead of rewording them — test_user_guide.py rejects any documented `@chowki.workflow` with a parameter that has no default (resume calls `fn(run_id=run_id)`), and test_auto_pause.py pins that `gateway=None` still yields a PAUSED run with a token; then defaulted every documented workflow parameter, added the "resumable workflows take no required arguments" notes to warm-resume.md and hitl.md, corrected the ABORT/auto-pause-without-gateway text, rewrote tenant AAD tampering and per-tenant `db_path` isolation in configuration.md, and de-duplicated the Task 5/Task 6 ledgers -> PASS

**Goal:** the documentation a first-hour evaluator needs; currently none exists.
Every page ends with "what can go wrong" — honesty is positioning.

**Files:** new under `docs/user-guide/`: `index.md`, `concepts.md`,
`warm-resume.md`, `guardrails.md`, `hitl.md`, `configuration.md`, `limits.md`
(`resuming-in-production.md` arrives via Task 5). Modified: `README.md` (links).

**Content contract per page:**
- `concepts.md`: runs, steps, state, snapshots (base/delta), the engine; a
  10-line mental model diagram in text.
- `warm-resume.md`: re-execution from the top, memoisation, **the R4 rule** (every
  side effect in a step — the single most important thing to teach), step identity
  and the rename/reorder hazard, crash recovery (`recover_runs` → `rerun`), the
  failed-step retry matrix and `release_step`/`complete_step`.
- `guardrails.md`: the default table, loop tiers with `record_text`/
  `record_transition`, budgets with **provider recipes** — 15-line snippets mapping
  an OpenAI and an Anthropic response's usage block into `chowki.report_usage`
  (recipes only; no SDK dependency) — auto-pause behavior and the resume-after-raise
  flow.
- `hitl.md`: pause gates, tokens (single-use, TTL, reissue), decisions incl. EDIT
  patches and ESCALATE, the audit trail, console gateway, CLI walkthrough.
- `configuration.md`: `ChowkiConfig` field-by-field, `resume_secret` in production,
  encryption setup (`CHOWKI_MASTER_KEY`), storage paths, tenant id.
- `limits.md`: single-process/single-writer-per-run (no `gather` over steps), SQLite
  boundary and when Phase 5 matters, redaction's non-UTF-8 exemption, `<TypeName>`
  args-hash collapse, what redaction does **not** guarantee (soften any
  "guarantees zero leaks" phrasing — defense in depth).

**Tests first:** a docs lint test (`tests/unit/test_user_guide.py`): every page
exists, every ```python block in the guide parses (`ast.parse`), every intra-repo
link resolves. Watch it fail on the empty directory, then write pages.

**Done-when:** lint test green; a newcomer can go install → quickstart → guide
without reading source.

---

## Task 7 — Flagship example: the showcase agent

**Status:** COMPLETED
**Failed Verify Cycles:** 2
**Attempt Ledger:**
- attempt 1: implemented agent_review.py, test_agent_example.py -> FAIL (--crash-after called os._exit(1) making recover/rerun demo block unreachable, workflow prompt parameter default needed for rerun memoisation)
- attempt 2: changed crash simulation to RuntimeError, set default prompt="Audit repo security", updated main() to catch crash and run recover + rerun -> FAIL (CHOWKI_CRASH_AFTER env var missing pop before rerun in first crash branch, README CLI recovery instructions needed sync with script flags)
- attempt 3 (escalated): popped CHOWKI_CRASH_AFTER in both crash branches before rerun; added `--no-auto-recover` so a simulated crash exits 1 and leaves the run stalled in RUNNING for the documented `chowki recover` + `chowki rerun` arc; rewrote the examples README to document both modes; added integration tests for the env-var trigger and for the operator CLI arc; updated get_llm_call_count docstring to "in current process" -> PASS

**Goal:** the launch demo as runnable code: an LLM tool-use agent with a budget
auto-pause, an approval gate before a dangerous tool, and the kill-mid-run → `rerun`
demo showing completed LLM calls are **not** re-executed (the "zero-waste" pitch).

**Files:** new `examples/python/agent_review.py`, new
`python/chowki/tests/integration/test_agent_example.py`; modified `README.md`
(demo section), `examples/python/README.md`.

**Behavior:**
1. Self-contained: a deterministic fake LLM callable (no API key needed) with a
   clearly marked 5-line swap-in for a real provider; tools: `search` (safe) and
   `send_email` (gated by `chowki.pause` with an EDIT-able draft).
2. Demo script (documented at the top of the file): run it → watch the budget
   soft-warning → approval gate pauses with a console notice → resume via CLI with
   an EDIT patch → `kill` it mid-run on the crash flag (`--crash-after N`) → `chowki
   recover` + `chowki rerun` → completed steps skipped (call counter printed).
3. README gains a "Showcase" section narrating exactly that flow (the launch-post /
   GIF script).

**Tests first:** integration test drives the whole arc in-process (no subprocess
except one CLI resume): pause → CLI resume EDIT → crash flag → rerun → assert the
fake-LLM call count did not grow on rerun.

**Done-when:** example runs green from a clean checkout with zero keys; README
section matches the code.

---

## Task 8 — Packaging & release engineering

**Status:** PENDING
**Failed Verify Cycles:** 2
**Attempt Ledger:**
- attempt 1: implemented CHANGELOG.md, pyproject.toml, release.yml, wheel_smoke_test.py, test_package_metadata.py -> FAIL (LICENSE file missing in sdist/wheel, ci.yml missing wheel_smoke_test job, pyproject.toml repo URL mismatch, features.md row claimed live PyPI release)
- attempt 2: added LICENSE file, force-included in sdist/wheel, added wheel-smoke job to ci.yml, updated project URLs to Git-Uzair/chowki -> FAIL (wheel force-include polluted site-packages root, README claimed pip install chowki from PyPI when unpublished, features.md claimed release workflow verified without tag)

**Goal:** `release.yml` has never fired; the version is running on hatch-vcs
fallback; there is no CHANGELOG. Make the release mechanics boringly verified.

**Files:** new `CHANGELOG.md`; modified `README.md` (badges, install, pitch),
`python/chowki/pyproject.toml` (URLs, keywords; verify classifiers),
`.github/workflows/release.yml` (verify trusted-publisher config against the repo);
new `python/chowki/tests/unit/test_package_metadata.py`.

**Behavior:**
1. `CHANGELOG.md` (Keep a Changelog format): everything under `0.1.0` — the Phase 1
   feature set, the hardening pass, and this phase's additions.
2. Wheel smoke test, automated: `uv build`, install the wheel into a scratch venv,
   run the quickstart and `chowki --version` against the installed package (script
   under `scripts/`, wired as a CI job and runnable locally).
3. `test_package_metadata.py`: version is not the fallback when a tag is present
   (skipped locally without tags), `py.typed` ships in the wheel, LICENSE in sdist,
   entry point resolves.
4. TestPyPI dry run through `release.yml` on a `v0.1.0rc1` tag; fix whatever breaks;
   document the release runbook in `CHANGELOG.md`'s footer or `docs/user-guide/`
   (maintainers section).
5. Tag `v0.1.0` is cut in Task 10, not here.

**Done-when:** rc lands on TestPyPI installable via
`uv add --index testpypi chowki`; smoke test green in CI.

---

## Task 9 — Ratify the durability decision (docs only)

**Status:** PENDING

**Goal:** close the roadmap's write-behind question with the decision: **synchronous
dispatch is the contract** — a snapshot is durable before the step returns; SIGKILL
loses nothing acknowledged. It is also launch messaging ("your state is on disk
before your step returns").

**Files:** `docs/research/07-cross-sdk-parity.md` §3 (explicit durability statement),
`docs/research/02-serialization.md` (amend the "async local buffer queue" budget
line), `docs/research/00-synthesis.md` (§5.1 table footnote),
`docs/plans/00-roadmap.md` (Phase 2 bullet → DECIDED), `docs/user-guide/limits.md`
(one line).

**Done-when:** amendments landed; `dispatch_ms` benchmark still green (the proof the
decision costs nothing).

---

## Task 10 — Launch close-out

**Status:** PENDING

**Goal:** ship it, verified and self-erasing.

**Steps:**
1. Full harness: unit + integration + benchmarks + pyright + mypy + ruff + layout +
   wheel smoke test.
2. `docs/features.md`: every Phase 2 row ✅; add rows discovered during execution.
3. Research amendments for anything learned (plans are disposable, amendments are
   not).
4. Tag `v0.1.0`; `release.yml` publishes to PyPI; GitHub release notes from the
   CHANGELOG.
5. `docs/plans/00-roadmap.md`: Phase 2 → DONE with the closing commit range; delete
   this plan file in the same commit.
6. Launch checklist (also the advertising copy skeleton): the kill-demo GIF from
   Task 7's script, the CLI approval flow, the redaction one-liner, the "zero
   infrastructure" positioning against Temporal/LangGraph, and the honest
   single-process boundary. Next showcase beat: Slack Socket Mode (Phase 4 opens
   with it).

**Done-when:** `pip install chowki` works from PyPI and the roadmap shows Phase 2
DONE.

---

## Risks

- **R1 — Registry is process-global state.** Test pollution; the `clear_registry()`
  autouse fixture is mandatory from Task 1.
- **R2 — `resume`/`aresume` refactor touches the most intricate control flow in the
  codebase** (decision handling + seeding). The existing resume/auto-pause suites are
  the safety net: they must stay green untouched; `_decide` is a pure extraction, not
  a redesign.
- **R3 — CLI subprocess tests on Windows.** argv lists, `sys.executable`, `pathlib`
  only; no shell strings.
- **R4 — Docs drift.** The docs lint test (Task 6) and recipe tests (Task 5) exist so
  the guide breaks CI when the API moves, instead of rotting.
- **R5 — Release workflow surprises.** That is what the TestPyPI rc is for; never
  debug publishing on the real `v0.1.0` tag.

PLAN COMPLETE
