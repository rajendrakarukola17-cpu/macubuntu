# Copyright 2026 Celina contributors.
"""L2 external verifier, runner, and gate tests."""

from __future__ import annotations

import subprocess
from dataclasses import FrozenInstanceError
from typing import TYPE_CHECKING

import pytest

from celina_pkg.selfimprove.budget import BudgetExceeded, BudgetGuard
from celina_pkg.selfimprove.contracts import BudgetLimits, TaskSpec, VerifierResult
from celina_pkg.selfimprove.flags import SelfImproveFlags
from celina_pkg.selfimprove.l2.gate import VerificationGate
from celina_pkg.selfimprove.l2.runner import OUTPUT_LIMIT, run_allowlisted_command
from celina_pkg.selfimprove.l2.verifiers import (
    BanditVerifier,
    MypyVerifier,
    PytestVerifier,
    RuffVerifier,
)

if TYPE_CHECKING:
    from pathlib import Path


class FakeExecutor:
    """Fake subprocess boundary with deterministic outcomes."""

    def __init__(self, code: int = 0, output: str = '{"results": []}', failure: str = "") -> None:
        """Set a canned response."""
        self.code = code
        self.output = output
        self.failure = failure
        self.calls: list[list[str]] = []

    def __call__(
        self,
        argv: list[str],
        *,
        cwd: Path,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        """Capture the invocation and supply the canned response."""
        assert cwd.is_dir()
        self.calls.append(argv)
        if self.failure == "missing":
            raise FileNotFoundError(argv[0])
        if self.failure == "timeout":
            raise subprocess.TimeoutExpired(argv, timeout)
        return subprocess.CompletedProcess(argv, self.code, self.output, "")


def budget(subprocesses: int = 8) -> BudgetGuard:
    """Make a generous deterministic budget."""
    return BudgetGuard(BudgetLimits(10, 100, 60.0, 0, subprocesses), clock=lambda: 0.0)


def active() -> SelfImproveFlags:
    """Explicitly opt in to verifier execution."""
    return SelfImproveFlags(enabled=True, verifiers=True)


@pytest.mark.parametrize("cls", [PytestVerifier, RuffVerifier, MypyVerifier, BanditVerifier])
def test_concrete_verifiers_pass_fail_timeout_missing(tmp_path: Path, cls: type) -> None:
    """All four verifiers use the executor and report normal and error outcomes."""
    for failure, code, expected in [
        ("", 0, "pass"),
        ("", 1, "fail"),
        ("timeout", 0, "error"),
        ("missing", 0, "error"),
    ]:
        executor = FakeExecutor(code=code, failure=failure)
        verifier = cls(active(), budget(), tmp_path, executor=executor)
        result = verifier.verify(TaskSpec("t", "goal"))
        assert result.status == expected
        assert len(executor.calls) == 1
        if failure:
            assert failure in result.detail.lower()
        with pytest.raises(FrozenInstanceError):
            result.status = "fail"


@pytest.mark.parametrize("cls", [PytestVerifier, RuffVerifier, MypyVerifier, BanditVerifier])
def test_disabled_flags_block_before_subprocess(tmp_path: Path, cls: type) -> None:
    """Master and individual opt-in both gate execution."""
    for flags in [SelfImproveFlags(), SelfImproveFlags(enabled=True)]:
        executor = FakeExecutor()
        guard = budget()
        result = cls(flags, guard, tmp_path, executor=executor).verify(TaskSpec("t", "goal"))
        assert result.status != "pass"
        assert not executor.calls
        assert guard.snapshot().subprocesses == 0


def test_runner_rejects_unknown_and_unsafe_directories(tmp_path: Path) -> None:
    """Unlisted tools and escaped, missing, or non-directory cwd are refused."""
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    fake = FakeExecutor()
    for name, cwd in [
        ("sh", root),
        ("/usr/bin/pytest", root),
        ("pytest", root / ".."),
        ("pytest", root / "escape"),
        ("pytest", root / "missing"),
    ]:
        with pytest.raises(ValueError, match=r"allowed|escapes|does not exist|directories"):
            run_allowlisted_command(
                name,
                (),
                cwd=cwd,
                workspace_root=root,
                timeout=1,
                executor=fake,
            )
    assert not fake.calls


def test_runner_truncates_outputs_and_handles_timeout(tmp_path: Path) -> None:
    """Runner returns bounded output and a distinct timeout result."""
    fake = FakeExecutor(output="x" * 20000)
    result = run_allowlisted_command("pytest", (), cwd=tmp_path, timeout=1, executor=fake)
    assert len(result.stdout) <= OUTPUT_LIMIT
    assert "[TRUNCATED]" in result.stdout
    timed = run_allowlisted_command(
        "pytest",
        (),
        cwd=tmp_path,
        timeout=1,
        executor=FakeExecutor(failure="timeout"),
    )
    assert timed.timed_out
    assert timed.returncode != 0


def test_bandit_rejects_high_medium_and_invalid_json(tmp_path: Path) -> None:
    """Bandit requires valid JSON and denies medium or high findings."""
    for severity in ["HIGH", "MEDIUM"]:
        fake = FakeExecutor(output='{"results": [{"issue_severity": "' + severity + '"}]}')
        result = BanditVerifier(active(), budget(), tmp_path, executor=fake).verify(
            TaskSpec("t", "goal"),
        )
        assert result.status != "pass"
        assert "-ll" in fake.calls[0]
    fake = FakeExecutor(output="not json")
    assert (
        BanditVerifier(active(), budget(), tmp_path, executor=fake)
        .verify(TaskSpec("t", "goal"))
        .status
        != "pass"
    )


class StubVerifier:
    """A controlled implementation of the L0 verifier protocol for gate tests."""

    def __init__(self, outcome: str) -> None:
        """Remember the desired outcome."""
        self.outcome = outcome
        self.calls = 0

    def verify(self, task: TaskSpec) -> VerifierResult:
        """Return the requested result or simulate an exception."""
        assert task.task_id
        self.calls += 1
        if self.outcome == "raise":
            message = "synthetic failure"
            raise RuntimeError(message)
        return VerifierResult("pytest", self.outcome, 1.0 if self.outcome == "pass" else 0.0)


def test_gate_empty_and_disabled_are_blocked() -> None:
    """An empty suite or disabled feature can never mark work complete."""
    task = TaskSpec("t", "goal")
    gate = VerificationGate(active(), budget(), ())
    decision = gate.verify(task)
    assert not decision.passed
    assert decision.blocked_reason == "no_verifiers_configured"
    stub = StubVerifier("pass")
    for flags in [SelfImproveFlags(), SelfImproveFlags(enabled=True)]:
        assert not VerificationGate(flags, budget(), (stub,)).verify(task).passed
    assert stub.calls == 0


@pytest.mark.parametrize("fail_fast", [True, False])
@pytest.mark.parametrize("outcome", ["fail", "error", "raise"])
def test_gate_fails_on_any_bad_verifier(fail_fast: bool, outcome: str) -> None:  # noqa: FBT001  # Pytest parameter.
    """Failure or exception blocks success, with controlled continuation."""
    first = StubVerifier(outcome)
    second = StubVerifier("pass")
    decision = VerificationGate(active(), budget(), (first, second), fail_fast=fail_fast).verify(
        TaskSpec("t", "goal"),
    )
    assert not decision.passed
    assert first.calls == 1
    assert second.calls == (0 if fail_fast else 1)


def test_gate_passes_only_when_all_pass() -> None:
    """All configured verifiers must execute and pass."""
    first, second = StubVerifier("pass"), StubVerifier("pass")
    decision = VerificationGate(active(), budget(), (first, second)).verify(TaskSpec("t", "goal"))
    assert decision.passed
    assert (first.calls, second.calls) == (1, 1)


def test_budget_exhaustion_stops_suite_even_without_fail_fast(tmp_path: Path) -> None:
    """No later verifier runs once the subprocess cap has been exhausted."""
    guard = budget(1)
    first, second = FakeExecutor(), FakeExecutor()
    a = PytestVerifier(active(), guard, tmp_path, executor=first)
    b = RuffVerifier(active(), guard, tmp_path, executor=second)
    decision = VerificationGate(active(), guard, (a, b), fail_fast=False).verify(
        TaskSpec("t", "goal"),
    )
    assert not decision.passed
    assert decision.blocked_reason == "budget_exceeded"
    assert len(first.calls) == 1
    assert not second.calls
    assert guard.snapshot().subprocesses == 1


def test_real_pytest_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A real allowlisted pytest runs a tiny test in a temporary directory."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TARGET_HOME", raising=False)
    (tmp_path / "test_smoke.py").write_text("def test_ok():\n    assert 1 == 1\n")
    result = PytestVerifier(active(), budget(), tmp_path).verify(TaskSpec("t", "goal"))
    assert result.status == "pass", result.detail


