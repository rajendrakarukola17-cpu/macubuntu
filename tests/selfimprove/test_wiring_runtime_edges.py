# Copyright 2026 Celina contributors.
"""Runtime isolation, fail-closed paths and synthetic fixture boundary tests."""

from __future__ import annotations

import json
import runpy
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

import celina_pkg
from celina_pkg.approval import RiskLevel, request_bound_approval
from celina_pkg.selfimprove.budget import BudgetGuard
from celina_pkg.selfimprove.contracts import BudgetLimits, TaskSpec
from celina_pkg.selfimprove.flags import SelfImproveFlags
from celina_pkg.selfimprove.l6.optimizer import OfflineOptimizer
from celina_pkg.selfimprove.l6.promoter import ArtifactPromoter
from celina_pkg.selfimprove.l6.registry import ArtifactRegistry
from celina_pkg.selfimprove.l7.patcher import SafeSelfPatcher
from celina_pkg.selfimprove.wiring import _runtime_eval, _runtime_verify, cli, facade
from celina_pkg.selfimprove.wiring._token_store import TokenSpendStore

from .test_l6_optimizer import Factory, Generator
from .test_l7_patcher import TASK, make_patcher
from .test_l7_worktree import PATCH

if TYPE_CHECKING:
    from collections.abc import Callable

ACTIVE = SelfImproveFlags(enabled=True, eval_harness=True, optimize=True, patcher=True)
LIMITS = BudgetLimits(100, 100, 60, 100, 20)
DENIED = 2
BASELINE = 24 / 24


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Place the private state outside an explicit, separate HOME."""
    home, state = tmp_path / "home", tmp_path / "state"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return state


def test_facade_evaluation_failure_boundaries(
    isolated: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inactive, missing, unsafe, failed, oversized and malformed paths fail closed."""
    with pytest.raises(PermissionError):
        facade.SelfImproveRuntime(SelfImproveFlags(), isolated).evaluate()
    with pytest.raises(ValueError, match="state-dir"):
        facade.SelfImproveRuntime(ACTIVE).evaluate()
    runtime = facade.SelfImproveRuntime(ACTIVE, isolated)
    outputs = iter([
        CompletedProcess([], 1, "", "error"),
        CompletedProcess([], 0, "x" * 100_001, ""),
        CompletedProcess([], 0, "[]", ""),
        CompletedProcess([], 0, '{"complete": true}', ""),
    ])
    monkeypatch.setattr(facade.subprocess, "run", lambda *_a, **_k: next(outputs))
    with pytest.raises(RuntimeError):
        runtime.evaluate()
    with pytest.raises(RuntimeError):
        runtime.evaluate()
    with pytest.raises(TypeError):
        runtime.evaluate()
    assert runtime.evaluate() == {"complete": True}


@pytest.mark.parametrize("path", ["relative", "traversal", "home", "root", "symlink"])
def test_collaborator_paths_are_confined(
    isolated: Path, monkeypatch: pytest.MonkeyPatch, path: str,
) -> None:
    """No collaborator may escape state through lexical traversal or symlinks."""
    isolated.mkdir(mode=0o700)
    link = isolated / "link"
    link.symlink_to(isolated.parent, target_is_directory=True)
    values = {
        "relative": Path("relative"), "traversal": isolated / ".." / "outside",
        "home": Path.home() / "external", "root": isolated, "symlink": link / "state",
    }
    monkeypatch.setenv("HOME", str(isolated.parent / "home"))
    with pytest.raises(ValueError, match="collaborator"):
        facade.SelfImproveRuntime._within(values[path], isolated)  # noqa: SLF001
    facade.SelfImproveRuntime._within(isolated / "valid", isolated)  # noqa: SLF001


