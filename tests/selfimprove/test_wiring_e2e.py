# Copyright 2026 Celina contributors.
"""Default-off runtime and real offline evaluation end-to-end contracts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from celina_pkg.evidence import EvidenceLedger
from celina_pkg.selfimprove.budget import BudgetGuard
from celina_pkg.selfimprove.flags import SelfImproveFlags
from celina_pkg.selfimprove.l6.optimizer import OfflineOptimizer
from celina_pkg.selfimprove.l6.promoter import ArtifactPromoter
from celina_pkg.selfimprove.l6.registry import ArtifactRegistry
from celina_pkg.selfimprove.l6.types import OptimizationConfig
from celina_pkg.selfimprove.l7._bundle_io import bundle_hash, bundle_metadata
from celina_pkg.selfimprove.l7._git_runner import _execute
from celina_pkg.selfimprove.l7.patcher import SafeSelfPatcher
from celina_pkg.selfimprove.l7.policy import validate_patch_proposal
from celina_pkg.selfimprove.l7.types import PatchPolicyConfig, PatchProposal
from celina_pkg.selfimprove.l7.worktree import WorktreeManager
from celina_pkg.selfimprove.ledger_bridge import LedgerBridge
from celina_pkg.selfimprove.wiring import cli
from celina_pkg.selfimprove.wiring.approval_adapter import (
    PatchApprovalAdapter,
    PromotionApprovalAdapter,
    TokenSpendStore,
    patch_arguments,
    promotion_arguments,
)
from celina_pkg.selfimprove.wiring.facade import SelfImproveRuntime

from .test_l6_optimizer import FLAGS as L6_FLAGS
from .test_l6_optimizer import LIMITS as L6_LIMITS
from .test_l6_optimizer import TASKS, Factory, Generator
from .test_l7_patcher import FLAGS as L7_FLAGS
from .test_l7_patcher import LIMITS as L7_LIMITS
from .test_l7_patcher import TASK, GateFactory
from .test_l7_worktree import PATCH, git, policy_manifest
from .test_l7_worktree import state as repo_state
from .test_wiring_approval import clone, issue

if TYPE_CHECKING:
    from pathlib import Path

TASK_COUNT = 24
SECOND_VERSION = 2
FILE_MODE = 0o600
DIR_MODE = 0o700


def test_fresh_default_off_has_no_home_or_state_entries(tmp_path: Path) -> None:
    """Import, status and blocked active commands do not touch HOME or state."""
    home, state = tmp_path / "home", tmp_path / "state"
    home.mkdir()
    env = {
        "PATH": os.environ.get("PATH", ""), "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1", "HOME": str(home), "SELFIMPROVE_ENABLED": "0",
        "TEST_STATE": str(state),
    }
    script = (
        "import os, sys\n"
        "from celina_pkg.selfimprove.wiring import facade, cli\n"
        "state = os.environ['TEST_STATE']\n"
        "assert cli.main(['status']) == 0\n"
        "for command in ('eval', 'optimize', 'patch'):\n"
        "    assert cli.main([command, '--state-dir', state]) != 0\n"
        "for name in ('celina_pkg.evidence', 'celina_pkg.router', "
        "'celina_pkg.tools', 'celina_pkg.approval'):\n"
        "    assert name not in sys.modules, name\n"
    )
    result = subprocess.run(  # noqa: S603  # Fixed interpreter and trusted inline probe.
        [sys.executable, "-c", script], env=env, timeout=30, check=False,
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert not list(home.iterdir())
    assert not state.exists()


@pytest.mark.parametrize("command", ["eval", "optimize", "patch"])
def test_active_cli_requires_explicit_state_dir(
    command: str, capsys: pytest.CaptureFixture[str],
) -> None:
    """No active command defaults to the user's HOME."""
    env = {
        "SELFIMPROVE_ENABLED": "1", "SELFIMPROVE_EVAL_HARNESS": "1",
        "SELFIMPROVE_OPTIMIZE": "1", "SELFIMPROVE_PATCHER": "1", "SELFIMPROVE_VERIFIERS": "1",
    }
    assert cli.main([command], env=env) != 0
    assert "state-dir" in capsys.readouterr().err


