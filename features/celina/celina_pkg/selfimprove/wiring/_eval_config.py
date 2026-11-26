# Copyright 2026 Celina contributors.
"""Offline config integrity observations using real Celina validation paths."""

from __future__ import annotations

import json
import shutil
import sys
from typing import TYPE_CHECKING, Any

from ._eval_probes import Diagnostic

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from celina_pkg.approval import ApprovalToken
    from celina_pkg.selfimprove.flags import SelfImproveFlags
    from celina_pkg.selfimprove.l2.runner import CommandExecutor

    from ._token_store import TokenSpendStore


def _manifest_copies(work: Path, repository: Path, executor: CommandExecutor) -> Diagnostic:
    script = work / "tools" / "manifest_integrity.py"
    first = work / "system" / "capabilities" / "celina-capabilities.json"
    second = work / "features" / "celina" / "config" / "celina-capabilities.json"
    for directory in (script.parent, first.parent, second.parent):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    shutil.copyfile(repository / "tools" / "manifest_integrity.py", script)
    original = (repository / "system" / "capabilities" / "celina-capabilities.json").read_bytes()
    first.write_bytes(original)
    second.write_bytes(original + b"\n")
    result = executor([sys.executable, str(script), "--verify"], cwd=work, timeout=20.0)
    rejected = result.returncode != 0 and "NOT byte-identical" in result.stdout
    reason = f"manifest checker exit={result.returncode}; byte divergence reported={rejected}"
    return Diagnostic(rejected, reason, "rejected" if rejected else "not detected")


def _duplicate(work: Path, load: Callable[[Path], dict[str, Any]]) -> Diagnostic:
    path = work / "duplicate.json"
    path.write_text('{"tools":{"duplicate":{},"duplicate":{}}}', encoding="utf-8")
    try:
        load(path)
    except ValueError:
        return Diagnostic(
            passed=True, reason="manifest loader rejected duplicate identifier",
            observed="rejected",
        )
    return Diagnostic(
        passed=False, reason="manifest loader accepted duplicate identifier", observed="accepted",
    )


def _missing_reference(
    repository: Path, validate: Callable[..., list[str]],
) -> Diagnostic:
    path = repository / "system" / "capabilities" / "celina-capabilities.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    tools = manifest["tools"]
    tier1 = {name for name, spec in tools.items() if spec.get("tier") == "tier1"}
    tier2 = {name for name, spec in tools.items() if spec.get("tier") == "tier2"}
    absent = "gate4_nonexistent_tool"
    errors = validate(
        manifest, expected_tier1=tier1 | {absent}, expected_tier2=tier2,
    )
    detected = any("missing tools" in text.lower() and absent in text for text in errors)
    return Diagnostic(detected, f"missing capability reference detected={detected}", str(detected))


def _file_mode(
    work: Path, issue: Callable[..., tuple[ApprovalToken | None, str | None]],
    store_type: type[TokenSpendStore],
) -> Diagnostic:
    directory = work / "mode-store"
    directory.mkdir(mode=0o700)
    path = directory / "approval-spends.sqlite3"
    path.touch(mode=0o600)
    path.chmod(0o644)
    tool = "selfimprove_l7_patch"
    args: dict[str, object] = {"proposal_id": "fixture", "bundle_hash": "a" * 64}
    token, _ = issue(tool, args, lambda _title, _details: True)
    if token is None:
        msg = "fixture approval was not issued"
        raise RuntimeError(msg)
    store = store_type(directory)
    insecure = store.consume(token, tool, args)
    path.chmod(0o600)
    secure = store.consume(token, tool, args)
    passed = not insecure and secure
    reason = f"world-readable accepted={insecure}; private accepted={secure}"
    return Diagnostic(passed, reason, str(insecure))


def config_observations(
    flags: SelfImproveFlags, work: Path, repository: Path, executor: CommandExecutor,
) -> dict[str, Diagnostic]:
    """Observe real config, manifest and private-store behavior after opt-in."""
    if not flags.enabled or not flags.eval_harness:
        return {}
    from celina_pkg import approval, capabilities  # noqa: PLC0415  # Import only after opt-in.

    from ._token_store import TokenSpendStore  # noqa: PLC0415  # Import only after opt-in.

    work.mkdir(mode=0o700, parents=True, exist_ok=True)
    return {
        "config_integrity-17": _manifest_copies(work, repository, executor),
        "config_integrity-18": _duplicate(work, capabilities.load_manifest),
        "config_integrity-19": _missing_reference(
            repository, capabilities.validate_manifest_invariants,
        ),
        "config_integrity-20": _file_mode(work, approval.request_bound_approval, TokenSpendStore),
    }
