from __future__ import annotations

import random
from enum import StrEnum

from chowki.errors import ErrorClass, classify
from chowki.guardrails.config import GuardrailConfig


class BreakerAction(StrEnum):
    RETRY = "RETRY"
    REASK = "REASK"
    SUMMARIZE = "SUMMARIZE"
    PAUSE = "PAUSE"
    ABORT = "ABORT"


class AnomalyBreaker:
    def __init__(
        self,
        config: GuardrailConfig,
        *,
        hitl_available: bool = True,
        summarizer_available: bool = True,
    ) -> None:
        self._cfg = config
        self._hitl_available = hitl_available
        self._summarizer_available = summarizer_available

    def decide(self, exc: BaseException, *, attempt: int) -> BreakerAction:
        if not self._cfg.enabled:
            return BreakerAction.ABORT

        err_class = classify(exc)

        action: BreakerAction
        if err_class in (ErrorClass.RATE_LIMIT, ErrorClass.TOOL_EXECUTION):
            if attempt < self._cfg.max_auto_retries:
                action = BreakerAction.RETRY
            else:
                action = BreakerAction.PAUSE
        elif err_class == ErrorClass.VALIDATION:
            if attempt < self._cfg.max_validation_reasks:
                action = BreakerAction.REASK
            else:
                action = BreakerAction.PAUSE
        elif err_class == ErrorClass.CONTEXT_WINDOW:
            action = BreakerAction.SUMMARIZE if attempt == 0 else BreakerAction.ABORT
        elif err_class == ErrorClass.INFINITE_LOOP:
            action = BreakerAction.PAUSE
        else:
            action = (
                BreakerAction(self._cfg.hard_budget_action)
                if err_class == ErrorClass.BUDGET
                else BreakerAction.ABORT
            )

        if action is BreakerAction.PAUSE and not self._hitl_available:
            action = BreakerAction.ABORT

        if action is BreakerAction.SUMMARIZE and not self._summarizer_available:
            action = BreakerAction.ABORT

        return action

    def backoff_seconds(self, attempt: int) -> float:
        ceiling = min(self._cfg.retry_max_seconds, self._cfg.retry_base_seconds * (2**attempt))
        return random.uniform(0.0, ceiling)  # noqa: S311 - scheduling backoff jitter, not crypto