def test_real_eval_cli_persists_both_chains_with_separate_home(tmp_path: Path) -> None:
    """An active isolated run stores all 24 observed outcomes and verifies both chains."""
    home, state = tmp_path / "home", tmp_path / "state"
    home.mkdir()
    env = {
        "PATH": os.environ.get("PATH", ""), "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1", "HOME": str(home), "SELFIMPROVE_ENABLED": "1",
        "SELFIMPROVE_EVAL_HARNESS": "1",
    }
    result = subprocess.run(  # noqa: S603  # Fixed interpreter, module and trusted arguments.
        [
            sys.executable, "-m", "celina_pkg.selfimprove.wiring.cli",
            "eval", "--state-dir", str(state),
        ],
        env=env, timeout=90, check=False, text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["task_count"] == TASK_COUNT
    assert data["pass_rate"] == 24 / 24
    assert data["complete"]
    assert data["eval_chain_verified"]
    assert data["ledger_chain_verified"]
    assert data["diagnostics"]["routing-01"]["passed"] is True
    assert not list(home.iterdir())
    store_path = state / "evaluations" / "evaluations.jsonl"
    saved = json.loads(store_path.read_text().strip())["run"]
    counts: dict[str, list[int]] = {}
    for item in saved["outcomes"]:
        report = item["report"]
        category = report["task_id"].rsplit("-", 1)[0]
        counts.setdefault(category, [0, 0])
        counts[category][1] += 1
        counts[category][0] += report["status"] == "pass"
    assert counts == {
        "routing": [5, 5], "security_refusal": [6, 6], "tool_args": [5, 5],
        "config_integrity": [4, 4], "code_repair": [4, 4],
    }
    assert data["diagnostics"]["config_integrity-18"]["passed"]
    assert "duplicate identifier" in data["diagnostics"]["config_integrity-18"]["reason"]
    assert store_path.is_file()
    assert (state / "evidence" / "ledger.jsonl").is_file()


def test_runtime_disabled_rejects_without_state_or_collaborators(tmp_path: Path) -> None:
    """Constructing and calling a disabled facade cannot create the supplied state."""
    runtime = SelfImproveRuntime(SelfImproveFlags(), tmp_path / "state")
    assert not runtime.enabled_for("eval")
    assert not runtime.enabled_for("optimize")
    assert not runtime.enabled_for("patch")
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("master", [None, "0"])
def test_default_off_in_fresh_process_unset_or_zero(tmp_path: Path, master: str | None) -> None:
    """Both unset and zero master settings leave HOME and state untouched."""
    home, state = tmp_path / "home", tmp_path / "state"
    home.mkdir()
    env = {
        "PATH": os.environ.get("PATH", ""), "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1", "HOME": str(home), "STATE": str(state),
    }
    if master is not None:
        env["SELFIMPROVE_ENABLED"] = master
    script = (
        "import os, sys\n"
        "from celina_pkg.selfimprove.wiring.cli import main\n"
        "assert main(['status']) == 0\n"
        "for c in ('eval', 'optimize', 'patch'):\n"
        "    assert main([c, '--state-dir', os.environ['STATE']]) != 0\n"
        "assert not any(n in sys.modules for n in "
        "('celina_pkg.evidence', 'celina_pkg.approval', 'celina_pkg.router', 'celina_pkg.tools'))\n"
    )
    result = subprocess.run(  # noqa: S603  # Fixed interpreter and trusted inline code.
        [sys.executable, "-c", script], env=env, timeout=30, capture_output=True, check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"enabled": False}
    assert not list(home.iterdir())
    assert not state.exists()


def test_l6_runtime_optimizer_promotion_rollback_and_replay(tmp_path: Path) -> None:
    """A genuine bound token promotes once despite reconstruction after rollback."""
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    runtime = SelfImproveRuntime(L6_FLAGS, state)
    registry = ArtifactRegistry(state / "registry", L6_FLAGS)
    registry.register("main", "prompt", "baseline")
    registry.activate("main", 1)
    optimizer = OfflineOptimizer(
        L6_FLAGS, BudgetGuard(L6_LIMITS), registry, Generator(("candidate",)), Factory(),
        config=OptimizationConfig(max_candidates=1, bootstrap_resamples=50),
    )
    outcome = runtime.optimize(optimizer, "main", TASKS)
    assert outcome.status == "improved"
    assert outcome.proposal is not None
    token = issue("selfimprove_l6_promotion", promotion_arguments(outcome.proposal))
    assert token is not None
    duplicate = clone(token)
    promoter = ArtifactPromoter(
        L6_FLAGS, registry,
        approval_gate=PromotionApprovalAdapter(token, TokenSpendStore(state / "approvals")),
    )
    assert runtime.promote(promoter, outcome.proposal).status == "promoted"
    assert registry.active("main").version == SECOND_VERSION
    assert runtime.rollback(promoter, "main", reason="revert").status == "rolled_back"
    assert registry.active("main").version == 1
    promoter.approval_gate = PromotionApprovalAdapter(
        duplicate, TokenSpendStore(state / "approvals"),
    )
    assert runtime.promote(promoter, outcome.proposal).status == "rejected_by_approver"
    assert registry.active("main").version == 1


def test_l7_runtime_real_worktree_approval_and_replay(tmp_path: Path) -> None:
    """Protected paths invoke zero Git; clean review uses a bundle-bound token once."""
    root, repo = tmp_path / "state", tmp_path / "repo"
    root.mkdir(mode=0o700)
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    (repo / "hello.txt").write_text("old\n")
    git(repo, "add", "hello.txt")
    git(repo, "commit", "-qm", "initial")
    original = repo_state(repo)
    calls: list[tuple[str, ...]] = []

    def tracked(
        argv: tuple[str, ...], cwd: Path, data: bytes | None, timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append(argv)
        return _execute(argv, cwd, data, timeout)

    manager = WorktreeManager(
        repo, root / "trees", policy_manifest(tmp_path), BudgetGuard(L7_LIMITS),
        PatchPolicyConfig(), executor=tracked,
    )
    ledger = EvidenceLedger(root / "ledger", anchor_path=root / "anchor")
    gate = GateFactory()
    patcher = SafeSelfPatcher(
        L7_FLAGS, manager, gate, bundle_dir=root / "bundles", bridge=LedgerBridge(ledger),
    )
    runtime = SelfImproveRuntime(L7_FLAGS, root)
    protected_path = "features/celina/celina_pkg/approval.py"
    protected = PatchProposal("bad", PATCH.replace("hello.txt", protected_path), "bad")
    assert runtime.patch(patcher, protected, TASK).status == "rejected_policy"
    assert manager.budget.snapshot().subprocesses == 0
    assert gate.calls == 0
    assert calls == []
    proposal = PatchProposal("p1", PATCH, "change file")
    policy = validate_patch_proposal(proposal, manager.manifest_path, manager.config)
    digest = bundle_hash(bundle_metadata(proposal, original[0].decode().strip(), policy))
    token = issue("selfimprove_l7_patch", patch_arguments(proposal, digest))
    assert token is not None
    duplicate = clone(token)
    patcher.approval_gate = PatchApprovalAdapter(token, TokenSpendStore(root / "approvals"))
    result = runtime.patch(patcher, proposal, TASK)
    assert result.status == "bundle_ready"
    assert result.bundle is not None
    assert calls
    assert result.bundle.bundle_hash == digest
    assert result.bundle.patch_path.stat().st_mode & 0o777 == FILE_MODE
    assert result.bundle.metadata_path.stat().st_mode & 0o777 == FILE_MODE
    assert (root / "bundles").stat().st_mode & 0o777 == DIR_MODE
    assert repo_state(repo) == original
    assert ledger.verify()[0]
    patcher.approval_gate = PatchApprovalAdapter(duplicate, TokenSpendStore(root / "approvals"))
    assert runtime.patch(patcher, proposal, TASK).status == "rejected_by_approver"
    assert repo_state(repo) == original
