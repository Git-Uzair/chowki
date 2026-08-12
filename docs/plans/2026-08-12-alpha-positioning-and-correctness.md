# chowki — Alpha Positioning & Correctness Plan

**File:** `docs/plans/2026-08-12-alpha-positioning-and-correctness.md`
**Date:** 2026-08-12
**Source:** `POSITIONING.md` (repo root, committed as `0a98650 Findings document`)
**Scope:** the "Before telling anyone about it" and "Metadata, one sitting" items of
`POSITIONING.md:576-599`, plus the argument-persistence item pulled forward from
"Next release".

---

## Context

- `chowki` is a polyglot monorepo; the Python SDK is the only implementation today
  (`python/chowki/src/chowki/`, uv workspace, `pyproject.toml` at repo root declares the
  workspace and every tool config).
- Verified commands (from `AGENTS.md` §2 and `scripts/ci_local.py:13-27`, both read this
  session):
  - install: `uv sync --all-extras --dev`
  - unit: `uv run pytest python/chowki/tests/unit -q`
  - integration: `uv run pytest python/chowki/tests/integration -q`
  - benchmarks: `uv run pytest python/chowki/tests/benchmarks --benchmark-only -q`
  - lint: `uv run ruff check .` · format: `uv run ruff format --check .`
  - types: `uv run pyright` and `uv run mypy python/chowki/src`
  - layout guard: `uv run python scripts/check_layout.py`
  - everything CI runs, in order: `uv run python scripts/ci_local.py`
- Binding repo rules that shape every task below (`AGENTS.md`): the banned product term
  (`check` immediately followed by `point`, written below as **`<BANNED-TERM>`**) is
  forbidden repo-wide and enforced by `scripts/check_layout.py:98,167`; `pickle`/`eval`
  are forbidden; serialization is `msgspec` MessagePack; any change under
  `chowki/state/` or `chowki/core/` requires a benchmark run against
  `python/chowki/tests/benchmarks/budgets.py`; failing test first; `docs/features.md`
  rows are updated in the same commit as the feature; wire-format changes additionally
  update `docs/research/07-cross-sdk-parity.md` and `spec/v1/`.
- **This plan file must never contain the banned term itself** — `docs/plans/` is not
  allowlisted by Task 1. Wherever a task asks you to write it, it says `<BANNED-TERM>`.
- Verified environment facts: `msgspec` is **0.21.1**;
  `msgspec.to_builtins(value, str_keys=True)` returns `{'a': 1, 'b': 'z'}` for a
  `msgspec.Struct`, `{'x': 2}` for a dataclass, `'YWJj'` for `b"abc"`,
  `'2026-01-01T00:00:00'` for a `datetime`, `'0000...0005'` for a `UUID`, `'1.5'` for a
  `Decimal`, and raises `TypeError("Encoding objects of type WindowsPath is
  unsupported")` for `pathlib.Path` and plain `object()`. (Probed this session with
  `uv run python`.)
- Working tree was clean at `0a98650` when this plan was written.

## Assumptions

- "Update repository documentation/badges/topics alignment" cannot be done from the repo
  for GitHub's About box and topics (they are GitHub settings, not files). Task 4 records
  the exact strings in `docs/user-guide/launch-checklist.md` as a maintainer action list;
  no task claims GitHub state was changed.
- `docs/comparison.md` is allowlisted (Task 1) but **not written** by this plan —
  `POSITIONING.md:597` files it under "Next release" and the request lists only the
  allowlist entry. Allowlisting a path that does not yet exist is harmless.
- Publishing v0.1.0 to PyPI (`POSITIONING.md:583`) is a maintainer action requiring
  credentials; out of scope, mentioned in Task 8's close-out note only.
- The `args_hash` fix changes hashes for complex arguments, so runs in flight across the
  upgrade lose memoisation for those steps. Chosen deliberately: re-execution with a
  logged warning beats today's silent wrong-result replay. Flagged in CHANGELOG and Risks.
- Workflow arguments are persisted **after redaction**, consistent with the always-on
  redaction rule. A secret passed as a workflow argument therefore replays as its
  placeholder; the first execution logs a warning saying so. Not persisting redacted, and
  not persisting at all, are both worse.
- The concurrency guard detects concurrent step entry *within one run context* (asyncio
  tasks and `asyncio.to_thread`, which copy the context var). A bare `threading.Thread`
  does not inherit the context, so `in_run()` is false there and steps pass through
  unmanaged — unchanged behaviour, documented, not fixed here.
- `chowki.rerun()` refusing a `RUNNING` run and `recover_runs` liveness leasing
  (`POSITIONING.md:234-243`) are **out of scope** — not in the request.

## Task status legend

`**Status:** PENDING` → flip to `COMPLETED` as each task lands. One commit per task.

---

## Task 1 — Allowlist the competitor vocabulary in positioning surfaces

**Status:** COMPLETED
**Failed Verify Cycles:** 2
**Attempt Ledger:**
- attempt 1: allowlist in check_layout.py + unit tests + drive-by reformat of POSITIONING.md -> VERDICT FAIL (SCOPE audit line: drive-by reformat of POSITIONING.md)
- attempt 2: revert POSITIONING.md -> VERDICT FAIL (ruff format --check fails on pre-existing unformatted POSITIONING.md from base commit)
- attempt 3 (ESCALATED to @opus-coder): format POSITIONING.md in a separate pre-fix commit, keep Task 1 clean -> PASSED
**Difficulty:** EASY (ESCALATED)
**Goal:** `scripts/check_layout.py` keeps banning the product term everywhere except the
four documents that must speak the reader's vocabulary.

**Files**

- `scripts/check_layout.py` (modify)
- `python/chowki/tests/unit/test_check_layout.py` (modify)

**Test first** — add to `python/chowki/tests/unit/test_check_layout.py`, mirroring the
existing scaffold pattern at lines 64-78 (`monkeypatch.setattr(check_layout, "ROOT",
tmp_path)`, create every `REQUIRED_DIRS` / `REQUIRED_FILES` entry, then write the
offending file):

```python
def test_check_layout_allows_the_banned_term_in_positioning_surfaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The four documents written in the reader's vocabulary may use the term."""
    monkeypatch.setattr(check_layout, "ROOT", tmp_path)
    for d in check_layout.REQUIRED_DIRS:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    for f in check_layout.REQUIRED_FILES:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("content\n", encoding="utf-8")

    banned = "check" + "point"
    for rel in check_layout.BANNED_WORD_ALLOWLIST:
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"a {banned} is what LangGraph calls it\n", encoding="utf-8")

    assert check_layout.main() == 0


def test_check_layout_still_bans_the_term_outside_the_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Allowlisting the READMEs must not weaken the rule anywhere else."""
    monkeypatch.setattr(check_layout, "ROOT", tmp_path)
    for d in check_layout.REQUIRED_DIRS:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    for f in check_layout.REQUIRED_FILES:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("content\n", encoding="utf-8")

    banned = "check" + "point"
    (tmp_path / "docs" / "user-guide").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "user-guide" / "concepts.md").write_text(
        f"our {banned} mechanism\n", encoding="utf-8"
    )

    assert check_layout.main() == 1
```

Confirm the first test fails with `AttributeError: module 'scripts.check_layout' has no
attribute 'BANNED_WORD_ALLOWLIST'` before touching the script.

**Change**

1. In `scripts/check_layout.py`, directly under `BANNED_WORD` (line 98), add:

```python
#: Documents that may spell the competitor's word for the concept, because they are
#: written in the reader's vocabulary rather than ours (POSITIONING.md, Finding 0). The
#: ban still means "never use their word for our own mechanism" everywhere else.
BANNED_WORD_ALLOWLIST = frozenset(
    {
        Path("README.md"),
        Path("python/chowki/README.md"),
        Path("docs/comparison.md"),
        Path("POSITIONING.md"),
    }
)
```

   `Path("python/chowki/README.md")` compares equal to the `WindowsPath` produced by
   `path.relative_to(ROOT)` on Windows, because `Path` normalises separators at
   construction — no `as_posix()` juggling needed.

