# Copyright 2026 Celina contributors.
"""Offline L7 patcher gating, approval and review bundle integration tests."""

from __future__ import annotations

import json
import os
import stat
from typing import TYPE_CHECKING

import pytest

from celina_pkg.evidence import EvidenceIntegrityError, EvidenceLedger
from celina_pkg.selfimprove.budget import BudgetGuard
from celina_pkg.selfimprove.contracts import BudgetLimits, EvalReport, TaskSpec, VerifierResult
from celina_pkg.selfimprove.flags import SelfImproveFlags
from celina_pkg.selfimprove.l2.gate import VerificationGate
from celina_pkg.selfimprove.l3.runner import EvalRunner
from celina_pkg.selfimprove.l3.stats import EvalRun, TaskOutcome
from celina_pkg.selfimprove.l3.suite import EvalTask
from celina_pkg.selfimprove.l7._audit import audit_payloads, record_audit
from celina_pkg.selfimprove.l7._bundle_io import _publish
from celina_pkg.selfimprove.l7.patcher import SafeSelfPatcher
from celina_pkg.selfimprove.l7.types import (
    PatchApproval,
    PatchPolicyConfig,
    PatchProposal,
    PolicyCheckResult,
)
from celina_pkg.selfimprove.l7.worktree import (
    ThrowawayWorktreeSession,
    WorktreeCleanupError,
    WorktreeManager,
)
from celina_pkg.selfimprove.ledger_bridge import LedgerBridge

from .test_l7_worktree import PATCH, git, policy_manifest, state

if TYPE_CHECKING:
    from pathlib import Path

FLAGS = SelfImproveFlags(enabled=True, patcher=True, verifiers=True, eval_harness=True)
FILE_MODE = 0o600
DIR_MODE = 0o700
LIMITS = BudgetLimits(20, 100, 60, 50, 30)
TASK = TaskSpec("patch", "Verify candidate")
PROPOSAL = PatchProposal("p1", PATCH, "change file")


