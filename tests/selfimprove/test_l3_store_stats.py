# Copyright 2026 Celina contributors.
"""Test L3 durable evaluation storage and paired statistical comparison."""

import json
import random
import stat
from pathlib import Path

import pytest

from celina_pkg.selfimprove.contracts import EvalReport
from celina_pkg.selfimprove.l3.stats import EvalRun, TaskOutcome, compare_eval_runs
from celina_pkg.selfimprove.l3.store import EvalStore, EvalStoreIntegrityError

PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
EVENT_COUNT = 2


def run(run_id: str, passes: set[int], *, critical: set[int] | None = None) -> EvalRun:
    """Create paired benchmark results with stable IDs."""
    critical = set() if critical is None else critical
    return EvalRun(
        run_id,
        tuple(
            TaskOutcome(
                EvalReport(str(i), "pass" if i in passes else "fail", float(i in passes), 1),
                i in critical,
            )
            for i in range(24)
        ),
    )


def test_store_private_chain_and_hook(tmp_path: Path) -> None:
    """Append records, verify chain and deliver compact bridge summary."""

    class Bridge:
        def __init__(self) -> None:
            self.events: list[tuple[str, object]] = []

        def record(self, event_type: str, payload: object) -> None:
            self.events.append((event_type, payload))

    bridge = Bridge()
    store = EvalStore(tmp_path / "private", bridge=bridge)
    first = store.append(run("one", set(range(10)), critical={0}))
    second = store.append(run("two", set(range(20)), critical={0}))
    assert second.prev_hash == first.entry_hash
    assert store.verify_chain() == (first, second)
    assert stat.S_IMODE(store.directory.stat().st_mode) == PRIVATE_DIR_MODE
    assert stat.S_IMODE(store.path.stat().st_mode) == PRIVATE_FILE_MODE
    assert len(bridge.events) == EVENT_COUNT
    assert bridge.events[-1][0] == "selfimprove.eval.run"
    assert bridge.events[-1][1]["entry_hash"] == second.entry_hash


@pytest.mark.parametrize(
    "damage", ["modify", "reorder", "delete", "truncate", "missing", "anchor", "extra_field"],
)
def test_store_tamper_fails_closed(tmp_path: Path, damage: str) -> None:
    """Every read and append reject common log and checkpoint mutations."""
    store = EvalStore(tmp_path / "store")
    store.append(run("one", {0}))
    store.append(run("two", {1}))
    lines = store.path.read_bytes().splitlines(keepends=True)
    if damage == "modify":
        doc = json.loads(lines[0])
        doc["run"]["run_id"] = "evil"
        lines[0] = (json.dumps(doc) + "\n").encode()
    elif damage == "extra_field":
        doc = json.loads(lines[0])
        doc["run"]["outcomes"][0]["extra"] = "injected"
        lines[0] = (json.dumps(doc) + "\n").encode()
    elif damage == "reorder":
        lines.reverse()
    elif damage == "delete":
        lines.pop()
    elif damage == "truncate":
        lines[-1] = lines[-1][:-4]
    elif damage == "anchor":
        store.anchor_path.unlink()
    if damage == "missing":
        store.path.unlink()
    elif damage != "anchor":
        store.path.write_bytes(b"".join(lines))
    with pytest.raises(EvalStoreIntegrityError):
        store.verify_chain()
    with pytest.raises(EvalStoreIntegrityError):
        store.append(run("three", {2}))


def test_bootstrap_is_paired_seeded_and_private_rng() -> None:
    """Strong improvements are significant without mutating global RNG state."""
    baseline = run("baseline", set(range(10)), critical={0, 1})
    candidate = run("candidate", set(range(20)), critical={0, 1})
    state = random.getstate()
    result = compare_eval_runs(baseline, candidate)
    assert result == compare_eval_runs(baseline, candidate)
    assert random.getstate() == state
    assert result.improved
    assert result.statistically_significant
    assert result.approved_for_promotion
    assert result.delta > 0
    assert result.ci_low > 0


def test_marginal_and_security_veto() -> None:
    """A security regression always vetoes promotion, even with a strong gain."""
    baseline = run("baseline", set(range(10)), critical={0})
    marginal = compare_eval_runs(baseline, run("marginal", set(range(11)), critical={0}))
    assert not marginal.statistically_significant
    assert not marginal.approved_for_promotion
    veto = compare_eval_runs(baseline, run("veto", set(range(1, 23)), critical={0}))
    assert veto.security_regression
    assert veto.statistically_significant
    assert not veto.approved_for_promotion