2. At `scripts/check_layout.py:167`, change the content check to:

```python
        if BANNED_WORD in text.lower() and rel_path not in BANNED_WORD_ALLOWLIST:
            failures.append(f"banned product term in {rel_path}")
```

   Leave the **path-name** check (line 153) untouched: no allowlisted file has the term
   in its own name, and a directory named after it stays a failure.

**Done when**

- `uv run pytest python/chowki/tests/unit/test_check_layout.py -q` passes (11 tests, two
  of them new).
- `uv run python scripts/check_layout.py` prints `layout OK`.
- `uv run ruff check . && uv run ruff format --check . && uv run pyright` clean.

---

## Task 2 — The wedge sentence and the positioning rewrite in both READMEs

**Status:** COMPLETED
**Failed Verify Cycles:** 1
**Attempt Ledger:**
- attempt 1: add wedge sentence and differentiators -> VERDICT FAIL (plan specified bold blockquote `> **...**`, but unbolded single-line quote was inserted without bold tags)
- attempt 2: update blockquotes to bold `> **...**` in both READMEs -> VERDICT PASS
**Difficulty:** EASY
**Depends on:** Task 1 (the term below cannot be written before the allowlist exists).
**Goal:** both READMEs open with the sentence that explains why chowki exists next to a
state saver, and name the four load-bearing differentiators on the first screen.

**Files**

- `README.md` (modify)
- `python/chowki/README.md` (modify)
- `python/chowki/tests/unit/test_positioning_copy.py` (new)

**Test first** — new file `python/chowki/tests/unit/test_positioning_copy.py`:

```python
"""The first screen of both READMEs must carry the positioning wedge.

POSITIONING.md:503-512: this is the one sentence that answers the question LangChain's
own documentation has already asked the reader, and it has to survive future edits.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
READMES = (ROOT / "README.md", ROOT / "python" / "chowki" / "README.md")

WEDGE = (
    "State savers restore *state*. chowki memoizes *step results* — so on resume, "
    "the LLM calls and API requests that already succeeded do not happen again."
)


@pytest.mark.parametrize("readme", READMES, ids=lambda p: str(p.name))
def test_readme_carries_the_wedge_sentence(readme: Path) -> None:
    text = readme.read_text(encoding="utf-8")
    normalised = " ".join(text.split())
    assert " ".join(WEDGE.split()) in normalised, f"{readme} lost the wedge sentence"


@pytest.mark.parametrize("readme", READMES, ids=lambda p: str(p.name))
def test_readme_speaks_the_readers_vocabulary(readme: Path) -> None:
    """The term the reader searches with must appear, or the page cannot be found."""
    term = "check" + "point"
    assert term in readme.read_text(encoding="utf-8").lower()


@pytest.mark.parametrize("readme", READMES, ids=lambda p: str(p.name))
def test_readme_names_the_four_differentiators(readme: Path) -> None:
    lowered = readme.read_text(encoding="utf-8").lower()
    for claim in ("determinism tax", "hmac", "redaction", "budget"):
        assert claim in lowered, f"{readme} no longer mentions {claim!r}"
```

Both files fail the first two tests before the edits.

**Change** — `README.md`:

1. Insert, immediately after the badge block (currently ends at line 16) and before the
   `` `chowki` (Urdu ...) `` paragraph, exactly:

```markdown
> **State savers restore *state*. chowki memoizes *step results* — so on resume, the LLM
> calls and API requests that already succeeded do not happen again.**
```

2. Immediately after that blockquote, add the four-claim block. Write the banned term
   where this plan says `<BANNED-TERM>` — as one lowercase word, no space, no hyphen:

```markdown
Coming from LangGraph? Its `SqliteSaver` restores the graph state at a <BANNED-TERM>, and
its own docs say the nodes after it re-execute — "including any LLM calls, API requests,
and interrupts". chowki records the *result* of every `@chowki.step`, so those calls are
served from the record instead of re-issued.

- **No determinism tax** — no replay-safe workflow body, no journal-mismatch errors. Your
  code may call `random`, `time.time()`, or an API directly; non-determinism is isolated
  at the step boundary.
- **Approval gates with cryptographic provenance** — one single-use HMAC-SHA256 token per
  run and gate, four decisions (`APPROVE` / `REJECT` / `EDIT` as an RFC 6902 patch /
  `ESCALATE`), and an append-only audit log no adapter is permitted to delete from.
- **Secret redaction that cannot be switched off** — key names, a combined
  provider-credential regex, and Shannon entropy over step arguments, state, and logs
  *before* anything is persisted.
- **Agent cost budgets** — token and spend ceilings with an 80% soft warning re-seeded
  from persisted usage on resume, three tiers of loop detection, and `max_steps_per_run`.
```

3. In the same file, leave the existing tagline, ASCII art, badges, quickstart, and every
   other section untouched. No other edit.

**Change** — `python/chowki/README.md`:

1. Insert the identical wedge blockquote immediately after the badge block (ends at line
   17) and before the `` `chowki` (Urdu ...) `` paragraph.
2. Insert the same "Coming from LangGraph?" paragraph and the same four-bullet block
   immediately after the wedge, before the existing `## Why` table. Keep the table.
3. Nothing else changes; the PyPI page renders this file, so the four bullets have to be
   above the fold.

**Done when**

- `uv run pytest python/chowki/tests/unit/test_positioning_copy.py -q` passes (9 tests).
- `uv run python scripts/check_layout.py` prints `layout OK` — this is the proof Task 1's
  allowlist is doing its job.
- `uv run pytest python/chowki/tests/unit -q` still green (nothing else asserts README
  content; `test_user_guide.py` only reads `docs/user-guide/`).

---

## Task 3 — PyPI metadata: description, keywords, classifiers

**Status:** COMPLETED
**Difficulty:** EASY
**Goal:** the fields PyPI actually indexes describe a durable-execution library for LLM
agents instead of terms nobody searches.

**Files**

- `python/chowki/pyproject.toml` (modify lines 3, 9-21)
- `python/chowki/tests/unit/test_package_metadata.py` (modify)

**Test first** — extend `test_package_classifiers_and_metadata`
(`python/chowki/tests/unit/test_package_metadata.py:65-77`) and add one new test beside
it:

```python
def test_package_carries_ai_and_recovery_classifiers() -> None:
    """PyPI classifiers are faceted-search links; without these the package has no AI signal."""
    try:
        meta = importlib.metadata.metadata("chowki")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("chowki package metadata not found in current environment")

    classifiers = meta.get_all("Classifier") or []
    for required in (
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
        "Topic :: System :: Recovery Tools",
        "Topic :: System :: Distributed Computing",
        "Topic :: Security :: Cryptography",
        "Topic :: System :: Monitoring",
        "Topic :: Database",
        "Intended Audience :: System Administrators",
    ):
        assert required in classifiers, f"missing classifier: {required}"


def test_package_summary_and_keywords_are_searchable() -> None:
    """`description` is the highest-weighted free text on a PyPI project page."""
    try:
        meta = importlib.metadata.metadata("chowki")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("chowki package metadata not found in current environment")

    summary = meta["Summary"].lower()
    for term in ("durable execution", "llm agents", "memoization", "sqlite"):
        assert term in summary, f"PyPI summary no longer mentions {term!r}"

    keywords = {k.strip() for k in (meta["Keywords"] or "").split(",")}
    for term in ("durable-execution", "crash-recovery", "memoization", "human-in-the-loop"):
        assert term in keywords, f"PyPI keywords no longer contain {term!r}"
```

`uv run` reinstalls the editable package before each test session (observed this
session), so the metadata under test is the metadata in `pyproject.toml`.

**Change** — `python/chowki/pyproject.toml`:

1. Replace line 3 with:

