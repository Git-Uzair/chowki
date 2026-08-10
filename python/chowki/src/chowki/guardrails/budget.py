from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import structlog

from chowki.errors import BudgetExceeded
from chowki.guardrails.config import GuardrailConfig
from chowki.types import Usage

logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class BudgetWarning:
    dimension: Literal["tokens", "cost"]
    used: float
    limit: float
    fraction: float


class BudgetTracker:
    def __init__(
        self,
        config: GuardrailConfig,
        *,
        on_warning: Callable[[BudgetWarning], None] | None = None,
    ) -> None:
        self.config = config
        self.on_warning = on_warning
        self.total = Usage()
        self._warned: set[str] = set()

    def add(self, usage: Usage) -> None:
        self.total = self.total.merge(usage)

        if not self.config.enabled:
            return

        # Hard limit checks
        if self.config.max_token_budget is not None:
            billable = self.total.billable_tokens
            token_limit = self.config.max_token_budget
            if billable > token_limit:
                raise BudgetExceeded(f"chowki token budget exceeded: {billable} > {token_limit}")

        if self.config.max_cost_usd is not None:
            cost = self.total.cost_usd
            cost_limit = self.config.max_cost_usd
            if cost > cost_limit:
                raise BudgetExceeded(
                    f"chowki cost budget exceeded: ${cost:.4f} > ${cost_limit:.4f}"
                )

        # Soft limit checks
        threshold = self.config.soft_budget_threshold
        if self.config.max_token_budget is not None and "tokens" not in self._warned:
            billable_f = float(self.total.billable_tokens)
            limit_f = float(self.config.max_token_budget)
            fraction = billable_f / limit_f if limit_f > 0 else 1.0
            if fraction >= threshold:
                self._warned.add("tokens")
                warning = BudgetWarning(
                    dimension="tokens",
                    used=billable_f,
                    limit=limit_f,
                    fraction=fraction,
                )
                if self.on_warning is not None:
                    self.on_warning(warning)
                logger.warning(
                    "chowki_budget_warning",
                    dimension="tokens",
                    used=billable_f,
                    limit=limit_f,
                    fraction=fraction,
                )

        if self.config.max_cost_usd is not None and "cost" not in self._warned:
            cost_f = float(self.total.cost_usd)
            limit_f = float(self.config.max_cost_usd)
            fraction = cost_f / limit_f if limit_f > 0 else 1.0
            if fraction >= threshold:
                self._warned.add("cost")
                warning = BudgetWarning(
                    dimension="cost",
                    used=cost_f,
                    limit=limit_f,
                    fraction=fraction,
                )
                if self.on_warning is not None:
                    self.on_warning(warning)
                logger.warning(
                    "chowki_budget_warning",
                    dimension="cost",
                    used=cost_f,
                    limit=limit_f,
                    fraction=fraction,
                )

    def would_exceed(self, usage: Usage) -> bool:
        if not self.config.enabled:
            return False

        projected = self.total.merge(usage)
        if (
            self.config.max_token_budget is not None
            and projected.billable_tokens > self.config.max_token_budget
        ):
            return True
        return (
            self.config.max_cost_usd is not None and projected.cost_usd > self.config.max_cost_usd
        )

    @property
    def remaining_tokens(self) -> int | None:
        if self.config.max_token_budget is None:
            return None
        return self.config.max_token_budget - self.total.billable_tokens

    @property
    def remaining_cost_usd(self) -> float | None:
        if self.config.max_cost_usd is None:
            return None
        return self.config.max_cost_usd - self.total.cost_usd
