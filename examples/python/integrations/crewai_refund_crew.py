"""chowki + CrewAI.

    pip install chowki crewai

CrewAI orchestrates role-playing agents: you give an Agent a role and tools, attach Tasks,
group them into a Crew, and call `kickoff()`. The crew runs to completion or it doesn't —
there is no built-in "stop here and wait for a human, then carry on tomorrow".

chowki adds that, at the tool boundary::

    @tool("Issue refund")   # outermost: CrewAI's decorator
    @chowki.step(...)       # innermost: chowki intercepts the call
    def issue_refund(...): ...

A crew that pauses mid-kickoff raises `WorkflowPaused` out of `kickoff()`. Resuming replays
the crew, and every tool call that already completed returns its recorded result — so the
`support_agent` does not re-look-up the order and the refund cannot fire twice.

This matters more for CrewAI than for most frameworks: hierarchical crews fan out across
several agents, and a failure in the last task otherwise discards every LLM call the
earlier agents already paid for.

Caveat: chowki memoises what you decorate. CrewAI's own model calls happen inside the
framework, so a resume re-issues them unless you route them through a chowki step — see
`durable_llm` below.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import chowki
from chowki.guardrails import GuardrailConfig
from chowki.hitl.console import ConsoleGateway

sys.path.insert(0, str(Path(__file__).parent))

from _refund_domain import executed_refunds, issue_refund, lookup_order, report_usage

try:
    from crewai import Agent, Crew, Process, Task
    from crewai.tools import tool
except ImportError as exc:  # pragma: no cover - example dependency
    raise SystemExit("This example needs CrewAI:  pip install crewai") from exc


# --- The integration: CrewAI's @tool on the outside, chowki's @step underneath ----------
lookup_order_tool = tool("Look up order")(lookup_order)
issue_refund_tool = tool("Issue refund")(issue_refund)


def durable_llm(llm: Any) -> Any:
    """Optional: wrap a CrewAI LLM so its calls are memoised and counted.

    CrewAI calls the model inside the framework, outside chowki's view. Passing an LLM
    whose `call` goes through a `@chowki.step` means a resumed crew replays recorded
    completions instead of re-billing them.
    """
    inner_call = llm.call

    @chowki.step(name="llm_turn")
    def _turn(messages: Any, **kwargs: Any) -> Any:
        response = inner_call(messages, **kwargs)
        usage = getattr(llm, "_last_usage", None) or {}
        report_usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
        return response

    llm.call = _turn
    return llm


@chowki.workflow
def refund_crew(order_id: str = "ord-4417") -> str:
    """A CrewAI crew that can stop at a human gate and pick up where it left off."""
    support_agent = Agent(
        role="Refund Specialist",
        goal="Resolve customer refund requests correctly and only when justified",
        backstory="You handle refunds carefully. Large refunds go to a human first.",
        tools=[lookup_order_tool, issue_refund_tool],
        verbose=True,
    )

    refund_task = Task(
        description=(
            f"Customer requests a refund for order {order_id}. "
            "Look up the order, then refund the full amount."
        ),
        expected_output="A one-line confirmation of the refund.",
        agent=support_agent,
    )

    crew = Crew(agents=[support_agent], tasks=[refund_task], process=Process.sequential)
    return str(crew.kickoff())


def main() -> None:
    chowki.configure(
        db_path="./crewai_refund.db",
        gateway=ConsoleGateway(),
        resume_secret=b"crewai-example-32-byte-secret-ok!",
        # A crew that fans out across agents is exactly where a budget ceiling earns its keep.
        guardrails=GuardrailConfig(max_token_budget=50_000, soft_budget_threshold=0.8),
    )

    run_id = "crewai-refund-1"
    try:
        print(refund_crew(order_id="ord-4417", run_id=run_id))
        return
    except chowki.WorkflowPaused as paused:
        print(f"\nCrew paused at {paused.step_id}. Refunds so far: {executed_refunds}")
        token = paused.token

    result = chowki.resume(run_id=run_id, token=token, decision=chowki.Decision.APPROVE)
    print(f"\nCrew resumed: {result.value}")
    print(f"Refunds executed: {executed_refunds}  <- the lookup was not repeated")


if __name__ == "__main__":
    main()