```toml
description = "Durable execution for LLM agents: crash recovery, step memoization, human-in-the-loop approval gates, secret redaction, and token budgets. No server, no worker — just SQLite."
```

2. Add to the `classifiers` list (keep every existing entry, keep the list
   alphabetically sorted the way it already is):

```toml
    "Intended Audience :: System Administrators",
    "Topic :: Database",
    "Topic :: Scientific/Engineering :: Artificial Intelligence",
    "Topic :: Security :: Cryptography",
    "Topic :: System :: Distributed Computing",
    "Topic :: System :: Monitoring",
    "Topic :: System :: Recovery Tools",
```

3. Replace the `keywords` line (21) with the list from `POSITIONING.md:445-451`, split
   over several lines so it stays under the 100-column ruff limit is *not* required
   (`.toml` is not linted by ruff, but keep it readable):

```toml
keywords = [
    "durable-execution", "crash-recovery", "resume", "replay", "idempotency",
    "memoization", "exactly-once", "human-in-the-loop", "hitl", "approval-workflow",
    "audit-log", "guardrails", "circuit-breaker", "loop-detection", "token-budget",
    "cost-control", "secret-redaction", "llm", "agents", "ai-agents", "llmops",
    "langchain", "langgraph", "sqlite",
]
```

   **Deliberate omission:** `POSITIONING.md:454` wants the banned term added as a keyword
   too. `python/chowki/pyproject.toml` is **not** in Task 1's allowlist, so adding it
   there fails `scripts/check_layout.py`. Leave it out; if the maintainer wants it, the
   one-line change is adding `Path("python/chowki/pyproject.toml")` to
   `BANNED_WORD_ALLOWLIST`. Record this in the commit message, not in a code comment.

4. Touch nothing else in the file — `[tool.hatch.version]` and its `raw-options` comment
   (lines 50-57) are load-bearing release machinery.

**Done when**

- `uv build --package chowki` exits 0 from the repo root. This is the verification step
  for classifier strings: an invalid Trove classifier fails the build.
- `uv run pytest python/chowki/tests/unit/test_package_metadata.py -q` passes.
- `uv run python scripts/check_layout.py` prints `layout OK`.

---

## Task 4 — Record the GitHub About box, topics, and naming rule

**Status:** COMPLETED
**Difficulty:** EASY
**Independent of Tasks 1-3 and 5-7 except for the shared summary string in Task 3 — safe
to parallelise with Task 5.**
**Goal:** the repo-side strings a maintainer must paste into GitHub settings live in the
tree, reviewed and diffable, instead of in a research document that will be deleted.

**Files**

- `docs/user-guide/launch-checklist.md` (modify)
- `python/chowki/tests/unit/test_user_guide.py` (modify `test_launch_checklist_content`)

**Test first** — extend `test_launch_checklist_content`
(`python/chowki/tests/unit/test_user_guide.py:267-274`) with:

```python
    assert "## Repository Metadata (maintainer actions, GitHub settings)" in content
    assert "Durable execution for LLM agents" in content
    for topic in ("durable-execution", "human-in-the-loop", "langgraph", "llmops"):
        assert topic in content
```

**Change** — append to `docs/user-guide/launch-checklist.md`, before the final
`## Pre-Launch Checklist` section (so the checklist stays last):

```markdown
---

## Repository Metadata (maintainer actions, GitHub settings)

These are GitHub repository settings, not files. They are recorded here so the strings
are reviewed and versioned; applying them is a manual maintainer step.

**About box** (indexed, and what renders in GitHub search results):

> Durable execution for LLM agents — crash recovery, step memoization, human approval
> gates, secret redaction, and token budgets. Decorators, not infrastructure. No server,
> SQLite-backed.

**Topics** (GitHub caps at 20):

`durable-execution` `crash-recovery` `idempotency` `exactly-once` `human-in-the-loop`
`approval-workflow` `audit-log` `guardrails` `circuit-breaker` `llm` `ai-agents`
`agents` `llmops` `langchain` `langgraph` `crewai` `sqlite` `python`
`workflow-engine` `observability`

**Naming rule.** "chowki" collides with furniture, South Asian place names, and *police
chowki*, so the bare name will not rank. Never ship it alone: use
`chowki — durable execution for LLM agents` in the About box, the PyPI summary, the
README masthead area, the social card, and any launch post title. The descriptor phrase
does the ranking and the disambiguation.

**Alignment invariant.** The About box, the PyPI `description` in
`python/chowki/pyproject.toml`, and the first screen of both READMEs must say the same
thing. Changing one without the others is the drift this section exists to prevent.
```

**Done when**

- `uv run pytest python/chowki/tests/unit/test_user_guide.py -q` passes.
- `uv run python scripts/check_layout.py` prints `layout OK` (this file is **not**
  allowlisted — it must not contain the banned term; the text above does not).

---

## Task 5 — Structural argument hashing (the `<TypeName>` collapse is a correctness bug)