def test_incompatible_runs_fail_closed() -> None:
    """Empty, mismatched IDs or classifications, incomplete runs are rejected."""
    baseline = run("baseline", {0}, critical={0})
    with pytest.raises(ValueError, match=r"."):
        compare_eval_runs(EvalRun("empty", ()), EvalRun("empty2", ()))
    with pytest.raises(ValueError, match=r"."):
        compare_eval_runs(baseline, EvalRun("short", baseline.outcomes[:-1]))
    with pytest.raises(ValueError, match=r"."):
        compare_eval_runs(baseline, run("changed", {0}, critical={1}))
    with pytest.raises(ValueError, match=r"."):
        compare_eval_runs(baseline, EvalRun("incomplete", baseline.outcomes, complete=False))


def test_store_rejects_unsafe_existing_file_mode(tmp_path: Path) -> None:
    """Existing log or checkpoint exposed to other users fails closed."""
    store = EvalStore(tmp_path / "store")
    store.path.chmod(0o644)
    with pytest.raises(EvalStoreIntegrityError, match="mode"):
        store.verify_chain()


def test_store_rejects_dangling_anchor_symlink(tmp_path: Path) -> None:
    """A dangling symlink cannot be mistaken for a fresh checkpoint."""
    root = tmp_path / "store"
    root.mkdir()
    (root / "head.sha256").symlink_to(root / "missing")
    with pytest.raises(EvalStoreIntegrityError, match="symlink"):
        EvalStore(root)


def test_skipped_or_error_outcomes_cannot_support_promotion() -> None:
    """An unevaluated baseline must never manufacture an apparent improvement."""
    baseline = EvalRun("baseline", (
        TaskOutcome(EvalReport("x", "skipped", 0.0, 0), security_critical=True),
    ))
    candidate = EvalRun("candidate", (
        TaskOutcome(EvalReport("x", "pass", 1.0, 1), security_critical=True),
    ))
    with pytest.raises(ValueError, match="nonterminal"):
        compare_eval_runs(baseline, candidate)


def test_stats_validation_edges() -> None:
    """Malformed DTOs and sampling parameters fail closed."""
    report = EvalReport("x", "pass", 1.0, 1)
    with pytest.raises(ValueError, match="outcome"):
        TaskOutcome(report, security_critical="yes")
    with pytest.raises(ValueError, match="run ID"):
        EvalRun("", ())
    with pytest.raises(ValueError, match="tuple"):
        EvalRun("x", [])
    outcome = TaskOutcome(report, security_critical=False)
    with pytest.raises(ValueError, match="duplicate"):
        EvalRun("x", (outcome, outcome))
    with pytest.raises(ValueError, match="blocked"):
        EvalRun("x", (), blocked_reason="unexpected")
    good = EvalRun("x", (outcome,))
    with pytest.raises(ValueError, match="resamples"):
        compare_eval_runs(good, good, resamples=0)
    with pytest.raises(ValueError, match="alpha"):
        compare_eval_runs(good, good, alpha=0)
    assert EvalRun("empty", ()).security_pass_rate == 0


def test_store_rejects_invalid_paths_and_data(tmp_path: Path) -> None:
    """Symlink, orphaned data and malformed JSON are rejected."""
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(EvalStoreIntegrityError, match="symlink"):
        EvalStore(link)
    orphan = tmp_path / "orphan"
    orphan.mkdir()
    (orphan / "random").write_text("x")
    with pytest.raises(EvalStoreIntegrityError, match="incomplete"):
        EvalStore(orphan)
    store = EvalStore(tmp_path / "store")
    with pytest.raises(TypeError, match="EvalRun"):
        store.append("not a run")
    store.path.write_text("{}\n")
    with pytest.raises(EvalStoreIntegrityError, match="entry"):
        store.verify_chain()
    store.path.write_text('{"run":{},"prev_hash":"x","entry_hash":"x"}\n')
    with pytest.raises(EvalStoreIntegrityError, match="schema"):
        store.verify_chain()
    store.path.write_text("{\n")
    with pytest.raises(EvalStoreIntegrityError, match="cannot read"):
        store.verify_chain()


def test_store_rejects_changed_or_symlinked_lock_and_store(tmp_path: Path) -> None:
    """Symlinked runtime files do not bypass verification."""
    store = EvalStore(tmp_path / "store")
    store.path.unlink()
    store.path.symlink_to(tmp_path / "target")
    with pytest.raises(EvalStoreIntegrityError, match="symlink"):
        store.verify_chain()
    (store.directory / ".lock").unlink()
    (store.directory / ".lock").symlink_to(tmp_path / "target")
    with pytest.raises(EvalStoreIntegrityError, match="symlink"):
        store.verify_chain()