def test_optimizer_and_promotion_type_and_store_checks(isolated: Path) -> None:
    """Reject disabled calls, untrusted collaborators and alternative replay stores."""
    runtime = facade.SelfImproveRuntime(ACTIVE, isolated)
    registry = ArtifactRegistry(isolated / "registry", ACTIVE)
    optimizer = OfflineOptimizer(
        ACTIVE, BudgetGuard(LIMITS), registry, Generator(("x",)), Factory(),
    )
    promoter = ArtifactPromoter(ACTIVE, registry)
    with pytest.raises(PermissionError):
        facade.SelfImproveRuntime(SelfImproveFlags(), isolated).optimize(optimizer, "x", ())
    with pytest.raises(ValueError, match="optimizer"):
        runtime.optimize(object(), "x", ())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="promoter"):
        runtime.rollback(object(), "x", reason="x")  # type: ignore[arg-type]
    promoter.approval_gate = object()  # type: ignore[assignment]
    with pytest.raises(ValueError, match="approval"):
        runtime.rollback(promoter, "x", reason="x")


def test_patcher_type_and_store_checks(
    isolated: Path, tmp_path: Path,
) -> None:
    """Reject fake patchers and approval gates before applying any diff."""
    from celina_pkg.selfimprove.l7.types import PatchProposal  # noqa: PLC0415

    isolated.mkdir(mode=0o700)
    repo = tmp_path / "repo"
    repo.mkdir()
    patcher, _, _ = make_patcher(tmp_path, repo, flags=ACTIVE)
    patcher.manager.worktree_parent = isolated / "trees"
    patcher.bundle_dir = isolated / "bundles"
    runtime = facade.SelfImproveRuntime(ACTIVE, isolated)
    proposal = PatchProposal("x", PATCH, "test")
    with pytest.raises(ValueError, match="patcher"):
        runtime.patch(object(), proposal, TASK)  # type: ignore[arg-type]
    patcher.approval_gate = object()  # type: ignore[assignment]
    with pytest.raises(ValueError, match="approval"):
        runtime.patch(patcher, proposal, TASK)


def test_cli_active_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI reports disabled, unavailable trusted collaborators and evaluator errors."""
    env = {"SELFIMPROVE_ENABLED": "1", "SELFIMPROVE_EVAL_HARNESS": "1",
           "SELFIMPROVE_OPTIMIZE": "1", "SELFIMPROVE_PATCHER": "1"}
    assert cli.main(["status"], env=env) == 0
    assert json.loads(capsys.readouterr().out)["enabled"]
    for command in ("optimize", "patch"):
        assert cli.main([command, "--state-dir", str(tmp_path / "state")], env=env) == DENIED
    monkeypatch.setattr(facade.SelfImproveRuntime, "evaluate", lambda _self: {"complete": True})
    assert cli.main(["eval", "--state-dir", str(tmp_path / "state")], env=env) == 0
    assert json.loads(capsys.readouterr().out)["complete"]
    def fail(_self: object) -> None:
        msg = "fixture"
        raise RuntimeError(msg)
    monkeypatch.setattr(facade.SelfImproveRuntime, "evaluate", fail)
    assert cli.main(["eval", "--state-dir", str(tmp_path / "state")], env=env) == DENIED
    assert "fixture" in capsys.readouterr().err


def test_fixture_verifier_rechecks_once_and_denies_unknown(tmp_path: Path) -> None:
    """L2 fixture recheck is cached and never claims actual patch execution."""
    verifier = _runtime_verify.RepairFixtureVerifier(tmp_path, BudgetGuard(LIMITS))
    assert verifier.verify(TaskSpec("code_repair-21", "x")).status == "pass"
    assert verifier.verify(TaskSpec("unknown", "x")).status == "fail"


def test_runtime_worker_main_boundaries(
    isolated: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """Master and path validation precede collection; incomplete chains fail closed."""
    monkeypatch.setattr(_runtime_eval, "load_flags", SelfImproveFlags)
    assert _runtime_eval.main() == DENIED
    monkeypatch.setattr(_runtime_eval, "load_flags", lambda: ACTIVE)
    monkeypatch.setattr(_runtime_eval, "install_network_filter", lambda: None)
    monkeypatch.setenv("SELFIMPROVE_STATE_DIR", "relative")
    assert _runtime_eval.main() == DENIED
    monkeypatch.setenv("SELFIMPROVE_STATE_DIR", str(isolated))
    result = {"complete": True, "eval_chain_verified": True, "ledger_chain_verified": True}
    monkeypatch.setattr(_runtime_eval, "_run", lambda _state: result)
    assert _runtime_eval.main() == 0
    assert json.loads(capsys.readouterr().out) == result
    result["ledger_chain_verified"] = False
    assert _runtime_eval.main() == 1


def test_worker_rejects_loaded_target_mismatch(
    isolated: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A previously imported config with a different target is rejected."""
    target = isolated / "target"
    monkeypatch.setenv("TARGET_HOME", str(target))
    monkeypatch.setenv("CELINA_STORAGE_DIR", str(isolated / "storage"))
    monkeypatch.setattr(celina_pkg, "config", SimpleNamespace(HOME=isolated), raising=False)
    with pytest.raises(ValueError, match="mismatch"):
        _runtime_eval._run(isolated)  # noqa: SLF001