def test_gate_trusts_configured_protocol_without_independent_attestation() -> None:
    """Trusted configuration, not the protocol, establishes verifier provenance."""
    reporter = StubVerifier("pass")
    guard = budget()
    decision = VerificationGate(active(), guard, (reporter,)).verify(TaskSpec("t", "goal"))
    assert decision.passed
    assert guard.snapshot().subprocesses == 0


def test_gate_does_not_accept_caller_supplied_results() -> None:
    """A VerifierResult is data, not a configured executable verifier."""
    result = VerifierResult("pytest", "pass", 1.0)
    decision = VerificationGate(active(), budget(), (result,)).verify(TaskSpec("t", "goal"))
    assert not decision.passed


def test_verifier_checks_wall_clock_after_execution(tmp_path: Path) -> None:
    """A verifier cannot pass after the shared wall-clock budget expires."""
    elapsed = [0.0]
    guard = BudgetGuard(BudgetLimits(2, 3, 1.0, 0, 1), clock=lambda: elapsed[0])

    class SlowExecutor(FakeExecutor):
        def __call__(
            self,
            argv: list[str],
            *,
            cwd: Path,
            timeout: float,
        ) -> subprocess.CompletedProcess[str]:
            elapsed[0] = 2.0
            return super().__call__(argv, cwd=cwd, timeout=timeout)

    verifier = PytestVerifier(active(), guard, tmp_path, executor=SlowExecutor())

    with pytest.raises(BudgetExceeded, match="wall_seconds"):
        verifier.verify(TaskSpec("t", "goal"))


