# Copyright 2026 Celina contributors.
"""Additional switch inheritance and patch-target boundary checks."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from celina_pkg.selfimprove.l7._sensitive_paths import is_sensitive, load_sensitive_paths
from celina_pkg.selfimprove.l7.policy import PolicyViolationError, validate_patch_proposal
from celina_pkg.selfimprove.l7.types import PatchPolicyConfig, PatchProposal
from celina_pkg.selfimprove.l7.worktree import WorktreeTargetError, _check_targets
from celina_pkg.selfimprove.wiring import _runtime_eval

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "selfimprove/protected_hashes.json"
RESERVED = (".github/CODEOWNERS", "features/celina/celina_pkg/selfimprove/protected_hashes.json")


def patch(path: str, *, create: bool = False) -> PatchProposal:
    old = "/dev/null" if create else "a/" + path
    hunk = "@@ -0,0 +1 @@\n+new\n" if create else "@@ -1 +1 @@\n-old\n+new\n"
    return PatchProposal("boundary", f"diff --git a/{path} b/{path}\n--- {old}\n+++ b/{path}\n{hunk}", "test")


def test_real_worker_spawns_grandchild_with_master_disabled(tmp_path: Path) -> None:
    """The real adapter constructs the disabled grandchild environment in a fresh worker."""
    script = '''import json, os, subprocess, sys
from pathlib import Path
from celina_pkg.selfimprove.budget import BudgetGuard
from celina_pkg.selfimprove.contracts import BudgetLimits
from celina_pkg.selfimprove.flags import SelfImproveFlags
from celina_pkg.selfimprove.wiring.eval_adapter import CelinaOfflineEvaluator
root = Path(os.environ["TEST_ROOT"])
obj = CelinaOfflineEvaluator(root, SelfImproveFlags(enabled=True, eval_harness=True), BudgetGuard(BudgetLimits(1,1,10,0,0)))
env = obj._environment(root)
p = subprocess.run([sys.executable, "-m", "celina_pkg.selfimprove.wiring._eval_worker"], env=env, capture_output=True, timeout=10)
print(json.dumps({"worker": os.environ.get("SELFIMPROVE_ENABLED"), "grandchild": env["SELFIMPROVE_ENABLED"], "exit": p.returncode}))
'''
    env = {"PATH": os.defpath, "HOME": str(tmp_path), "TEST_ROOT": str(tmp_path),
           "PYTHONPATH": str(ROOT / "features/celina"), "PYTHONDONTWRITEBYTECODE": "1",
           "SELFIMPROVE_ENABLED": "0", "SELFIMPROVE_EVAL_HARNESS": "1"}
    result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True,
                            text=True, check=True, timeout=15)
    assert json.loads(result.stdout) == {"worker": "0", "grandchild": "0", "exit": 2}


def test_internal_runtime_rejects_disabled_before_fixture_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Direct calls cannot reach the separately enabled simulated verifier flags."""
    monkeypatch.setenv("SELFIMPROVE_ENABLED", "0")
    monkeypatch.setenv("SELFIMPROVE_EVAL_HARNESS", "1")
    monkeypatch.setattr(_runtime_eval, "_target", lambda *_: pytest.fail("target touched"))
    with pytest.raises(PermissionError, match="disabled"):
        _runtime_eval._run(tmp_path)


def test_path_component_boundaries() -> None:
    files, directories = load_sensitive_paths()
    assert "selfimprove/protected_hashes.json" in files
    for path in ("features/celina/celina_pkg/config.py.bak", "features/celina/celina_pkg/selfimprove/l7/policy.pyevil", ".github/workflows-extra/new.yml"):
        assert not is_sensitive(path, files, directories)
    assert is_sensitive(".github/workflows/new.yml", files, directories)


@pytest.mark.parametrize("name,digest", list(json.loads(MANIFEST.read_text()).items()))
def test_existing_hash_protected_file_write_denied(name: str, digest: str) -> None:
    path = ROOT / name
    assert path.is_file()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    with pytest.raises(PolicyViolationError, match="protected"):
        validate_patch_proposal(patch(name), MANIFEST, PatchPolicyConfig())


def test_real_manifest_itself_is_explicit_and_write_denied() -> None:
    data = json.loads((ROOT / "selfimprove/l7_sensitive_paths.json").read_text())
    assert "selfimprove/protected_hashes.json" in data["files"]
    with pytest.raises(PolicyViolationError, match="protected"):
        validate_patch_proposal(patch("selfimprove/protected_hashes.json"), MANIFEST, PatchPolicyConfig())


@pytest.mark.parametrize("name", RESERVED)
def test_absent_reserved_name_cannot_be_created(name: str) -> None:
    assert not (ROOT / name).exists()
    with pytest.raises(PolicyViolationError, match="protected"):
        validate_patch_proposal(patch(name, create=True), MANIFEST, PatchPolicyConfig())


def test_git_symlink_mode_and_write_through_are_rejected(tmp_path: Path) -> None:
    """A symlink-mode patch is rejected, as is a pre-existing symlink target."""
    name = "selfimprove/protected_hashes.json"
    content = (f"diff --git a/{name} b/{name}\nnew file mode 120000\n"
               f"--- /dev/null\n+++ b/{name}\n@@ -0,0 +1 @@\n+elsewhere\n")
    with pytest.raises(PolicyViolationError):
        validate_patch_proposal(PatchProposal("link", content, "test"), MANIFEST, PatchPolicyConfig())
    root = tmp_path / "repo"
    root.mkdir()
    protected = root / "protected"
    protected.write_text("safe")
    (root / "link").symlink_to(protected)
    with pytest.raises(WorktreeTargetError, match="symlink"):
        _check_targets(root, ("link",))
    assert protected.read_text() == "safe"