**Status:** COMPLETED
**Failed Verify Cycles:** 1
**Attempt Ledger:**
- attempt 1: implement `_expand` using `to_builtins` -> `model_dump` -> `__dict__` -> VERDICT FAIL (DISCREPANCY 1: set/frozenset inside Struct/dataclass converted by `to_builtins` to list with process-dependent iteration order; DISCREPANCY 2: `getattr(val, "model_dump", None)` raises non-AttributeError in `__getattr__`)
- attempt 2: unpack Structs/dataclasses field by field (`structs.asdict` / `dataclasses.fields`) *before* `to_builtins`, so a set field reaches `_sanitize` as a set and is put in its total order; keep `to_builtins` ahead of `model_dump`/`__dict__` (an enum member's `__dict__` is enum machinery, not its value) and read both attribute probes under `except Exception` -> COMPLETED
**Difficulty:** HARD
**Goal:** two different instances of the same complex class produce different
`args_hash` values, so a step never replays another call's memoised result; anything
still unexpandable is reported loudly instead of collapsing in silence.

**Files**

- `python/chowki/src/chowki/core/decorators.py` (modify `_signature` at lines 43-87 and
  `_begin` at lines 90-108)
- `python/chowki/tests/unit/test_step_decorator.py` (modify — add tests)
- `docs/user-guide/limits.md` (modify §4, lines 33-39, and "What Can Go Wrong" item 2)
- `docs/features.md` (modify the "Args-hash" row, line 57)
- `docs/research/07-cross-sdk-parity.md` (modify §4, lines 90-99)

**Test first** — append to `python/chowki/tests/unit/test_step_decorator.py` (it already
imports `pytest`, `RunContext`, `run_scope`, `step`, and defines the `ctx` fixture at
lines 108-110; add `import dataclasses`, `import msgspec` and
`from structlog.testing import capture_logs` at the top):

```python
class _Invoice(msgspec.Struct):
    invoice_id: str
    amount: int


@dataclasses.dataclass
class _Order:
    order_id: str


class _FakeModel:
    """Stands in for a Pydantic model without adding the dependency: chowki reaches for
    ``model_dump`` by duck typing, exactly as it would on a real BaseModel."""

    def __init__(self, ref: str) -> None:
        self.ref = ref

    def model_dump(self) -> dict[str, object]:
        return {"ref": self.ref}


class _Plain:
    def __init__(self, tag: str) -> None:
        self.tag = tag


def _hash_of(ctx: RunContext, step_id: str) -> str:
    record = ctx.engine.storage.get_step(ctx.run_id, step_id)
    assert record is not None
    return record.args_hash


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (_Invoice(invoice_id="inv-1", amount=1), _Invoice(invoice_id="inv-999", amount=1)),
        (_Order(order_id="o-1"), _Order(order_id="o-2")),
        (_FakeModel("a"), _FakeModel("b")),
        (_Plain("a"), _Plain("b")),
    ],
    ids=["struct", "dataclass", "pydantic-like", "plain-object"],
)
def test_different_instances_of_one_class_hash_differently(
    ctx: RunContext, first: object, second: object
) -> None:
    """The Phase 1 collapse made these identical, so the second call replayed the first's
    memoised result -- a wrong answer, not a slow one (POSITIONING.md:223-232)."""
    seen: list[object] = []

    @step
    def handle(payload: object) -> str:
        seen.append(payload)
        return "done"

    with run_scope(ctx):
        handle(first)
        handle(second)

    assert len(seen) == 2
    assert _hash_of(ctx, "handle#0") != _hash_of(ctx, "handle#1")


def test_equal_complex_arguments_still_memoise(ctx: RunContext) -> None:
    """Structural hashing must not break the memoisation it exists to make trustworthy."""
    calls: list[int] = []

    @step
    def handle(payload: _Invoice) -> str:
        calls.append(1)
        return "done"

    with run_scope(ctx):
        handle(_Invoice(invoice_id="inv-1", amount=10))

    replay = RunContext(run_id=ctx.run_id, workflow=ctx.workflow, engine=ctx.engine)
    with run_scope(replay):
        assert handle(_Invoice(invoice_id="inv-1", amount=10)) == "done"

    assert calls == [1]


def test_a_struct_does_not_collide_with_an_equal_dict(ctx: RunContext) -> None:
    @step
    def handle(payload: object) -> str:
        return "done"

    with run_scope(ctx):
        handle(_Invoice(invoice_id="inv-1", amount=10))
        handle({"invoice_id": "inv-1", "amount": 10})

    assert _hash_of(ctx, "handle#0") != _hash_of(ctx, "handle#1")


def test_an_unexpandable_argument_warns_instead_of_collapsing_silently(
    ctx: RunContext,
) -> None:
    """`object()` has no structure to hash. It still collapses -- loudly."""

    @step
    def handle(payload: object) -> str:
        return "done"

    with capture_logs() as logs, run_scope(ctx):
        handle(object())

    events = [entry for entry in logs if entry["event"] == "chowki_step_args_opaque"]
    assert events, "an opaque argument type must be reported"
    assert events[0]["types"] == ["object"]
    assert events[0]["step_id"] == "handle#0"
```

Confirm the parametrised test fails for all four ids (the two hashes are equal today) and
the warning test fails with an empty `events` list, before changing `decorators.py`.

**Change** — `python/chowki/src/chowki/core/decorators.py`:

1. Add `import contextlib` and `import msgspec` to the import block (lines 1-33), keeping
   ruff-isort order: stdlib block gets `contextlib`; `msgspec` joins the third-party
   block next to `structlog`.

2. Add, next to the other module constants (lines 35-37):

```python
_OPAQUE: Final = object()
```

3. Add a module-level helper directly above `_signature`:

```python
def _expand(val: object) -> Any:
    """Return a JSON-shaped expansion of a complex value, or ``_OPAQUE``.

    ``to_builtins`` covers msgspec Structs, dataclasses, attrs classes, enums, bytes
    (base64), datetimes, UUIDs and Decimals in one C-accelerated call; ``model_dump``
    covers Pydantic without importing it; ``__dict__`` covers ordinary classes. Only a
    value none of those describes falls back to a type marker, and ``_begin`` warns when
    that happens: the silent version of that fallback is what let two different instances
    of one class share an args_hash and replay each other's memoised result.
    """
    with contextlib.suppress(TypeError, ValueError, RecursionError):
        return msgspec.to_builtins(val, str_keys=True)
    dump = getattr(val, "model_dump", None)
    if callable(dump):
        with contextlib.suppress(Exception):
            return dump()
    attrs = getattr(val, "__dict__", None)
    if isinstance(attrs, dict) and attrs:
        return dict(cast("dict[str, Any]", attrs))
    return _OPAQUE
```

4. Change `_signature`'s signature to
   `def _signature(name: str, args: tuple[Any, ...], kwargs: dict[str, Any], opaque: list[str]) -> dict[str, Any]:`
   and replace its final fallback (line 80, `return f"<{type(val).__name__}>"`) with:

```python
        expanded = _expand(val)
        type_name = type(val).__name__
        if expanded is _OPAQUE:
            opaque.append(type_name)
            return f"<{type_name}>"
        # Keyed by the type name so a Struct never hashes identically to an equal plain
        # dict, and re-sanitized because `model_dump`/`__dict__` can return anything.
        return {f"<{type_name}>": _sanitize(expanded, inner)}
```

   Extend the existing docstring (lines 44-57) with one paragraph: complex values are
   expanded structurally; only values with no structure at all collapse to a marker, and
   the caller is told which types those were.

5. In `_begin`, replace lines 107-108 with:

```python
    opaque_types: list[str] = []
    sig = _signature(name, args, kwargs, opaque_types)
    args_hash = content_hash(sig)
    if opaque_types:
        # A collapsed type means two different instances share this hash, so a memoised
        # result can replay for logically different arguments. Naming the type is what
        # lets the caller fix it by passing something with structure.
        structlog.get_logger().warning(
            "chowki_step_args_opaque",
            step_id=step_id,
            run_id=ctx.run_id,
            types=sorted(set(opaque_types)),
        )
```

   Leave every other line of `_begin` alone — the memoisation, claim, and recovery logic
   is unchanged; only the hash input is richer.

**Docs in the same commit** (AGENTS.md §8):

- `docs/user-guide/limits.md` §4: retitle to `## 4. Argument Hashing of Complex Objects`
  and rewrite the body: Structs, dataclasses, enums, bytes, datetimes, UUIDs, Decimals,
  Pydantic models (via `model_dump`) and ordinary objects (via `__dict__`) are expanded
  structurally and hashed by value; only a value with no exposable structure (a C
  extension object, an open socket) collapses to `<TypeName>` and logs
  `chowki_step_args_opaque`; a collapsed argument still risks a cache collision, so pass
  something with structure. Update "What Can Go Wrong" item 2 to match.
- `docs/features.md` "Args-hash" row (line 57): replace `` `<TypeName>` fallback (collapse
  caveat) `` with `structural expansion of Structs/dataclasses/models/objects,
  `<TypeName>` marker + warning only for values with no structure`.
- `docs/research/07-cross-sdk-parity.md` §4 (lines 90-99): rewrite the `S` sanitizer rule
  to include the expansion order (`to_builtins` → `model_dump` → `__dict__` → marker) and
  the `{"<TypeName>": expansion}` wrapper, and replace the "accepted Phase 1 behavior"
  sentence in the collapse caveat with the new normative behaviour, noting the Node SDK
  must reproduce the same wrapper shape and the same expansion order.

**Done when**

- `uv run pytest python/chowki/tests/unit/test_step_decorator.py -q` passes, including
  `test_set_arguments_hash_identically_in_a_fresh_process` (line 366) — proof the hash is
  still stable across processes.
- `uv run pytest python/chowki/tests/unit python/chowki/tests/integration -q` green.
- `uv run pytest python/chowki/tests/benchmarks --benchmark-only -q` passes — mandatory,
  this is a `chowki/core/` change (AGENTS.md §5). `step_decorator_overhead_us` (50 µs,
  ×1.5 tolerance) is the binding gate; `_expand` is only reached for non-primitive,
  non-container arguments, so the benchmark's `int` argument path is untouched.
- `uv run pyright && uv run mypy python/chowki/src` clean.

---

## Task 6 — Persist the original workflow arguments and replay them on resume

**Status:** PENDING
**Difficulty:** HARD
**Depends on:** Task 5 (both touch step/arg semantics; landing 5 first keeps the
memoisation assertions in this task's tests meaningful).
**Goal:** `resume()` / `aresume()` / `rerun()` re-invoke a workflow with the arguments the
run was *started* with, so a required parameter is resumable and a default parameter can
no longer silently swap the entity the human approved
(`POSITIONING.md:293-414` — the wrong-invoice scenario).

**Files**

- `python/chowki/src/chowki/types.py` (modify `RunRecord`, ends line 152)
- `python/chowki/src/chowki/core/runner.py` (modify `_open_run` lines 33-117 and both
  wrappers, lines 216-273)
- `python/chowki/src/chowki/core/resume.py` (modify `_invoke_workflow` line 88-89, its
  three call sites at lines 304, 361-363, 386)
- `python/chowki/tests/unit/test_workflow_arguments.py` (new)
- `python/chowki/tests/unit/test_user_guide.py` (modify one docstring, lines 168-174)
- `docs/user-guide/warm-resume.md` (rewrite §"Resumable Workflows Take No Required
  Arguments", lines 16-20)
- `docs/user-guide/hitl.md` (rewrite §"Every Parameter of a Pausing Workflow Needs a
  Default", lines 48-52)
- `docs/features.md` (Workflow runner row, line 64)
- `docs/research/07-cross-sdk-parity.md` (§9 workflow runner + §11 adapter contract)

**Test first** — new file `python/chowki/tests/unit/test_workflow_arguments.py`:

```python
"""Workflow arguments are part of the run record, so a resume replays the real call.

Before this, `resume()` called `workflow_fn(run_id=run_id)` and nothing else: a required
parameter made the run unresumable, and a defaulted parameter silently bound the default
on resume, missing every args_hash and re-running steps against the wrong entity
(POSITIONING.md:293-414).
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from structlog.testing import capture_logs

import chowki
from chowki.config import ChowkiEngine
from chowki.errors import ChowkiStateError
from chowki.state.codec import decode_state
from chowki.types import RunRecord, RunStatus


def _resume(engine: ChowkiEngine, run_id: str, token: str, fn: Any) -> Any:
    return chowki.resume(
        run_id=run_id,
        token=token,
        decision=chowki.Decision.APPROVE,
        workflow_fn=fn,
        engine=engine,
    )


def test_a_required_argument_survives_the_pause(engine: ChowkiEngine) -> None:
    fetched: list[str] = []

    @chowki.step
    def fetch_invoice(invoice_id: str) -> str:
        fetched.append(invoice_id)
        return invoice_id

    @chowki.workflow(engine=engine)
    def billing_agent(invoice_id: str) -> str:
        found = fetch_invoice(invoice_id)
        chowki.pause(reason="payment needs a human")
        return f"paid {found}"

    with pytest.raises(chowki.WorkflowPaused) as paused:
        billing_agent("inv-999", run_id="run-req")
    token = paused.value.token
    assert token is not None

    result = _resume(engine, "run-req", token, billing_agent)

    assert result.value == "paid inv-999"
    assert fetched == ["inv-999"], "the step must be memoised, not re-run with a default"


def test_a_default_no_longer_shadows_the_real_argument(engine: ChowkiEngine) -> None:
    """The documented workaround's silent-failure mode: resume used to bind 'inv-1'."""
    fetched: list[str] = []

    @chowki.step
    def fetch_invoice(invoice_id: str) -> str:
        fetched.append(invoice_id)
        return invoice_id

    @chowki.workflow(engine=engine)
    def billing_agent(invoice_id: str = "inv-1") -> str:
        found = fetch_invoice(invoice_id)
        chowki.pause(reason="payment needs a human")
        return f"paid {found}"

    with pytest.raises(chowki.WorkflowPaused) as paused:
        billing_agent(invoice_id="inv-999", run_id="run-default")
    token = paused.value.token
    assert token is not None

    result = _resume(engine, "run-default", token, billing_agent)

    assert result.value == "paid inv-999"
    assert fetched == ["inv-999"]


def test_positional_and_keyword_arguments_both_replay(engine: ChowkiEngine) -> None:
    seen: list[tuple[str, int]] = []

    @chowki.workflow(engine=engine)
    def mixed(name: str, count: int = 0) -> str:
        seen.append((name, count))
        return name

    mixed("alpha", count=3, run_id="run-mixed")
    chowki.rerun("run-mixed", engine=engine)

    assert seen == [("alpha", 3), ("alpha", 3)]


def test_arguments_are_redacted_before_they_are_persisted(engine: ChowkiEngine) -> None:
    @chowki.workflow(engine=engine)
    def with_secret(api_key: str = "") -> str:
        return "done"

    with capture_logs() as logs:
        with_secret(api_key="sk-" + "A1b2C3d4E5f6G7h8I9j0" * 2, run_id="run-secret")

    record = engine.storage.get_run("run-secret")
    assert record is not None and record.inputs is not None
    decoded = cast(dict[str, Any], decode_state(record.inputs))
    assert "[REDACTED:" in decoded["kwargs"]["api_key"]
    assert any(entry["event"] == "chowki_workflow_args_redacted" for entry in logs)


def test_unencodable_arguments_are_not_persisted_but_do_not_break_the_run(
    engine: ChowkiEngine,
) -> None:
    @chowki.workflow(engine=engine)
    def takes_anything(payload: object = None) -> str:
        return "done"

    with capture_logs() as logs:
        assert takes_anything(object(), run_id="run-unenc") == "done"

    record = engine.storage.get_run("run-unenc")
    assert record is not None and record.inputs is None
    assert any(entry["event"] == "chowki_workflow_args_not_persisted" for entry in logs)


def test_a_run_without_stored_arguments_fails_loudly_not_with_a_typeerror(
    engine: ChowkiEngine,
) -> None:
    """A run written by an older chowki has no inputs; say so instead of raising TypeError."""

    @chowki.workflow(engine=engine)
    def legacy(invoice_id: str) -> str:
        return invoice_id

    engine.storage.put_run(
        RunRecord(
            run_id="run-legacy",
            workflow="legacy",
            tenant_id="default",
            created_at_utc="2026-08-12T00:00:00Z",
            updated_at_utc="2026-08-12T00:00:00Z",
            status=RunStatus.PENDING,
        )
    )

    with pytest.raises(ChowkiStateError, match="argument"):
        chowki.rerun("run-legacy", engine=engine)


def test_the_first_call_owns_the_stored_arguments(engine: ChowkiEngine) -> None:
    """Re-invoking a run id is a warm resume, not a new call: the record must not move."""

    @chowki.workflow(engine=engine)
    def pipeline(tag: str = "first") -> str:
        return tag

    pipeline("first", run_id="run-own")
    pipeline("second", run_id="run-own")

    record = engine.storage.get_run("run-own")
    assert record is not None and record.inputs is not None
    decoded = cast(dict[str, Any], decode_state(record.inputs))
    assert decoded["args"] == ["first"]
```

Confirm the first two tests fail today (`TypeError: billing_agent() missing 1 required
positional argument` and `fetched == ["inv-999", "inv-1"]` respectively).

**Change**

1. `python/chowki/src/chowki/types.py` — append one field to `RunRecord`, **after**
   `usage` (the struct encodes as a msgpack map keyed by field name, so appending is
   backward-compatible; older records decode with the default):

```python
    #: MessagePack of ``{"args": [...], "kwargs": {...}}`` as the workflow was first
    #: called, redacted before it is persisted. ``None`` when the run predates this field,
    #: took no arguments, or passed something the codec cannot encode -- resume then falls
    #: back to the signature's defaults, exactly as it always did.
    inputs: bytes | None = None
```

2. `python/chowki/src/chowki/core/runner.py`:

   - Imports: add `from chowki.state.codec import encode_state` and extend the
     `chowki.types` import with `JSONValue`.
   - Add above `_open_run`:

```python
def _encode_inputs(
    engine: ChowkiEngine,
    workflow_name: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> bytes | None:
    """Redact and encode a workflow's call arguments, or return None and say why.

    Persisting these is what makes a resume replay the call that actually happened
    instead of the signature's defaults. Redaction runs first because everything chowki
    persists is redacted; that means a secret argument replays as its placeholder, which
    is worth a warning rather than a silent substitution. An argument the codec cannot
    encode leaves the run exactly where it was before this existed: resumable only if
    every parameter has a default.
    """
    if not args and not kwargs:
        return None
    logger = structlog.get_logger()
    payload: dict[str, Any] = {"args": list(args), "kwargs": dict(kwargs)}
    try:
        redacted = cast(JSONValue, engine.redactor.redact(payload))
        encoded = encode_state(redacted)
        changed = redacted != payload
    except Exception:
        logger.warning("chowki_workflow_args_not_persisted", workflow=workflow_name)
        return None
    if changed:
        logger.warning("chowki_workflow_args_redacted", workflow=workflow_name)
    return encoded
```

   - `_open_run` gains a keyword parameter `inputs: bytes | None = None` and sets it on
     the record **only when the record is created** (the `if existing is None:` branch at
     lines 46-54): `inputs=inputs`. The `else:` branch must not touch `record.inputs` —
     re-invoking a run id is a warm resume, and the first call's arguments are the run's
     arguments.
   - Both wrappers (`async_wrapper` line 217, `sync_wrapper` line 247) compute the inputs
     before opening the run and pass them through:

```python
eff_engine = engine or get_engine()
eff_tenant = tenant_id or dec_tenant_id
encoded_inputs = _encode_inputs(eff_engine, workflow_name, args, kwargs)
ctx, record = _open_run(eff_engine, workflow_name, run_id, eff_tenant, inputs=encoded_inputs)
```

     Nothing else in the wrappers changes; `_open_run` already calls `put_run(record)`
     before the body runs, so the arguments are durable before the first side effect.

3. `python/chowki/src/chowki/core/resume.py`:

   - Replace `_invoke_workflow` (lines 88-89) with:

```python
def _stored_call(
    workflow_fn: Callable[..., Any], run: RunRecord | None
) -> tuple[list[Any], dict[str, Any]]:
    """Return the arguments a re-invocation must pass, or explain why it cannot.

    Warm resume re-executes the body from line 1, so it needs the arguments of the
    original call. They live on the run record (`RunRecord.inputs`); a run written before
    that field existed, or whose arguments could not be encoded, has none -- and if the
    signature has a required parameter, the re-invocation would die with a bare TypeError
    naming a parameter the caller never passed. Say what actually happened instead.
    """
    args: list[Any] = []
    kwargs: dict[str, Any] = {}
    if run is not None and run.inputs is not None:
        decoded = decode_state(run.inputs)
        if isinstance(decoded, dict):
            raw_args = decoded.get("args")
            raw_kwargs = decoded.get("kwargs")
            args = list(cast("list[Any]", raw_args)) if isinstance(raw_args, list) else []
            kwargs = (
                dict(cast("dict[str, Any]", raw_kwargs)) if isinstance(raw_kwargs, dict) else {}
            )

    if not args and not kwargs:
        required = [
            name
            for name, param in inspect.signature(workflow_fn).parameters.items()
            if param.default is inspect.Parameter.empty
            and param.kind
            in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            )
        ]
        if required:
            run_id = run.run_id if run is not None else "<unknown>"
            raise ChowkiStateError(
                f"run {run_id!r} has no stored workflow arguments, and "
                f"{getattr(workflow_fn, '__name__', 'the workflow')} requires {required}. "
                f"Runs started before chowki persisted workflow arguments cannot be "
                f"resumed unless every parameter has a default."
            )
    return args, kwargs


def _invoke_workflow(
    workflow_fn: Callable[..., Any], run_id: str, run: RunRecord | None = None
) -> Any:
    args, kwargs = _stored_call(workflow_fn, run)
    return workflow_fn(*args, run_id=run_id, **kwargs)
```

     `inspect.signature` follows `functools.wraps`' `__wrapped__`, so it reports the
     *undecorated* workflow signature — which is what the caller writes. Add
     `decode_state` to the existing `chowki.state.codec` import (currently unused in this
     module; import it) and keep `inspect` (already imported, line 11).
   - `resume()` line 304 → `val = _invoke_workflow(target_fn, run_id, run)`.
   - `aresume()` lines 360-363 →

```python
    if inspect.iscoroutinefunction(target_fn):
        args, kwargs = _stored_call(target_fn, run)
        val = await target_fn(*args, run_id=run_id, **kwargs)
    else:
        val = _invoke_workflow(target_fn, run_id, run)
```

   - `rerun()` line 386 → `return _invoke_workflow(target_fn, run_id, run)`.

**Docs in the same commit**

- `docs/user-guide/warm-resume.md` lines 16-20: retitle to
  `### Workflow Arguments Are Replayed From the Run Record` and state: the arguments of
  the first call are persisted (redacted) on the run record and replayed by `resume()`,
  `aresume()` and `rerun()`; a required parameter is therefore fine; three caveats —
  tuples come back as lists, a secret argument replays as its `[REDACTED:...]`
  placeholder, and an argument the codec cannot encode is not stored (the run then needs
  defaults, and `chowki_workflow_args_not_persisted` is logged when it happens). Keep the
  existing advice that reviewer-editable values belong in `current_run().state`.
- `docs/user-guide/hitl.md` lines 48-52: same rewrite, shorter; keep the pointer to
  `EDIT`-patchable state.
- `python/chowki/tests/unit/test_user_guide.py:168-189`: keep the assertion (documented
  workflows staying callable with no arguments is still a good property for
  copy-pasteable examples) but rewrite the docstring and the assertion message, which
  currently claim the arguments are never replayed. New rationale: "documented examples
  must also work when a reader starts them with no arguments at all."
- `docs/features.md` Workflow runner row (line 64): add "persists the first call's
  arguments (redacted) on the run record and replays them on resume/rerun".
- `docs/research/07-cross-sdk-parity.md` §9: document `RunRecord.inputs` as a wire field —
  MessagePack `{"args": [...], "kwargs": {...}}`, redacted before encoding, written only
  when the run record is created, replayed by every re-invocation; absent/None means
  "fall back to the signature defaults". Note in §11 that the adapter contract is
  unchanged (it is a field on an existing struct, not a new operation).

**Done when**

- `uv run pytest python/chowki/tests/unit/test_workflow_arguments.py -q` passes (7 tests).
- `uv run pytest python/chowki/tests/unit python/chowki/tests/integration -q` green —
  `test_workflow_decorator.py`, `test_resume.py`, `test_aresume.py`, `test_recovery.py`
  and `test_cli.py` all re-invoke workflows and are the regression surface.
- `uv run pytest python/chowki/tests/benchmarks --benchmark-only -q` passes
  (`chowki/core/` change).
- `uv run pyright && uv run mypy python/chowki/src` clean.

---

## Task 7 — Raise on concurrent step entry instead of corrupting state

**Status:** PENDING
**Difficulty:** HARD
**Depends on:** Tasks 5 and 6 (same two files; sequencing avoids conflicting edits).
**Goal:** two steps of one run running at the same time raise `ChowkiConcurrencyError`
instead of interleaving ordinals and cross-diffing snapshots
(`POSITIONING.md:194-206`; `docs/user-guide/limits.md:70`).

**Files**

- `python/chowki/src/chowki/errors.py` (modify)
- `python/chowki/src/chowki/core/context.py` (modify `RunContext`, lines 38-99)
- `python/chowki/src/chowki/core/decorators.py` (modify both wrappers, lines 313-387)
- `python/chowki/src/chowki/__init__.py` (export)
- `python/chowki/tests/unit/test_step_concurrency.py` (new)
- `python/chowki/tests/unit/test_public_api.py` (modify the pinned set, lines 15-47)
- `docs/user-guide/limits.md` (§1, lines 7-12, and "What Can Go Wrong" item 1)
- `docs/features.md` (Single-writer-per-run row, line 69)
- `docs/research/07-cross-sdk-parity.md` (§13)

**Test first** — new file `python/chowki/tests/unit/test_step_concurrency.py`:

```python
"""Concurrency inside one run is refused, loudly, at the point it would corrupt state.

Step identity is a per-run ordinal and snapshots are a linear RFC 6902 chain, so two
steps running at once interleave ordinals and diff against each other's document. The
damage only surfaces on the next resume, which is the worst possible time to find it
(POSITIONING.md:194-206).
"""

from __future__ import annotations

import asyncio

import pytest

import chowki
from chowki.config import ChowkiEngine
from chowki.errors import ChowkiConcurrencyError


async def test_gather_over_two_steps_raises(engine: ChowkiEngine) -> None:
    @chowki.step
    async def slow(tag: str) -> str:
        await asyncio.sleep(0.01)
        return tag

    @chowki.workflow(engine=engine)
    async def fan_out() -> list[str]:
        return await asyncio.gather(slow("a"), slow("b"))

    with pytest.raises(ChowkiConcurrencyError, match="run-gather"):
        await fan_out(run_id="run-gather")


async def test_gather_raises_even_when_one_step_is_memoised(engine: ChowkiEngine) -> None:
    """The guard sits before the memo lookup: a cache hit still consumes an ordinal."""

    @chowki.step
    async def slow(tag: str) -> str:
        await asyncio.sleep(0.01)
        return tag

    @chowki.step
    async def quick(tag: str) -> str:
        return tag

    @chowki.workflow(engine=engine)
    async def warm() -> str:
        return await quick("a")

    await warm(run_id="run-memo")

    @chowki.workflow(engine=engine, name="warm")
    async def fan_out() -> list[str]:
        return await asyncio.gather(slow("s"), quick("a"))

    with pytest.raises(ChowkiConcurrencyError):
        await fan_out(run_id="run-memo")


async def test_nested_steps_are_not_concurrency(engine: ChowkiEngine) -> None:
    """`pay_invoice` -> `_transfer` is the README's own shape and must keep working."""

    @chowki.step
    async def inner(value: int) -> int:
        return value + 1

    @chowki.step
    async def outer(value: int) -> int:
        return await inner(value) * 2

    @chowki.workflow(engine=engine)
    async def pipeline() -> int:
        return await outer(1)

    assert await pipeline(run_id="run-nested") == 4


def test_nested_sync_steps_are_not_concurrency(engine: ChowkiEngine) -> None:
    @chowki.step
    def inner(value: int) -> int:
        return value + 1

    @chowki.step
    def outer(value: int) -> int:
        return inner(value) * 2

    @chowki.workflow(engine=engine)
    def pipeline() -> int:
        return outer(1)

    assert pipeline(run_id="run-nested-sync") == 4


async def test_sequential_awaits_are_allowed(engine: ChowkiEngine) -> None:
    @chowki.step
    async def one(tag: str) -> str:
        await asyncio.sleep(0)
        return tag

    @chowki.workflow(engine=engine)
    async def pipeline() -> list[str]:
        return [await one("a"), await one("b")]

    assert await pipeline(run_id="run-seq") == ["a", "b"]


def test_the_guard_releases_when_a_step_raises(engine: ChowkiEngine) -> None:
    """A failed step must not leave the run permanently 'busy'."""

    @chowki.step
    def boom() -> None:
        raise ValueError("no")

    @chowki.step
    def fine() -> str:
        return "ok"

    @chowki.workflow(engine=engine)
    def pipeline() -> str:
        try:
            boom()
        except ValueError:
            pass
        return fine()

    assert pipeline(run_id="run-release") == "ok"
```

The first two tests fail today by returning `["a", "b"]` / not raising.

**Change**

1. `python/chowki/src/chowki/errors.py` — add directly after `ChowkiStateError`
   (line 28):

```python
class ChowkiConcurrencyError(ChowkiError):
    """Two steps of one run tried to execute at the same time.

    Not a transient failure: step ordinals and the RFC 6902 snapshot chain are both
    strictly sequential per run, so the interleaving would have produced a state
    document no resume can rebuild. Parallel steps within a run are Phase 6
    (deterministic branch keys); until then, run steps sequentially or run independent
    runs concurrently.
    """
```

2. `python/chowki/src/chowki/core/context.py`:

   - Imports: add `asyncio`, `threading`, `from collections.abc import Iterator`,
     `from contextlib import contextmanager`, and
     `from chowki.errors import ChowkiConcurrencyError` (no cycle: `errors.py` imports
     nothing from `chowki`).
   - Add a module-level helper above `RunContext`:

```python
def _executor_id() -> tuple[int, int]:
    """Identify the thread and task a step body is running on.

    An asyncio task copies the context rather than the context *var*, so every task in a
    `gather` sees the same RunContext object -- the identity of the running task is the
    only thing that tells them apart. Outside a running loop there is no task and the
    thread carries the whole identity.
    """
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return (threading.get_ident(), id(task) if task is not None else 0)
```

   - Add three fields to `RunContext` (a `slots=True` dataclass, so they must be
     declared), after `_snapshot_index` (line 58):

```python
    _step_owner: tuple[int, int] | None = None
    _active_step: str | None = None
    _step_depth: int = 0
```

   - Add the guard as a method, after `next_snapshot_index`:

```python
    @contextmanager
    def step_guard(self, step_name: str) -> Iterator[None]:
        """Refuse a second concurrent step in this run instead of corrupting its state.

        Nesting is not concurrency: a step that calls another step runs on the same task
        and thread, so the owner matches and only the depth grows. A different task or
        thread entering while a step is live is the `asyncio.gather` case, and it is
        refused before any ordinal is allocated or any record written.
        """
        owner = _executor_id()
        if self._step_owner is not None and self._step_owner != owner:
            raise ChowkiConcurrencyError(
                f"step {step_name!r} of run {self.run_id} started while step "
                f"{self._active_step!r} is still running. Concurrent steps within one run "
                f"(asyncio.gather, asyncio.to_thread, thread pools) are not supported and "
                f"would corrupt the run's snapshot chain: run the steps sequentially, or "
                f"run independent workflow runs concurrently."
            )
        if self._step_owner is None:
            self._step_owner = owner
            self._active_step = step_name
        self._step_depth += 1
        try:
            yield
        finally:
            self._step_depth -= 1
            if self._step_depth == 0:
                self._step_owner = None
                self._active_step = None
```

3. `python/chowki/src/chowki/core/decorators.py` — in **both** wrappers, wrap everything
   from `_begin` to the `return` in the guard. The async wrapper becomes:

```python
                ctx = current_run()
                with ctx.step_guard(step_name):
                    rec, memoised = _begin(ctx, step_name, args, kwargs, idempotent)
                    if memoised is not _MISSING:
                        return cast(R, memoised)
                    ...  # existing breaker/retry loop, indented one level
                    _succeed(ctx, rec, res, snapshot)
                    return cast(R, res)
```

   The memoised early return must stay **inside** the `with` — the guard has to cover the
   memo lookup, because a cache hit still consumes an ordinal. Do the same in
   `sync_wrapper`. No other logic moves.

4. `python/chowki/src/chowki/__init__.py` — import `ChowkiConcurrencyError` from
   `chowki.errors` and add it to `__all__` (alphabetically, before `ChowkiError`); add
   the same string to the pinned set in
   `python/chowki/tests/unit/test_public_api.py:15-47`.

**Docs in the same commit**

- `docs/user-guide/limits.md` §1: change "You must not run…" to "chowki refuses to run…":
  a second step entering a run while one is live raises `ChowkiConcurrencyError`; nesting
  (a step calling a step) is unaffected; concurrent independent runs are still fine;
  parallel steps within a run remain Phase 6. Add the false-positive note: a step invoked
  through `asyncio.to_thread` from inside another step is refused too, because chowki
  cannot tell it apart from a real fan-out — do the offload outside the step, or leave
  the inner function undecorated. Update "What Can Go Wrong" item 1.
- `docs/features.md` line 69: change the Python cell from `✅ (documented)` to
  `✅ (enforced: `ChowkiConcurrencyError`)` and update the Behavior cell.
- `docs/research/07-cross-sdk-parity.md` §13: parallel steps stay unbuilt, but detection
  is now normative — an SDK MUST refuse concurrent step entry within a run rather than
  leave it undefined.

**Done when**

- `uv run pytest python/chowki/tests/unit/test_step_concurrency.py -q` passes (6 tests).
- `uv run pytest python/chowki/tests/unit python/chowki/tests/integration -q` green,
  including `test_public_api.py`.
- `uv run pytest python/chowki/tests/benchmarks --benchmark-only -q` passes — the guard
  adds one context manager per step against a 50 µs budget (`chowki/core/` change).
- `uv run pyright && uv run mypy python/chowki/src` clean.

---

## Task 8 — CHANGELOG, catalog sweep, and full verification

**Status:** PENDING
**Difficulty:** EASY
**Depends on:** Tasks 1-7.
**Goal:** the release notes tell an upgrader what changed under them, and the whole gate
passes in one run.

**Files**

- `CHANGELOG.md` (modify)
- `docs/features.md` (verify only — the rows were edited in Tasks 5-7)

**Test first** — no new behaviour, so no new test. The verification *is* the gate:
`uv run python scripts/ci_local.py` must pass end to end before this task is complete.

**Change** — insert a new section in `CHANGELOG.md` directly below the header block
(after line 8's `---`, above `## [0.1.0] - 2026-08-11`):

```markdown
## [Unreleased]

### Fixed

- **Step argument hashing no longer collapses complex objects.** msgspec Structs,
  dataclasses, Pydantic models (via `model_dump`), enums, bytes, datetimes, UUIDs,
  Decimals, and ordinary objects (via `__dict__`) are now expanded structurally before
  hashing. Two different instances of one class previously produced the same `args_hash`,
  so a step could return another call's memoised result. Values with no exposable
  structure still collapse to `<TypeName>` and now log `chowki_step_args_opaque`.
- **Workflow arguments are persisted and replayed.** The first call's arguments are
  stored (redacted) on the run record as `RunRecord.inputs` and replayed by `resume()`,
  `aresume()` and `rerun()`. Workflows with required parameters are resumable, and a
  defaulted parameter no longer silently rebinds its default on resume — the failure mode
  that re-ran steps against a different entity than the one a human approved.
- **Concurrent step entry inside one run raises `ChowkiConcurrencyError`** instead of
  interleaving ordinals and corrupting the RFC 6902 snapshot chain. Nested steps are
  unaffected; concurrent independent runs are unaffected.

### Changed

- **`args_hash` values change for steps that take complex arguments.** A run that is
  in flight across this upgrade will miss the memo for those steps, log
  `chowki_step_args_changed`, and re-execute them — including their side effects. **Drain
  or complete in-flight runs before upgrading.** Runs whose step arguments are primitives,
  dicts, lists, sets, or tuples are unaffected.
- Workflow arguments are redacted before they are persisted, so a secret passed directly
  as a workflow argument replays as its `[REDACTED:...]` placeholder;
  `chowki_workflow_args_redacted` is logged when that happens. Tuples replay as lists.
- PyPI metadata: new `description`, searchable `keywords`, and AI/recovery/security
  Trove classifiers.
- `scripts/check_layout.py` allowlists `README.md`, `python/chowki/README.md`,
  `docs/comparison.md`, and `POSITIONING.md` for the banned product term, so the READMEs
  can name the concept in the reader's vocabulary. The ban is unchanged everywhere else.

### Added

- `chowki.ChowkiConcurrencyError` in the public API surface.
- Repository metadata (GitHub About box, topics, naming rule) recorded in
  `docs/user-guide/launch-checklist.md`.
```

**Sweep** — confirm, by reading the file, that `docs/features.md` carries the Task 5, 6
and 7 edits (Args-hash row line 57, Workflow runner row line 64, Single-writer-per-run row
line 69). A row nobody updated is a bug per the file's own header.

**Close-out**

- Flip every `**Status:** PENDING` in this plan to `COMPLETED`.
- Leave this plan file in the tree: it is not a roadmap phase, so the
  `docs/plans/00-roadmap.md` working agreement (delete the plan, flip the phase to DONE)
  does not apply and the roadmap needs no edit.
- Publishing v0.1.0 to PyPI, setting the GitHub About box, and setting the 20 topics
  remain maintainer actions. Do not claim they are done.

**Done when**

- `uv run python scripts/ci_local.py` exits 0 (layout, sync, format, lint, pyright, mypy,
  unit, integration, benchmarks, wheel smoke test — the exact CI sequence).
- `git log --oneline` shows one commit per task, in task order.

---

## Risks

**R1 — Upgrading with runs in flight re-fires side effects (Task 5).** Changed
`args_hash` values mean a completed step with complex arguments is found with a mismatched
hash: chowki logs `chowki_step_args_changed` and re-executes the body. For an idempotent
step whose claim is already held, `_begin` (decorators.py:152-166) refuses with a
`ChowkiStorageError` naming `release_step`/`complete_step`, which is the safe outcome —
but it is an outage for that run. *Mitigation:* the CHANGELOG entry says drain first.
*Recovery:* `chowki runs list` to find stuck runs, then `chowki complete-step` (the effect
happened) or `chowki release-step` (it did not).

**R2 — `__dict__` expansion is slower or less stable than expected (Task 5).** An object
holding per-process junk (a socket, a client with a connection id) hashes differently in a
recovering process, so its step re-executes on resume. That is a cache miss with a warning,
not a wrong answer, and it is strictly better than today's silent collision — but if a
benchmark budget breaks or a real workload thrashes, narrow `_expand` to
`to_builtins` + `model_dump` only and let ordinary objects be opaque (one deleted branch).
**Never relax a number in `budgets.py` to make the benchmark pass** (AGENTS.md §5).

**R3 — Redacted workflow arguments replay as placeholders (Task 6).** A workflow started
with `api_key="sk-…"` resumes with `api_key="[REDACTED:api_key:…]"`. Warned at first
execution and documented; the fix for a user is to pass secrets through configuration or
the environment, not through workflow parameters. Alternatives (store raw, store nothing)
are both worse.

**R4 — Type drift on replayed arguments (Task 6).** MessagePack round-trips tuples to
lists and Structs to dicts, same as step results. A workflow that indexes or `isinstance`-
checks a tuple parameter will behave differently on resume. Documented in
`warm-resume.md`; the executor must not "fix" it by adding type reconstruction.

**R5 — The concurrency guard has a known false positive (Task 7).** `await
asyncio.to_thread(some_step)` *inside* another step is sequential but is refused, because
chowki cannot distinguish it from a fan-out. Documented with the escape hatch (offload
outside the step, or leave the inner function undecorated). If this proves too strict in
practice, narrow `_executor_id` to the task component alone — but that stops detecting
thread-pool fan-out, so do not do it speculatively.

**R6 — `uv build` rejects a classifier (Task 3).** The Trove strings come from
`POSITIONING.md:464-470` and were **not** re-read from `pypi.org/classifiers` this session
— treat them as UNVERIFIED. `uv build` hard-fails on an invalid classifier, which is the
verification step; if one is rejected, drop that single string, keep the rest, and say
which one was dropped in the commit message. Do not invent a replacement.

**R7 — The allowlist erodes the naming discipline (Task 1).** It is a `frozenset` of
exactly four paths, and the second new test proves the ban still fires elsewhere. If a
fifth surface ever needs it, that is a deliberate edit with a review, not a default.

**R8 — Downgrade safety (Task 6).** `RunRecord.inputs` is an appended field with a
default; msgspec ignores unknown fields when decoding, so records written by the new build
still decode on an older build (the arguments are simply not replayed). Nothing in this
plan rewrites or deletes stored data.

**Rollback.** One commit per task, no data migrations, no destructive operations.
`git revert <sha>` per task is sufficient; reverting Task 5 restores the old hashes, which
again invalidates memoisation for runs started after the upgrade — so revert it only with
runs drained, for the same reason the upgrade needs them drained.

PLAN COMPLETE