@pytest.fixture
def local_repo(tmp_path: Path) -> Path:
    """Create an isolated local repository with one committed file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    (repo / "hello.txt").write_text("old\n")
    git(repo, "add", "hello.txt")
    git(repo, "commit", "-qm", "initial")
    return repo


class Verifier:
    """Deterministic verifier reads the actual patched worktree."""

    def __init__(self, path: Path, status: str = "pass") -> None:
        """Exercise   init  ."""
        self.path, self.status = path, status

    def verify(self, task: TaskSpec) -> VerifierResult:
        """Exercise verify."""
        assert (self.path / "hello.txt").read_text() == "new\n"
        return VerifierResult(task.task_id, self.status, 1.0)


class GateFactory:
    """Create a real L2 gate for each worktree."""

    def __init__(self, status: str = "pass") -> None:
        """Exercise   init  ."""
        self.status, self.calls = status, 0

    def create(self, worktree: Path, budget: BudgetGuard) -> VerificationGate:
        """Exercise create."""
        self.calls += 1
        return VerificationGate(FLAGS, budget, [Verifier(worktree, self.status)])


class Approval:
    """A fake human decision optionally bound to a different digest."""

    def __init__(self, *, approved: bool = True, mismatch: bool = False) -> None:
        """Exercise   init  ."""
        self.approved, self.mismatch, self.calls = approved, mismatch, 0

    def approve(self, _proposal: PatchProposal, bundle_hash: str) -> PatchApproval:
        """Exercise approve."""
        self.calls += 1
        return PatchApproval(self.approved, "0" * 64 if self.mismatch else bundle_hash, "reviewed")


class EvalFactory:
    """Create a real L3 runner returning one deterministic evaluation result."""

    def __init__(self, status: str) -> None:
        """Exercise   init  ."""
        self.status, self.calls = status, 0

    def create(self, worktree: Path, budget: BudgetGuard) -> EvalRunner:
        """Exercise create."""
        self.calls += 1

        def evaluate(task: EvalTask) -> EvalReport:
            assert (worktree / "hello.txt").read_text() == "new\n"
            return EvalReport(task.spec.task_id, self.status, 1.0, 1)

        return EvalRunner(FLAGS, budget, evaluate)


def make_patcher(  # noqa: PLR0913  # Test fixture explicitly configures all gates.
    tmp_path: Path,
    repo: Path,
    *,
    flags: SelfImproveFlags = FLAGS,
    approval: Approval | None = None,
    gate: GateFactory | None = None,
    budget: BudgetGuard | None = None,
    baseline: EvalRun | None = None,
    evaluator: EvalFactory | None = None,
    tasks: tuple[EvalTask, ...] = (),
    significant: bool = False,
) -> tuple[SafeSelfPatcher, EvidenceLedger, GateFactory]:
    """Bind only explicit temporary paths and host-owned dependencies."""
    actual_budget = budget or BudgetGuard(LIMITS)
    manager = WorktreeManager(
        repo,
        tmp_path / "worktrees",
        policy_manifest(tmp_path),
        actual_budget,
        PatchPolicyConfig(),
    )
    ledger = EvidenceLedger(tmp_path / "events", anchor_path=tmp_path / "anchor")
    factory = gate or GateFactory()
    patcher = SafeSelfPatcher(
        flags,
        manager,
        factory,
        bundle_dir=tmp_path / "bundles",
        approval_gate=approval,
        bridge=LedgerBridge(ledger),
        baseline=baseline,
        eval_factory=evaluator,
        eval_tasks=tasks,
        require_statistical_significance=significant,
    )
    return patcher, ledger, factory


@pytest.mark.parametrize("flags", [SelfImproveFlags(), SelfImproveFlags(enabled=True)])
def test_disabled_no_io_or_callbacks(
    tmp_path: Path,
    local_repo: Path,
    flags: SelfImproveFlags,
) -> None:
    """Exercise disabled no io or callbacks."""
    approval = Approval()
    patcher, _ledger, gate = make_patcher(tmp_path, local_repo, flags=flags, approval=approval)
    (tmp_path / "manifest.json").unlink()
    result = patcher.run(PROPOSAL, TASK)
    assert result.status == "blocked_by_flag"
    assert not result.approved
    assert result.bundle is None
    assert result.budget.subprocesses == result.budget.file_writes == 0
    assert not (tmp_path / "worktrees").exists()
    assert not (tmp_path / "bundles").exists()
    assert not (tmp_path / "events").exists()
    assert gate.calls == approval.calls == 0


@pytest.mark.parametrize(
    "path",
    [
        "features/celina/celina_pkg/approval.py",
        "features/celina/celina_pkg/evidence.py",
        "selfimprove/protected_hashes.json",
        ".github/CODEOWNERS",
        "tests/selfimprove/test_protected_core.py",
    ],
)
def test_policy_rejected_before_worktree(tmp_path: Path, local_repo: Path, path: str) -> None:
    """Exercise policy rejected before worktree."""
    proposal = PatchProposal("protected", PATCH.replace("hello.txt", path), "bad")
    patcher, _, gate = make_patcher(tmp_path, local_repo, approval=Approval())
    result = patcher.run(proposal, TASK)
    assert result.status == "rejected_policy"
    assert result.budget.subprocesses == 0
    assert not (tmp_path / "worktrees").exists()
    assert gate.calls == 0


@pytest.mark.parametrize(
    ("approval", "expected"),
    [
        (None, "rejected_no_approval_gate"),
        (Approval(approved=False), "rejected_by_approver"),
        (Approval(approved=True, mismatch=True), "rejected_approval_mismatch"),
    ],
)
def test_approval_refusal_never_exports(
    tmp_path: Path,
    local_repo: Path,
    approval: Approval | None,
    expected: str,
) -> None:
    """Exercise approval refusal never exports."""
    before = state(local_repo)
    patcher, _, _ = make_patcher(tmp_path, local_repo, approval=approval)
    result = patcher.run(PROPOSAL, TASK)
    assert result.status == expected
    assert not result.approved
    assert result.bundle is None
    assert state(local_repo) == before
    assert not (tmp_path / "bundles").exists()
    assert not list((tmp_path / "worktrees").iterdir())


def test_success_exports_private_bound_bundle_and_audit(tmp_path: Path, local_repo: Path) -> None:
    """Exercise success exports private bound bundle and audit."""
    before = state(local_repo)
    approval = Approval()
    patcher, ledger, gate = make_patcher(tmp_path, local_repo, approval=approval)
    outcome = patcher.run(PROPOSAL, TASK)
    assert outcome.status == "bundle_ready"
    assert outcome.approved
    assert outcome.bundle is not None
    bundle = outcome.bundle
    assert bundle.patch_path.read_text() == PATCH
    metadata = json.loads(bundle.metadata_path.read_text())
    assert metadata["bundle_hash"] == bundle.bundle_hash
    assert metadata["proposal_id"] == "p1"
    assert metadata["touched_files"] == ["hello.txt"]
    assert stat.S_IMODE(bundle.patch_path.stat().st_mode) == FILE_MODE
    assert stat.S_IMODE(bundle.metadata_path.stat().st_mode) == FILE_MODE
    assert stat.S_IMODE(bundle.patch_path.parent.stat().st_mode) == DIR_MODE
    assert ledger.verify()[0]
    records = [json.loads(line) for line in (tmp_path / "events").read_text().splitlines()]
    audit = " ".join(record["result_preview"] for record in records)
    assert "[TRUNCATED]" not in audit
    assert "p1" in audit
    previews = [json.loads(record["result_preview"]) for record in records]
    hash_chunks = next(
        row["bundle_hash_hex_chunks"] for row in previews if "bundle_hash_hex_chunks" in row
    )
    assert "".join(hash_chunks) == bundle.bundle_hash
    assert "hello.txt" in audit
    assert "bundle_ready" in audit
    commit_chunks = next(
        row["base_commit_hex_chunks"] for row in previews if "base_commit_hex_chunks" in row
    )
    assert "".join(commit_chunks) == metadata["base_commit"]
    assert approval.calls == gate.calls == 1
    assert state(local_repo) == before
    assert not list((tmp_path / "worktrees").iterdir())
    assert str(tmp_path / "worktrees").encode() not in git(
        local_repo,
        "worktree",
        "list",
        "--porcelain",
    )


def test_failed_l2_never_calls_approval(tmp_path: Path, local_repo: Path) -> None:
    """Exercise failed l2 never calls approval."""
    approval = Approval()
    patcher, _, _ = make_patcher(tmp_path, local_repo, approval=approval, gate=GateFactory("fail"))
    result = patcher.run(PROPOSAL, TASK)
    assert result.status == "failed_verification"
    assert approval.calls == 0
    assert not (tmp_path / "bundles").exists()
    assert not list((tmp_path / "worktrees").iterdir())


@pytest.mark.parametrize(
    "case",
    [
        ("pass", "fail", True, False, "failed_eval_regression"),
        ("pass", "fail", False, False, "failed_eval_regression"),
        ("pass", "pass", False, True, "failed_eval_regression"),
        ("pass", "pass", False, False, "bundle_ready"),
    ],
)
def test_l3_regression_and_significance(
    tmp_path: Path,
    local_repo: Path,
    case: tuple[str, str, bool, bool, str],
) -> None:
    """Critical regressions, negative deltas and required significance veto export."""
    old, new, critical, significant, expected = case
    task = EvalTask(TaskSpec("eval", "goal"), "routing", "expected", critical)
    baseline = EvalRun("baseline", (TaskOutcome(EvalReport("eval", old, 1.0, 1), critical),))
    evaluation, approval = EvalFactory(new), Approval()
    patcher, _, _ = make_patcher(
        tmp_path,
        local_repo,
        approval=approval,
        baseline=baseline,
        evaluator=evaluation,
        tasks=(task,),
        significant=significant,
    )
    result = patcher.run(PROPOSAL, TASK)
    assert result.status == expected
    assert evaluation.calls == 1
    assert approval.calls == int(expected == "bundle_ready")
    assert not list((tmp_path / "worktrees").iterdir())


def test_budget_exhaustion_before_and_during_worktree(tmp_path: Path, local_repo: Path) -> None:
    """Exercise budget exhaustion before and during worktree."""
    budget = BudgetGuard(BudgetLimits(10, 100, 60, 50, 6))
    patcher, _, _ = make_patcher(tmp_path, local_repo, approval=Approval(), budget=budget)
    result = patcher.run(PROPOSAL, TASK)
    assert result.status == "failed_budget"
    assert result.budget_dimension == "subprocesses"
    assert not list((tmp_path / "worktrees").iterdir())
    assert str(tmp_path / "worktrees").encode() not in git(
        local_repo,
        "worktree",
        "list",
        "--porcelain",
    )


def test_symlink_bundle_dir_is_refused(tmp_path: Path, local_repo: Path) -> None:
    """Exercise symlink bundle dir is refused."""
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    (tmp_path / "bundles").symlink_to(outside, target_is_directory=True)
    patcher, _, _ = make_patcher(tmp_path, local_repo, approval=Approval())
    result = patcher.run(PROPOSAL, TASK)
    assert not result.approved
    assert result.bundle is None
    assert not list(outside.iterdir())


def test_incomplete_eval_configuration_and_required_eval_fail_closed(
    tmp_path: Path,
    local_repo: Path,
) -> None:
    """A baseline without a runner or an unsatisfied significance requirement vetoes export."""
    baseline = EvalRun(
        "baseline",
        (TaskOutcome(EvalReport("eval", "pass", 1.0, 1), security_critical=False),),
    )
    patcher, _, _ = make_patcher(tmp_path, local_repo, approval=Approval(), baseline=baseline)
    assert patcher.run(PROPOSAL, TASK).status == "failed_eval_regression"
    patcher, _, _ = make_patcher(tmp_path, local_repo, approval=Approval(), significant=True)
    assert patcher.run(PROPOSAL, TASK).status == "failed_eval_regression"


def test_factory_and_approval_exceptions_cleanup_and_fail_closed(
    tmp_path: Path,
    local_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected host callback errors never export and leave no detached trees."""
    gate, approval = GateFactory(), Approval()
    patcher, _, _ = make_patcher(tmp_path, local_repo, approval=approval, gate=gate)

    def fail(*_args: object, **_kwargs: object) -> None:
        message = "callback failed"
        raise RuntimeError(message)

    monkeypatch.setattr(gate, "create", fail)
    assert patcher.run(PROPOSAL, TASK).status == "failed_error"
    monkeypatch.undo()
    monkeypatch.setattr(approval, "approve", fail)
    assert patcher.run(PROPOSAL, TASK).status == "failed_error"
    assert not (tmp_path / "bundles").exists()
    assert not list((tmp_path / "worktrees").iterdir())


