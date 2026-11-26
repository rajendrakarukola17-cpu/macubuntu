# Copyright 2026 Celina contributors.
"""Thread-safe deterministic caps with an injectable monotonic clock."""

from __future__ import annotations

import math
import threading
import time
from typing import TYPE_CHECKING

from .contracts import BudgetLimits, BudgetSnapshot

if TYPE_CHECKING:
    from collections.abc import Callable


class BudgetExceeded(RuntimeError):  # noqa: N818  # Public name is specified by the budget API.
    """A proposed action would exceed an explicit budget dimension."""

    def __init__(self, dimension: str, limit: float, current: float) -> None:
        """Record the dimension, cap and attempted usage."""
        self.dimension = dimension
        self.limit = limit
        self.current = current
        super().__init__(f"{dimension} budget exceeded: {current} > {limit}")


class BudgetGuard:
    """Budget counter: snapshot is passive; the first check or consume starts time.

    Counters change only after the wall clock and the proposed usage are accepted.
    The exhausted snapshot flag is true once any cap is reached, including a zero
    write/subprocess cap; it is informational and does not block other dimensions.
    """

    def __init__(self, limits: BudgetLimits, clock: Callable[[], float] = time.monotonic) -> None:
        """Create an unstarted guard; no clock read or I/O occurs here."""
        self._limits = limits
        self._clock = clock
        self._lock = threading.RLock()
        self._started: float | None = None
        self._last: float | None = None
        self._usage = {"iterations": 0, "tokens": 0, "file_writes": 0, "subprocesses": 0}
        self._caps = {
            "iterations": limits.max_iterations,
            "tokens": limits.max_tokens,
            "file_writes": limits.max_file_writes,
            "subprocesses": limits.max_subprocesses,
        }

    def _elapsed(self, *, start: bool) -> float:
        if self._started is None and not start:
            return 0.0
        now = self._clock()
        if isinstance(now, bool) or not isinstance(now, (float, int)) or not math.isfinite(now):
            message = "clock must return a finite number"
            raise ValueError(message)
        if self._last is not None and now < self._last:
            message = "clock moved backwards"
            raise ValueError(message)
        if self._started is None:
            self._started = now
        self._last = now
        return float(now - self._started)

    def _check_wall(self) -> None:
        elapsed = self._elapsed(start=True)
        if elapsed > self._limits.max_wall_seconds:
            dimension = "wall_seconds"
            raise BudgetExceeded(dimension, self._limits.max_wall_seconds, elapsed)

    def _consume(self, dimension: str, n: int) -> None:
        with self._lock:
            self._check_wall()
            if type(n) is not int or n < 0:
                message = "consumption must be a nonnegative integer"
                raise ValueError(message)
            proposed = self._usage[dimension] + n
            cap = self._caps[dimension]
            if proposed > cap:
                raise BudgetExceeded(dimension, cap, proposed)
            self._usage[dimension] = proposed

    def consume_iteration(self) -> None:
        """Consume one iteration if all relevant limits permit it."""
        self._consume("iterations", 1)

    def consume_tokens(self, n: int) -> None:
        """Consume nonnegative token units."""
        self._consume("tokens", n)

    def consume_file_write(self, n: int = 1) -> None:
        """Consume nonnegative file-write units."""
        self._consume("file_writes", n)

    def consume_subprocess(self, n: int = 1) -> None:
        """Consume nonnegative subprocess units."""
        self._consume("subprocesses", n)

    def check_wall_clock(self) -> None:
        """Start if necessary and enforce the wall-clock limit."""
        with self._lock:
            self._check_wall()

    def snapshot(self) -> BudgetSnapshot:
        """Return immutable, exact usage without starting an unstarted guard."""
        with self._lock:
            elapsed = self._elapsed(start=False)
            remaining = {name: cap - self._usage[name] for name, cap in self._caps.items()}
            wall_left = max(0.0, self._limits.max_wall_seconds - elapsed)
            exhausted = wall_left == 0 or any(value == 0 for value in remaining.values())
            return BudgetSnapshot(
                iterations=self._usage["iterations"],
                tokens=self._usage["tokens"],
                wall_seconds=elapsed,
                file_writes=self._usage["file_writes"],
                subprocesses=self._usage["subprocesses"],
                remaining_iterations=remaining["iterations"],
                remaining_tokens=remaining["tokens"],
                remaining_wall_seconds=wall_left,
                remaining_file_writes=remaining["file_writes"],
                remaining_subprocesses=remaining["subprocesses"],
                exhausted=exhausted,
            )
