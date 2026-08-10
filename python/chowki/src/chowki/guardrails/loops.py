"""Multi-tier loop and cycle detection (docs/research/04-guardrails.md)."""

from __future__ import annotations

from collections import Counter, deque
from typing import Final

import structlog

from chowki.errors import InfiniteLoopDetected
from chowki.guardrails.config import GuardrailConfig
from chowki.state.canonical import content_hash
from chowki.types import JSONValue

_SEM_MAX: Final[int] = 512


def normalized_levenshtein(a: str, b: str) -> float:
    """Compute normalized Levenshtein similarity in [0.0, 1.0] capped at 512 chars."""
    a_sub = a[:_SEM_MAX]
    b_sub = b[:_SEM_MAX]
    if a_sub == b_sub:
        return 1.0
    len_a, len_b = len(a_sub), len(b_sub)
    max_len = max(len_a, len_b)
    if max_len == 0:
        return 1.0

    if len_a > len_b:
        a_sub, b_sub = b_sub, a_sub
        len_a, len_b = len_b, len_a

    previous = list(range(len_a + 1))
    current = [0] * (len_a + 1)

    for j, char_b in enumerate(b_sub, start=1):
        current[0] = j
        for i, char_a in enumerate(a_sub, start=1):
            cost = 0 if char_a == char_b else 1
            current[i] = min(
                previous[i] + 1,
                current[i - 1] + 1,
                previous[i - 1] + cost,
            )
        previous, current = current, previous

    dist = previous[len_a]
    return 1.0 - (dist / max_len)


class LoopDetector:
    """Multi-tier loop and cycle detector for tools, prompts, and agent graphs."""

    def __init__(self, config: GuardrailConfig) -> None:
        self._cfg = config
        self._window: deque[str] = deque(maxlen=config.tool_loop_window_size)
        self._texts: deque[str] = deque(maxlen=config.semantic_loop_consecutive)
        self._edges: list[tuple[str, str]] = []
        self._edge_counts: Counter[tuple[str, str]] = Counter()
        self.steps: int = 0
        self.warnings: list[str] = []

    def reset(self) -> None:
        self.steps = 0
        self._window.clear()
        self._texts.clear()
        self._edges.clear()
        self._edge_counts.clear()
        self.warnings.clear()

    def record(self, tool_name: str, kwargs: JSONValue) -> None:
        if not self._cfg.enabled:
            return

        self.steps += 1
        if self.steps > self._cfg.max_steps_per_run:
            raise InfiniteLoopDetected(f"max_steps_per_run={self._cfg.max_steps_per_run} exceeded")

        sig = content_hash({"tool": tool_name, "kwargs": kwargs})
        self._window.append(sig)
        if self._window.count(sig) >= self._cfg.tool_loop_max_repeats:
            raise InfiniteLoopDetected(
                f"tool {tool_name!r} repeated {self._cfg.tool_loop_max_repeats} times "
                f"in a window of {self._cfg.tool_loop_window_size}"
            )

    def record_text(self, text: str) -> None:
        if not self._cfg.enabled:
            return

        self._texts.append(text)
        if len(self._texts) < max(2, self._cfg.semantic_loop_consecutive):
            return

        sims = [
            normalized_levenshtein(self._texts[i], self._texts[i + 1])
            for i in range(len(self._texts) - 1)
        ]
        if all(s >= self._cfg.semantic_loop_pause_threshold for s in sims):
            n = self._cfg.semantic_loop_consecutive
            raise InfiniteLoopDetected(
                f"prompt similarity {sims[-1]:.3f} across {n} consecutive steps"
            )
        if all(s >= self._cfg.semantic_loop_warn_threshold for s in sims):
            msg = f"high prompt similarity ({sims[-1]:.3f}) detected across consecutive steps"
            self.warnings.append(msg)
            logger = structlog.get_logger()
            logger.warning("chowki_semantic_loop_warning", similarity=sims[-1])

    def record_transition(self, src: str, dst: str) -> None:
        if not self._cfg.enabled:
            return

        edge = (src, dst)
        self._edges.append(edge)
        self._edge_counts[edge] += 1
        self._check_cycles()

    def _find_cycle(self, adj: dict[str, list[str]]) -> list[str] | None:
        color: dict[str, int] = {}
        path: list[str] = []

        for start_node in adj:
            if color.get(start_node, 0) != 0:
                continue

            color[start_node] = 1
            path.append(start_node)
            stack = [(start_node, iter(adj.get(start_node, [])))]

            while stack:
                curr, neighbors = stack[-1]
                nxt = next(neighbors, None)
                if nxt is not None:
                    nxt_color = color.get(nxt, 0)
                    if nxt_color == 1:
                        cycle_start = path.index(nxt)
                        return [*path[cycle_start:], nxt]
                    if nxt_color == 0:
                        color[nxt] = 1
                        path.append(nxt)
                        stack.append((nxt, iter(adj.get(nxt, []))))
                else:
                    stack.pop()
                    path.pop()
                    color[curr] = 2

        return None

    def _check_cycles(self) -> None:
        if not any(count >= 2 for count in self._edge_counts.values()):
            return

        adj: dict[str, list[str]] = {}
        for (u, v), count in self._edge_counts.items():
            if count >= 2:
                adj.setdefault(u, []).append(v)

        if not adj:
            return

        cycle = self._find_cycle(adj)
        if cycle:
            cycle_str = " -> ".join(cycle)
            raise InfiniteLoopDetected(f"delegation cycle detected: {cycle_str}")