def test_expired_wall_token_fails_closed(tmp_path: Path) -> None:
    """Wall-clock expiry is checked independently of monotonic expiry."""
    token, _ = request_bound_approval("selfimprove_l7_patch", {}, lambda _a, _b: True)
    assert token is not None
    token.risk = RiskLevel.SECURITY_SENSITIVE
    token.expires_at = 0
    assert not TokenSpendStore(tmp_path / "approval").consume(token, "selfimprove_l7_patch", {})


@pytest.mark.parametrize("module", ["_eval_worker", "_runtime_eval", "cli"])
def test_module_entrypoint_when_disabled(module: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Entry points execute disabled handling without installing an irreversible filter."""
    monkeypatch.setenv("SELFIMPROVE_ENABLED", "0")
    monkeypatch.setattr("sys.argv", [module, "status"] if module == "cli" else [module])
    with pytest.warns(RuntimeWarning), pytest.raises(SystemExit):
        runpy.run_module(f"celina_pkg.selfimprove.wiring.{module}", run_name="__main__")


def test_runtime_worker_real_evaluation(
    isolated: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker persists the real offline baseline and verifies both chains."""
    from celina_pkg import config  # noqa: PLC0415  # Explicit active isolation test.

    target = isolated / "target"
    monkeypatch.setenv("TARGET_HOME", str(target))
    monkeypatch.setenv("CELINA_STORAGE_DIR", str(isolated / "storage"))
    monkeypatch.setattr(config, "HOME", target)
    monkeypatch.setattr(celina_pkg, "config", config)
    monkeypatch.setattr(_runtime_eval, "load_flags", lambda: ACTIVE)
    result = _runtime_eval._run(isolated)  # noqa: SLF001
    assert result["complete"]
    assert result["pass_rate"] == BASELINE
    assert result["eval_chain_verified"]
    assert result["ledger_chain_verified"]


def test_cli_disabled_and_malformed_worker_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """A malformed response is reported, not exposed as an uncaught exception."""
    assert cli.main(["eval", "--state-dir", str(tmp_path / "state")], env={}) == DENIED
    assert "blocked by feature flags" in capsys.readouterr().err
    def bad(_self: object) -> None:
        msg = "invalid worker response"
        raise TypeError(msg)
    monkeypatch.setattr(facade.SelfImproveRuntime, "evaluate", bad)
    env = {"SELFIMPROVE_ENABLED": "1", "SELFIMPROVE_EVAL_HARNESS": "1"}
    assert cli.main(["eval", "--state-dir", str(tmp_path / "state")], env=env) == DENIED
    assert "invalid worker response" in capsys.readouterr().err