def test_audit_failure_removes_bundle(
    tmp_path: Path,
    local_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ledger failure removes published files and never reports a ready bundle."""
    patcher, _, _ = make_patcher(tmp_path, local_repo, approval=Approval())

    def fail(*_args: object, **_kwargs: object) -> None:
        message = "ledger unavailable"
        raise RuntimeError(message)

    monkeypatch.setattr(patcher.bridge, "record", fail)
    result = patcher.run(PROPOSAL, TASK)
    assert result.status == "failed_error"
    assert result.bundle is None
    assert not list((tmp_path / "bundles").iterdir())


def test_duplicate_bundle_is_never_overwritten(tmp_path: Path, local_repo: Path) -> None:
    """A repeat export cannot replace an earlier approved bundle."""
    patcher, _, _ = make_patcher(tmp_path, local_repo, approval=Approval())
    first = patcher.run(PROPOSAL, TASK)
    assert first.bundle is not None
    before = first.bundle.patch_path.read_bytes()
    again = patcher.run(PROPOSAL, TASK)
    assert again.status == "failed_error"
    assert first.bundle.patch_path.read_bytes() == before


def test_invalid_factory_products_are_rejected(
    tmp_path: Path,
    local_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only real L2/L3 instances bound to the shared budget are accepted."""
    gate = GateFactory()
    patcher, _, _ = make_patcher(tmp_path, local_repo, approval=Approval(), gate=gate)
    monkeypatch.setattr(gate, "create", lambda *_: object())
    assert patcher.run(PROPOSAL, TASK).status == "failed_verification"
    task = EvalTask(TaskSpec("eval", "goal"), "routing", "expected", security_critical=False)
    baseline = EvalRun(
        "baseline",
        (TaskOutcome(EvalReport("eval", "pass", 1.0, 1), security_critical=False),),
    )
    evaluator = EvalFactory("pass")
    patcher, _, _ = make_patcher(
        tmp_path,
        local_repo,
        approval=Approval(),
        baseline=baseline,
        evaluator=evaluator,
        tasks=(task,),
    )
    monkeypatch.setattr(evaluator, "create", lambda *_: object())
    assert patcher.run(PROPOSAL, TASK).status == "failed_eval_regression"


def test_cleanup_failure_is_not_hidden(
    tmp_path: Path,
    local_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A teardown error must be exposed, not collapsed into an ordinary failure."""
    patcher, _, _ = make_patcher(tmp_path, local_repo, approval=Approval())
    original = ThrowawayWorktreeSession._cleanup  # noqa: SLF001  # Exercise actual teardown before injected failure.

    def cleanup_then_fail(session: ThrowawayWorktreeSession) -> None:
        original(session)
        message = "host intervention may be necessary"
        raise WorktreeCleanupError(message)

    monkeypatch.setattr(ThrowawayWorktreeSession, "_cleanup", cleanup_then_fail)
    result = patcher.run(PROPOSAL, TASK)
    assert result.status == "failed_cleanup"
    assert result.bundle is None
    assert not (tmp_path / "bundles").exists()


def test_bundle_permissions_are_private_with_restrictive_umask(tmp_path: Path) -> None:
    """Published bundle files have exactly 0600 permissions under a restrictive umask."""
    path = tmp_path / "bundle.patch"
    previous = os.umask(0o777)
    try:
        _publish(path, b"patch")
    finally:
        os.umask(previous)
    assert stat.S_IMODE(path.stat().st_mode) == FILE_MODE


def test_disabled_or_unavailable_audit_does_not_leave_bundle(
    tmp_path: Path,
    local_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An audit bridge that refuses an event must veto and roll back export."""
    patcher, _, _ = make_patcher(tmp_path, local_repo, approval=Approval())
    monkeypatch.setattr(patcher.bridge, "record", lambda *_args, **_kwargs: None)
    result = patcher.run(PROPOSAL, TASK)
    assert result.status == "failed_error"
    assert result.bundle is None
    assert not list((tmp_path / "bundles").iterdir())


def test_nonprivate_bundle_directory_is_rejected(tmp_path: Path, local_repo: Path) -> None:
    """A world-readable existing bundle directory cannot receive patch artifacts."""
    directory = tmp_path / "bundles"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    patcher, _, _ = make_patcher(tmp_path, local_repo, approval=Approval())
    result = patcher.run(PROPOSAL, TASK)
    assert result.status == "failed_error"
    assert result.bundle is None
    assert not list(directory.iterdir())


def test_corrupted_ledger_cannot_authorize_a_bundle(tmp_path: Path, local_repo: Path) -> None:
    """An invalid evidence chain must fail closed even if the anchor tip is unchanged."""
    patcher, ledger, _ = make_patcher(tmp_path, local_repo, approval=Approval())
    patcher.bridge.record("selfimprove.test", {"info": "original"}, flags=FLAGS)
    event_path = tmp_path / "events"
    record = json.loads(event_path.read_text())
    record["result_preview"] = "tampered"
    event_path.write_text(json.dumps(record) + "\n")
    with pytest.raises(EvidenceIntegrityError):
        ledger.verify()
    result = patcher.run(PROPOSAL, TASK)
    assert not result.approved
    assert result.bundle is None
    assert not list((tmp_path / "bundles").iterdir())


def test_critical_regression_vetoes_zero_total_delta(
    tmp_path: Path,
    local_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An improvement elsewhere never offsets a critical pass-to-fail transition."""
    critical = EvalTask(TaskSpec("critical", "goal"), "routing", "expected", security_critical=True)
    ordinary = EvalTask(
        TaskSpec("ordinary", "goal"),
        "routing",
        "expected",
        security_critical=False,
    )
    baseline = EvalRun(
        "baseline",
        (
            TaskOutcome(EvalReport("critical", "pass", 1.0, 1), security_critical=True),
            TaskOutcome(EvalReport("ordinary", "fail", 0.0, 1), security_critical=False),
        ),
    )
    factory, approval = EvalFactory("pass"), Approval()

    def create(_path: Path, budget: BudgetGuard) -> EvalRunner:
        def evaluate(task: EvalTask) -> EvalReport:
            status = "fail" if task.security_critical else "pass"
            return EvalReport(task.spec.task_id, status, 1.0, 1)

        return EvalRunner(FLAGS, budget, evaluate)

    monkeypatch.setattr(factory, "create", create)
    patcher, _, _ = make_patcher(
        tmp_path,
        local_repo,
        approval=approval,
        baseline=baseline,
        evaluator=factory,
        tasks=(critical, ordinary),
    )
    assert patcher.run(PROPOSAL, TASK).status == "failed_eval_regression"
    assert approval.calls == 0


def test_l3_iteration_budget_exhaustion_is_reported(
    tmp_path: Path,
    local_repo: Path,
) -> None:
    """An incomplete runner caused by budget exhaustion preserves its dimension."""
    task = EvalTask(TaskSpec("eval", "goal"), "routing", "expected", security_critical=False)
    baseline = EvalRun(
        "baseline",
        (TaskOutcome(EvalReport("eval", "pass", 1.0, 1), security_critical=False),),
    )
    budget = BudgetGuard(BudgetLimits(1, 100, 60, 50, 30))
    patcher, _, _ = make_patcher(
        tmp_path,
        local_repo,
        budget=budget,
        baseline=baseline,
        evaluator=EvalFactory("pass"),
        tasks=(task,),
        approval=Approval(),
    )
    result = patcher.run(PROPOSAL, TASK)
    assert result.status == "failed_budget"
    assert result.budget_dimension == "iterations"
    assert not list((tmp_path / "worktrees").iterdir())


def test_clock_expiry_inside_verifier_cleans_worktree(
    tmp_path: Path,
    local_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wall expiry observed after a synchronous callback still cleans the worktree."""
    current = [0.0]
    budget = BudgetGuard(BudgetLimits(10, 100, 1, 50, 30), clock=lambda: current[0])
    factory = GateFactory()
    original = Verifier.verify

    def slow_verify(verifier: Verifier, task: TaskSpec) -> VerifierResult:
        result = original(verifier, task)
        current[0] = 2.0
        return result

    monkeypatch.setattr(Verifier, "verify", slow_verify)
    patcher, _, _ = make_patcher(
        tmp_path,
        local_repo,
        gate=factory,
        budget=budget,
        approval=Approval(),
    )
    result = patcher.run(PROPOSAL, TASK)
    assert result.status == "failed_budget"
    assert result.budget_dimension == "wall_seconds"
    assert not list((tmp_path / "worktrees").iterdir())


def test_existing_symlink_target_is_policy_rejected_before_git(
    tmp_path: Path,
    local_repo: Path,
) -> None:
    """A tracked symlink is refused before any Git invocation or temp directory."""
    outside = tmp_path / "outside"
    outside.write_text("old\n")
    (local_repo / "hello.txt").unlink()
    (local_repo / "hello.txt").symlink_to(outside)
    git(local_repo, "add", "hello.txt")
    git(local_repo, "commit", "-qm", "link")
    patcher, _, gate = make_patcher(tmp_path, local_repo, approval=Approval())
    outcome = patcher.run(PROPOSAL, TASK)
    assert outcome.status == "rejected_policy"
    assert outcome.budget.subprocesses == 0
    assert gate.calls == 0
    assert not (tmp_path / "worktrees").exists()
    assert outside.read_text() == "old\n"


def test_numeric_hashes_survive_evidence_redaction(tmp_path: Path) -> None:
    """Audit digests remain reconstructable even when they contain phone-like digits."""
    ledger = EvidenceLedger(tmp_path / "events", anchor_path=tmp_path / "anchor")
    digest, commit = "7" * 64, "8" * 40
    payloads = audit_payloads(
        PROPOSAL,
        commit,
        digest,
        PolicyCheckResult(allowed=True, touched_files=("hello.txt",), changed_lines=2),
    )
    record_audit(LedgerBridge(ledger), FLAGS, payloads)
    records = [
        json.loads(json.loads(line)["result_preview"])
        for line in (tmp_path / "events").read_text().splitlines()
    ]
    hash_chunks = next(
        row["bundle_hash_hex_chunks"] for row in records if "bundle_hash_hex_chunks" in row
    )
    commit_chunks = next(
        row["base_commit_hex_chunks"] for row in records if "base_commit_hex_chunks" in row
    )
    assert "".join(hash_chunks) == digest
    assert "".join(commit_chunks) == commit
    assert ledger.verify()[0]
