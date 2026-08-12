"""Chowki Showcase Agent: LLM tool-use agent with budget warnings, HITL, and zero-waste recovery.

Demonstrates:
1. Self-contained tool-use agent loop with deterministic fake LLM (no external API key needed).
2. Swappable 5-line LLM client integration snippet for OpenAI / Anthropic.
3. Token / cost usage tracking with soft budget warnings via `chowki.report_usage()`.
4. Human-in-the-Loop approval gate (`chowki.pause`) before dangerous tool execution.
5. Simulated mid-run crash (`--crash-after N`) and zero-waste recovery.

Best Practices:
    Every side effect in a Chowki workflow must live inside a `@chowki.step`.
    Because `resume()` re-executes the workflow function body from the top,
    any side effect outside a `@chowki.step` will be re-executed on warm resume.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from pathlib import Path
from typing import Any, cast

import chowki
from chowki.config import ChowkiEngine
from chowki.guardrails import GuardrailConfig
from chowki.hitl.console import ConsoleGateway

# To swap in a real OpenAI or Anthropic LLM client, replace fake_llm_call with:
# -----------------------------------------------------------------------------
# from openai import OpenAI
# client = OpenAI()
# resp = client.chat.completions.create(model="gpt-4o", messages=messages, tools=tools)
# chowki.report_usage(
#     chowki.Usage(
#         input_tokens=resp.usage.prompt_tokens,
#         output_tokens=resp.usage.completion_tokens,
#     )
# )
# return resp.choices[0].message
# -----------------------------------------------------------------------------

_llm_call_count: int = 0


def get_llm_call_count() -> int:
    """Return total number of LLM invocations executed in the current process."""
    return _llm_call_count


def reset_llm_call_count() -> None:
    """Reset the global LLM invocation counter (for testing)."""
    global _llm_call_count
    _llm_call_count = 0


@chowki.step
def agent_plan(prompt: str) -> dict[str, Any]:
    """LLM step 1: Analyze user request and choose initial safe tool."""
    global _llm_call_count
    _llm_call_count += 1

    # Report 1,650 tokens. With max_token_budget=2,000, 1650/2000 = 82.5% >= 80% soft limit.
    chowki.report_usage(chowki.Usage(input_tokens=1300, output_tokens=350, cost_usd=0.015))

    return {
        "action": "search",
        "query": f"python security best practices for {prompt}",
    }


@chowki.step
def search_tool(query: str) -> list[str]:
    """Safe tool: Execute search query and return results."""
    return [
        "Use parameterized SQL queries to prevent injection",
        "Sanitize inputs and validate strict schemas",
        "Redact sensitive secrets and tokens in application logs",
    ]


@chowki.step
def agent_draft_email(search_results: list[str]) -> dict[str, Any]:
    """LLM step 2: Process tool output and draft summary email."""
    global _llm_call_count
    _llm_call_count += 1

    chowki.report_usage(chowki.Usage(input_tokens=200, output_tokens=100, cost_usd=0.003))

    body_text = "Security Audit Findings:\n" + "\n".join(f"- {r}" for r in search_results)
    return {
        "to": "unverified-vendor@example.com",
        "subject": "Security Audit Summary Report",
        "body": body_text,
    }


@chowki.step
def send_email_tool(draft: dict[str, Any]) -> str:
    """Dangerous tool: Send approved email."""
    recipient = draft.get("to", "unknown")
    subject = draft.get("subject", "No subject")
    return f"Email sent to {recipient} with subject '{subject}'"


@chowki.workflow
def agent_review_workflow(prompt: str = "Audit repo security") -> str:
    """Showcase Agent Workflow: safe search -> draft email -> human approval gate -> send email.

    The crash simulation is driven by ``CHOWKI_CRASH_AFTER`` in the environment rather
    than by a workflow parameter, because chowki persists a workflow's arguments and
    replays them on `rerun()`: "this process should crash" would then be replayed too,
    and the recovery run would crash at the very step it exists to get past.
    """
    crash_after: int | None = None
    if "CHOWKI_CRASH_AFTER" in os.environ:
        with contextlib.suppress(ValueError):
            crash_after = int(os.environ["CHOWKI_CRASH_AFTER"])

    # Step 1: LLM planning
    plan = agent_plan(prompt)
    if crash_after == 1:
        sys.stderr.write("Simulated crash after step 1\n")
        raise RuntimeError("Simulated mid-run crash after step 1")

    # Step 2: Safe search tool
    query = str(plan.get("query", prompt))
    results = search_tool(query)
    if crash_after == 2:
        sys.stderr.write("Simulated crash after step 2\n")
        raise RuntimeError("Simulated mid-run crash after step 2")

    # Step 3: LLM drafts email
    draft = agent_draft_email(results)

    # Store draft in run state so Human-in-the-Loop patch can edit recipient/body
    chowki.current_run().state["draft"] = draft

    # Pause workflow for human approval before dangerous tool execution
    chowki.pause(
        reason="Email approval needed",
        payload=draft,
        permitted_actions=("APPROVE", "REJECT", "EDIT"),
    )

    # Read final draft (which incorporates any EDIT patch applied during warm resume)
    current_state = chowki.current_run().state
    final_draft = cast(dict[str, Any], current_state.get("draft", draft))

    if crash_after == 3:
        sys.stderr.write("Simulated crash after step 3 (post-pause)\n")
        raise RuntimeError("Simulated mid-run crash after step 3")

    # Step 4: Dangerous tool execution
    return send_email_tool(final_draft)


def _stall_run(engine: ChowkiEngine, run_id: str) -> None:
    """Put the run back into RUNNING: the state a hard-killed process leaves behind.

    A caught exception is a tidy failure, so chowki marks the run FAILED. A real
    kill -9 never gets that far, and `chowki recover` only revives RUNNING runs.
    """
    run = engine.storage.get_run(run_id)
    if run is not None:
        run.status = chowki.RunStatus.RUNNING
        engine.storage.put_run(run)


def _print_operator_recovery_hint(db: str, run_id: str) -> None:
    """Print the CLI arc an operator runs after a crash (README step 2/3)."""
    print(f"Total LLM calls executed before crash: {get_llm_call_count()}")
    print(f"Run {run_id} left stalled in RUNNING status. Recover it from your shell:")
    print(f"  chowki --db {db} -m examples.python.agent_review recover")
    print(f"  chowki --db {db} -m examples.python.agent_review rerun {run_id}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Chowki Showcase Agent Example")
    parser.add_argument(
        "--crash-after",
        type=int,
        default=None,
        help="Simulate mid-run crash after N steps (1, 2, or 3)",
    )
    parser.add_argument(
        "--db",
        type=str,
        default="./chowki.db",
        help="Path to SQLite database file",
    )
    parser.add_argument(
        "--no-auto-recover",
        action="store_true",
        help="On a simulated crash, exit(1) and leave the run stalled for `chowki recover`",
    )
    args = parser.parse_args()

    if args.crash_after is not None:
        os.environ["CHOWKI_CRASH_AFTER"] = str(args.crash_after)

    db_path = Path(args.db)
    if db_path.exists():
        with contextlib.suppress(OSError):
            db_path.unlink()

    # Configure chowki with budget guardrails and console gateway
    engine = chowki.configure(
        db_path=db_path,
        gateway=ConsoleGateway(),
        resume_secret=b"showcase-agent-32-byte-secret!",
        guardrails=GuardrailConfig(
            max_token_budget=2000,
            soft_budget_threshold=0.80,  # Warning at 1,600 tokens
        ),
    )

    print("\n=== Chowki Showcase Agent ===")
    print("Goal: Audit repository security and send summary email.\n")

    run_id = "showcase-agent-run-1"
    token = None

    # Step 1: Initial run -> watch soft budget warning & hit approval gate (or simulated crash)
    print("--- [1/4] Running Agent Workflow ---")
    try:
        agent_review_workflow(prompt="Audit repo security", run_id=run_id)
    except chowki.WorkflowPaused as exc:
        token = exc.token
        print(f"\n[Pause Gate] Workflow paused at step '{exc.step_id}'!")
        print(f"[Pause Gate] Resume Token: {token}\n")
    except RuntimeError as err:
        print(f"[CRASH SIMULATED] Process crashed mid-run: {err}\n")
        _stall_run(engine, run_id)
        if args.no_auto_recover:
            _print_operator_recovery_hint(args.db, run_id)
            sys.exit(1)

        print("--- Recovering Stalled Runs (`chowki recover`) ---")
        recovered = chowki.recover_runs(engine=engine)
        print(f"Recovered {len(recovered)} run(s) back to PENDING status.\n")

        print("--- Rerunning Recovered Workflow (`chowki rerun`) ---")
        # The env var trigger has to go before the rerun, or the rerun crashes at the
        # very step it is meant to skip past.
        os.environ.pop("CHOWKI_CRASH_AFTER", None)
        try:
            chowki.rerun(run_id=run_id, engine=engine)
        except chowki.WorkflowPaused as exc:
            token = exc.token
            print(f"\n[Pause Gate] Workflow paused at step '{exc.step_id}'!")
            print(f"[Pause Gate] Resume Token: {token}\n")

    if token is None:
        print("Workflow completed without pausing.")
        print(f"Total LLM calls executed: {get_llm_call_count()}")
        return

    # Step 2: Resume with EDIT decision patching the recipient
    print("--- [2/4] Resuming via CLI with EDIT patch ---")
    print(
        "Patching email recipient from 'unverified-vendor@example.com' "
        "to 'security-team@example.com'...\n"
    )

    # If --crash-after 3 was requested, simulate a crash right after pause unblocks
    crash_simulation = args.crash_after == 3 or os.environ.get("CHOWKI_CRASH_AFTER") == "3"

    try:
        if crash_simulation:
            os.environ["CHOWKI_CRASH_AFTER"] = "3"

        res = chowki.resume(
            run_id=run_id,
            token=token,
            decision=chowki.Decision.EDIT,
            patch=[{"op": "replace", "path": "/draft/to", "value": "security-team@example.com"}],
            engine=engine,
        )
        print(f"Workflow finished: {res.value}")
        print(f"Total LLM calls executed: {get_llm_call_count()}")
        print("(Notice: Completed steps were skipped and NOT re-executed!)\n")
        return
    except RuntimeError as err:
        print(f"[CRASH SIMULATED] Process crashed mid-run: {err}\n")
        _stall_run(engine, run_id)
        if args.no_auto_recover:
            _print_operator_recovery_hint(args.db, run_id)
            sys.exit(1)

        # Step 3: Recover crashed / stalled run
        print("--- [3/4] Recovering Stalled Runs (`chowki recover`) ---")
        recovered = chowki.recover_runs(engine=engine)
        print(f"Recovered {len(recovered)} run(s) back to PENDING status.\n")

        # Step 4: Rerun recovered run (`chowki rerun`)
        print("--- [4/4] Rerunning Recovered Workflow (`chowki rerun`) ---")
        os.environ.pop("CHOWKI_CRASH_AFTER", None)

        final_result = chowki.rerun(run_id=run_id, engine=engine)
        print("\nWorkflow Completed Successfully!")
        print(f"Result: {final_result}")
        print(f"Total LLM calls executed across all runs: {get_llm_call_count()}")
        print("(Notice: Completed steps were skipped and NOT re-executed!)\n")


if __name__ == "__main__":
    main()
