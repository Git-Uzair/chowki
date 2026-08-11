"""Inspection API for chowki workflow runs."""

from __future__ import annotations

from typing import cast

import msgspec

from chowki.config import ChowkiEngine, get_engine
from chowki.errors import ChowkiStateError
from chowki.state.pipeline import SnapshotPipeline
from chowki.types import JSONObject, JSONValue, PauseRequest, RunRecord, RunStatus, StepRecord


class RunInspection(msgspec.Struct, frozen=True):
    """Snapshot inspection container for a workflow run."""

    run: RunRecord
    steps: list[StepRecord]
    state: JSONValue | None
    audit: list[JSONObject]
    pause: PauseRequest | None
    resumable: bool


def inspect_run(run_id: str, *, engine: ChowkiEngine | None = None) -> RunInspection:
    """Inspect all state, steps, audit logs, and pause status for a given run_id."""
    eng = engine if engine is not None else get_engine()
    run = eng.storage.get_run(run_id)
    if run is None:
        raise ChowkiStateError(f"unknown run_id {run_id!r}")

    steps = sorted(eng.storage.list_steps(run_id), key=lambda s: s.ordinal)
    audit = cast(list[JSONObject], eng.storage.list_audit(run_id=run_id))
    pause = run.pause
    resumable = run.status in {RunStatus.PENDING, RunStatus.RUNNING, RunStatus.PAUSED}

    snaps = eng.storage.snapshots_for_resume(run_id)
    if not snaps:
        state = None
    else:
        pipeline = SnapshotPipeline(
            redactor=eng.redactor,
            blobs=eng.blobs,
            tenant_id=eng.config.tenant_id,
            keyring=eng.keyring,
            blob_threshold_bytes=eng.config.blob_threshold_bytes,
        )
        state = pipeline.load(snaps)

    return RunInspection(
        run=run,
        steps=steps,
        state=state,
        audit=audit,
        pause=pause,
        resumable=resumable,
    )
