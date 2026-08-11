"""CLI entry point for the `chowki` console script."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import json
import os
import sys
from pathlib import Path
from typing import Any

import msgspec
import structlog

from chowki import __version__
from chowki.config import ChowkiEngine, configure
from chowki.core.decorators import complete_step, release_step
from chowki.core.inspection import inspect_run
from chowki.core.resume import aresume, rerun
from chowki.core.runner import recover_runs, reissue_token
from chowki.errors import ChowkiError, WorkflowPaused
from chowki.types import Decision, RunStatus


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chowki",
        description="chowki — in-process agent state preservation, guardrails, and warm resume",
    )
    parser.add_argument(
        "--db",
        default="./.chowki/chowki.db",
        help="Path to SQLite database file (default: ./.chowki/chowki.db)",
    )
    parser.add_argument(
        "-m",
        "--module",
        action="append",
        default=[],
        help="Python module(s) to import so workflow definitions registration runs (repeatable)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"chowki {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: runs
    runs_parser = subparsers.add_parser("runs", help="Manage and inspect workflow runs")
    runs_subparsers = runs_parser.add_subparsers(dest="runs_command", required=True)

    # runs list
    list_parser = runs_subparsers.add_parser("list", help="List workflow runs")
    list_parser.add_argument(
        "--status",
        choices=[s.value for s in RunStatus],
        help="Filter runs by status",
    )

    # runs show
    show_parser = runs_subparsers.add_parser("show", help="Show inspection for a run")
    show_parser.add_argument("run_id", help="Run ID to inspect")

    # Subcommand: resume
    resume_parser = subparsers.add_parser("resume", help="Resume a paused workflow run")
    resume_parser.add_argument("run_id", help="Run ID to resume")
    resume_parser.add_argument("-t", "--token", required=True, help="Resume token")
    resume_parser.add_argument(
        "-d",
        "--decision",
        required=True,
        choices=[d.value for d in Decision],
        help="Human decision (APPROVE, REJECT, EDIT, ESCALATE)",
    )
    resume_parser.add_argument("-p", "--patch", help="JSON patch for state editing")
    resume_parser.add_argument("-n", "--note", help="Audit log note")

    # Subcommand: reissue-token
    reissue_parser = subparsers.add_parser("reissue-token", help="Reissue resume token for a run")
    reissue_parser.add_argument("run_id", help="Run ID to reissue token for")

    # Subcommand: release-step
    release_parser = subparsers.add_parser(
        "release-step", help="Release idempotency claim for a step"
    )
    release_parser.add_argument("run_id", help="Run ID")
    release_parser.add_argument("step_id", help="Step ID / idempotency key")

    # Subcommand: complete-step
    complete_parser = subparsers.add_parser(
        "complete-step", help="Force-complete a step with a result"
    )
    complete_parser.add_argument("run_id", help="Run ID")
    complete_parser.add_argument("step_id", help="Step ID")
    complete_parser.add_argument("-r", "--result", required=True, help="Result payload (JSON)")

    # Subcommand: recover
    subparsers.add_parser("recover", help="Recover stalled RUNNING runs to PENDING")

    # Subcommand: rerun
    rerun_parser = subparsers.add_parser("rerun", help="Rerun a recovered or pending workflow")
    rerun_parser.add_argument("run_id", help="Run ID")

    return parser


def cli_entry(argv: list[str] | None = None) -> int:
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(sys.stderr))

    parser = _build_parser()
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    resume_secret = os.environ.get("CHOWKI_RESUME_SECRET")
    encrypt_at_rest = "CHOWKI_MASTER_KEY" in os.environ

    engine = configure(
        db_path=db_path,
        resume_secret=resume_secret if resume_secret else None,
        encrypt_at_rest=encrypt_at_rest,
    )

    if args.module:
        if "" not in sys.path and "." not in sys.path:
            sys.path.insert(0, "")
        for mod_name in args.module:
            importlib.import_module(mod_name)

    try:
        return _dispatch_command(args, engine)
    except ChowkiError as err:
        sys.stderr.write(f"Error: {err}\n")
        return 1
    except Exception as err:
        sys.stderr.write(f"Error: {err}\n")
        return 1


def _dispatch_command(args: argparse.Namespace, engine: ChowkiEngine) -> int:
    cmd = args.command

    if cmd == "runs":
        if args.runs_command == "list":
            status_filter = RunStatus(args.status.upper()) if args.status else None
            runs = engine.storage.list_runs(status=status_filter)
            if args.json:
                print(msgspec.json.encode(runs).decode("utf-8"))
            else:
                if not runs:
                    print("No runs found.")
                else:
                    header = f"{'RUN ID':<36}  {'WORKFLOW':<20}  {'STATUS':<10}  {'CREATED AT'}"
                    print(header)
                    print("-" * len(header))
                    for r in runs:
                        print(
                            f"{r.run_id:<36}  {r.workflow:<20}  {r.status:<10}  {r.created_at_utc}"
                        )
            return 0

        if args.runs_command == "show":
            inspection = inspect_run(args.run_id, engine=engine)
            if args.json:
                print(msgspec.json.encode(inspection).decode("utf-8"))
            else:
                run = inspection.run
                print(f"Run ID:      {run.run_id}")
                print(f"Workflow:    {run.workflow}")
                print(f"Status:      {run.status}")
                print(f"Created At:  {run.created_at_utc}")
                print(f"Resumable:   {inspection.resumable}")
                print(f"Steps ({len(inspection.steps)}):")
                for s in inspection.steps:
                    print(f"  - {s.step_id} ({s.name}): status={s.status}")
                if inspection.pause is not None:
                    p = inspection.pause
                    print(f"Pause Gate:  step={p.step_id}, reason={p.reason}")
                if inspection.state is not None:
                    print(f"State:       {inspection.state}")
            return 0

    elif cmd == "resume":
        patch_val = json.loads(args.patch) if args.patch else None
        decision_val = Decision(args.decision.upper())
        try:
            res = asyncio.run(
                aresume(
                    run_id=args.run_id,
                    token=args.token,
                    decision=decision_val,
                    patch=patch_val,
                    note=args.note,
                    engine=engine,
                )
            )
            if args.json:
                out: dict[str, Any] = {
                    "run_id": res.run_id,
                    "decision": res.decision,
                    "value": res.value,
                    "state_hash_before": res.state_hash_before,
                    "state_hash_after": res.state_hash_after,
                }
                print(json.dumps(out, default=str))
            else:
                print(f"Resumed run {res.run_id}: decision={res.decision}, value={res.value}")
            return 0
        except WorkflowPaused as exc:
            if args.json:
                print(
                    json.dumps(
                        {
                            "status": "PAUSED",
                            "run_id": exc.run_id,
                            "step_id": exc.step_id,
                            "token": exc.token,
                        }
                    )
                )
            else:
                print(f"Run {exc.run_id} paused at step {exc.step_id}.\nToken: {exc.token}")
            return 0

    elif cmd == "reissue-token":
        token = reissue_token(args.run_id, engine=engine)
        if args.json:
            print(json.dumps({"run_id": args.run_id, "token": token}))
        else:
            print(token)
        return 0

    elif cmd == "release-step":
        release_step(args.run_id, args.step_id, engine=engine)
        if args.json:
            print(json.dumps({"released": True, "run_id": args.run_id, "step_id": args.step_id}))
        else:
            print(f"Released step {args.step_id} for run {args.run_id}")
        return 0

    elif cmd == "complete-step":
        result_val: Any = json.loads(args.result)
        complete_step(args.run_id, args.step_id, result_val, engine=engine)
        if args.json:
            print(json.dumps({"completed": True, "run_id": args.run_id, "step_id": args.step_id}))
        else:
            print(f"Completed step {args.step_id} for run {args.run_id}")
        return 0

    elif cmd == "recover":
        recovered = recover_runs(engine=engine)
        if args.json:
            print(msgspec.json.encode(recovered).decode("utf-8"))
        else:
            if not recovered:
                print("No runs recovered.")
            else:
                run_ids = [r.run_id for r in recovered]
                print(f"Recovered {len(recovered)} run(s): {', '.join(run_ids)}")
        return 0

    elif cmd == "rerun":
        res = rerun(args.run_id, engine=engine)
        if inspect.iscoroutine(res):
            res = asyncio.run(res)
        if args.json:
            out_rerun: dict[str, Any] = {"run_id": args.run_id, "result": res}
            print(json.dumps(out_rerun, default=str))
        else:
            print(f"Reran {args.run_id}: {res}")
        return 0

    return 1


def main() -> None:
    sys.exit(cli_entry())