def test_expired_budget_precedes_missing_executable(tmp_path: Path) -> None:
    """Wall expiry during a failing invocation takes precedence over tool errors."""
    elapsed = [0.0]
    guard = BudgetGuard(BudgetLimits(2, 3, 1.0, 0, 1), clock=lambda: elapsed[0])

    class MissingAfterDelay(FakeExecutor):
        def __call__(
            self,
            argv: list[str],
            *,
            cwd: Path,
            timeout: float,
        ) -> subprocess.CompletedProcess[str]:
            assert cwd.is_dir()
            assert timeout > 0
            elapsed[0] = 2.0
            raise FileNotFoundError(argv[0])

    verifier = PytestVerifier(active(), guard, tmp_path, executor=MissingAfterDelay())
    with pytest.raises(BudgetExceeded, match="wall_seconds"):
        verifier.verify(TaskSpec("t", "goal"))


@pytest.mark.parametrize("fail_fast", [True, False])
def test_gate_exhaustion_during_verifier_exception_stops_suite(fail_fast: bool) -> None:  # noqa: FBT001  # Pytest parameter.
    """A late verifier exception cannot hide wall budget exhaustion."""
    elapsed = [0.0]
    guard = BudgetGuard(BudgetLimits(2, 3, 1.0, 0, 1), clock=lambda: elapsed[0])

    class LateError(StubVerifier):
        def verify(self, task: TaskSpec) -> VerifierResult:
            assert task.task_id
            elapsed[0] = 2.0
            message = "synthetic"
            raise RuntimeError(message)

    second = StubVerifier("pass")
    decision = VerificationGate(
        active(),
        guard,
        (LateError("raise"), second),
        fail_fast=fail_fast,
    ).verify(TaskSpec("t", "goal"))
    assert decision.blocked_reason == "budget_exceeded"
    assert not decision.passed
    assert second.calls == 0


