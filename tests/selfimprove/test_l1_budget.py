# Copyright 2026 Celina contributors.
"""Deterministic budget-boundary tests."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError

import pytest

from celina_pkg.selfimprove.budget import BudgetExceeded, BudgetGuard
from celina_pkg.selfimprove.contracts import BudgetLimits


class FakeClock:
    """An explicitly advanced monotonic test clock."""

    def __init__(self) -> None:
        """Initialize the clock."""
        self.value = 100.0
        self.calls = 0

    def __call__(self) -> float:
        """Read the fake clock without advancing it."""
        self.calls += 1
        return self.value


@pytest.fixture
def limits() -> BudgetLimits:
    """Small positive limits with read/write capacity."""
    return BudgetLimits(2, 3, 5.0, 2, 2)


@pytest.mark.parametrize(
    "args",
    [
        (0, 1, 1.0, 0, 0),
        (1, 0, 1.0, 0, 0),
        (1, 1, 0.0, 0, 0),
        (1, 1, 1.0, -1, 0),
        (1, 1, 1.0, 0, -1),
        (1, 1, float("inf"), 0, 0),
    ],
)
def test_invalid_limits_are_rejected(args: tuple[object, ...]) -> None:
    """Invalid maxima cannot construct a budget."""
    with pytest.raises(ValueError, match="max_"):
        BudgetLimits(*args)


@pytest.mark.parametrize(
    ("method", "attribute", "cap", "dimension"),
    [
        ("consume_iteration", "iterations", 2, "iterations"),
        ("consume_tokens", "tokens", 3, "tokens"),
        ("consume_file_write", "file_writes", 2, "file_writes"),
        ("consume_subprocess", "subprocesses", 2, "subprocesses"),
    ],
)
def test_exact_cap_and_overshoot_preserve_counters(
    limits: BudgetLimits,
    method: str,
    attribute: str,
    cap: int,
    dimension: str,
) -> None:
    """Each dimension accepts its cap and rejects the next unit atomically."""
    guard = BudgetGuard(limits, clock=FakeClock())
    consume = getattr(guard, method)
    for _ in range(cap):
        consume() if method != "consume_tokens" else consume(1)
    assert getattr(guard.snapshot(), attribute) == cap
    with pytest.raises(BudgetExceeded) as exc:
        consume() if method != "consume_tokens" else consume(1)
    assert exc.value.dimension == dimension
    assert exc.value.limit == cap
    assert exc.value.current == cap + 1
    assert getattr(guard.snapshot(), attribute) == cap
    assert guard.snapshot().exhausted


@pytest.mark.parametrize("method", ["consume_tokens", "consume_file_write", "consume_subprocess"])
def test_negative_invalid_and_multiunit_overshoot(limits: BudgetLimits, method: str) -> None:
    """Negative and invalid units fail; overshoots never advance counters."""
    guard = BudgetGuard(limits, clock=FakeClock())
    consume = getattr(guard, method)
    before = guard.snapshot()
    with pytest.raises(ValueError, match="nonnegative integer"):
        consume(-1)
    with pytest.raises(ValueError, match="nonnegative integer"):
        consume(True)  # noqa: FBT003  # Verify booleans cannot be counted as integers.
    with pytest.raises(ValueError, match="nonnegative integer"):
        consume(1.5)
    with pytest.raises(BudgetExceeded):
        consume(10)
    assert guard.snapshot() == before


def test_zero_io_budgets_are_read_only() -> None:
    """A zero write or subprocess cap immediately denies positive use."""
    guard = BudgetGuard(BudgetLimits(1, 1, 1.0, 0, 0), clock=FakeClock())
    guard.consume_file_write(0)
    guard.consume_subprocess(0)
    with pytest.raises(BudgetExceeded, match="file_writes"):
        guard.consume_file_write()
    with pytest.raises(BudgetExceeded, match="subprocesses"):
        guard.consume_subprocess()
    assert guard.snapshot().exhausted


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("check_wall_clock", ()),
        ("consume_iteration", ()),
        ("consume_tokens", (1,)),
        ("consume_file_write", (1,)),
        ("consume_subprocess", (1,)),
    ],
)
def test_wall_boundary_and_late_consumers(
    limits: BudgetLimits, method: str, args: tuple[int, ...],
) -> None:
    """Elapsed == cap is allowed, elapsed > cap denies every operation."""
    clock = FakeClock()
    guard = BudgetGuard(limits, clock=clock)
    guard.check_wall_clock()
    clock.value += 5.0
    guard.check_wall_clock()
    before = guard.snapshot()
    clock.value += 0.001
    with pytest.raises(BudgetExceeded) as exc:
        getattr(guard, method)(*args)
    assert exc.value.dimension == "wall_seconds"
    assert exc.value.limit == limits.max_wall_seconds
    assert exc.value.current > limits.max_wall_seconds
    after = guard.snapshot()
    assert after.iterations == before.iterations
    assert after.tokens == before.tokens
    assert after.file_writes == before.file_writes
    assert after.subprocesses == before.subprocesses
    assert after.exhausted


def test_unstarted_snapshot_auto_start_and_immutable_headroom(limits: BudgetLimits) -> None:
    """Snapshot before use does not start time; first consumption starts it."""
    clock = FakeClock()
    guard = BudgetGuard(limits, clock=clock)
    snap = guard.snapshot()
    assert clock.calls == 0
    assert (
        snap.iterations,
        snap.tokens,
        snap.wall_seconds,
        snap.file_writes,
        snap.subprocesses,
    ) == (0, 0, 0.0, 0, 0)
    assert (
        snap.remaining_iterations,
        snap.remaining_tokens,
        snap.remaining_wall_seconds,
        snap.remaining_file_writes,
        snap.remaining_subprocesses,
    ) == (2, 3, 5.0, 2, 2)
    assert not snap.exhausted
    with pytest.raises(FrozenInstanceError):
        snap.tokens = 10
    guard.consume_iteration()
    guard.consume_tokens(2)
    guard.consume_file_write()
    guard.consume_subprocess()
    clock.value += 1.25
    snap = guard.snapshot()
    assert (
        snap.iterations,
        snap.tokens,
        snap.wall_seconds,
        snap.file_writes,
        snap.subprocesses,
    ) == (1, 2, 1.25, 1, 1)
    assert (
        snap.remaining_iterations,
        snap.remaining_tokens,
        snap.remaining_wall_seconds,
        snap.remaining_file_writes,
        snap.remaining_subprocesses,
    ) == (1, 1, 3.75, 1, 1)
    assert not snap.exhausted


def test_invalid_and_backward_clocks_fail_closed(limits: BudgetLimits) -> None:
    """Invalid or backwards clocks cannot be used to hide elapsed time."""
    clock = FakeClock()
    guard = BudgetGuard(limits, clock=clock)
    guard.check_wall_clock()
    clock.value = 99.0
    with pytest.raises(ValueError, match="clock"):
        guard.check_wall_clock()
    clock.value = float("nan")
    with pytest.raises(ValueError, match="clock"):
        guard.snapshot()


def test_parallel_consumption_cannot_exceed_limit(limits: BudgetLimits) -> None:
    """Concurrent users cannot race past an iteration cap."""
    guard = BudgetGuard(limits, clock=FakeClock())

    def attempt() -> bool:
        try:
            guard.consume_iteration()
        except BudgetExceeded:
            return False
        return True

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _: attempt(), range(20)))
    assert sum(outcomes) == limits.max_iterations
    assert guard.snapshot().iterations == limits.max_iterations


@pytest.mark.parametrize("method", ["consume_tokens", "consume_file_write", "consume_subprocess"])
def test_expired_clock_takes_precedence_over_invalid_consumption(
    limits: BudgetLimits, method: str,
) -> None:
    """Every consume call enforces wall time, even with an invalid amount."""
    clock = FakeClock()
    guard = BudgetGuard(limits, clock=clock)
    guard.check_wall_clock()
    clock.value += limits.max_wall_seconds + 1
    with pytest.raises(BudgetExceeded, match="wall_seconds"):
        getattr(guard, method)(-1)