@pytest.mark.parametrize("timeout", [0.0, -1.0, float("nan"), True])
def test_runner_rejects_invalid_timeout(tmp_path: Path, timeout: float) -> None:
    """Timeouts must be finite positive numbers, not booleans."""
    with pytest.raises(ValueError, match="timeout"):
        run_allowlisted_command(
            "pytest", (), cwd=tmp_path, timeout=timeout, executor=FakeExecutor(),
        )


def test_runner_rejects_file_directories(tmp_path: Path) -> None:
    """A file is neither a valid cwd nor workspace root."""
    file_path = tmp_path / "not-a-directory"
    file_path.write_text("x")
    with pytest.raises(ValueError, match="directories"):
        run_allowlisted_command("pytest", (), cwd=file_path, timeout=1, executor=FakeExecutor())
    with pytest.raises(ValueError, match="directories"):
        run_allowlisted_command(
            "pytest", (), cwd=tmp_path, workspace_root=file_path,
            timeout=1, executor=FakeExecutor(),
        )


def test_timeout_output_bytes_are_decoded_and_bounded(tmp_path: Path) -> None:
    """Byte-valued timeout diagnostics are normalized and truncated."""

    def fake(argv: list[str], *, cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
        assert cwd.is_dir()
        raise subprocess.TimeoutExpired(argv, timeout, output=b"x" * 5000, stderr=b"\xff")

    raw = run_allowlisted_command("pytest", (), cwd=tmp_path, timeout=1, executor=fake)
    assert raw.timed_out
    assert len(raw.stdout) <= OUTPUT_LIMIT
    assert "[TRUNCATED]" in raw.stdout
    assert "\ufffd" in raw.stderr


@pytest.mark.parametrize(
    "output",
    ['{"results": {}}', '{"results": [null]}', '{"results": [{"issue_severity": "UNKNOWN"}]}'],
)
def test_bandit_invalid_result_shapes_fail_closed(tmp_path: Path, output: str) -> None:
    """Malformed Bandit results are errors, never successful checks."""
    result = BanditVerifier(
        active(), budget(), tmp_path, executor=FakeExecutor(output=output),
    ).verify(TaskSpec("t", "goal"))
    assert result.status == "error"


def test_unexpected_executor_failure_is_error(tmp_path: Path) -> None:
    """An executor exception returns an error rather than a pass."""

    def fake(argv: list[str], *, cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
        assert argv
        assert cwd.is_dir()
        assert timeout > 0
        message = "synthetic executor failure"
        raise RuntimeError(message)

    result = PytestVerifier(active(), budget(), tmp_path, executor=fake).verify(
        TaskSpec("t", "goal"),
    )
    assert result.status == "error"


def test_gate_rejects_bad_return_type() -> None:
    """A verifier returning something other than the L0 result cannot pass."""

    class InvalidResult(StubVerifier):
        def verify(self, task: TaskSpec) -> VerifierResult:
            assert task.task_id
            return None

    decision = VerificationGate(active(), budget(), (InvalidResult("pass"),)).verify(
        TaskSpec("t", "goal"),
    )
    assert not decision.passed
    assert decision.blocked_reason == "verifier_failed"
