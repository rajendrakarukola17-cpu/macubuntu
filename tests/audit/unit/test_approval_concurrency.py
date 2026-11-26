"""
tests/audit/unit/test_approval_concurrency.py

Phase 7 remediation — Test for UNVERIFIED-2:
Concurrency race on ApprovalToken.verify_and_consume_atomic under lock contention.

Verifies that when N threads concurrently call verify_and_consume_atomic on the
same token, exactly one succeeds and the rest fail with "already consumed".
"""

from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

from celina_pkg.approval import ApprovalToken, RiskLevel


def _make_token():
    from celina_pkg.approval import compute_arguments_hash
    args_hash = compute_arguments_hash("write_file", None)
    return ApprovalToken(
        tool="write_file",
        target="/home/user/test.txt",
        arguments_hash=args_hash,
        risk=RiskLevel.FILE_WRITE,
        expires_at=9999999999.0,
        expires_at_monotonic=9999999999.0,
        single_use=True,
        consumed=False,
    )


def test_verify_and_consume_atomic_exactly_one_succeeds():
    """N threads racing on verify_and_consume_atomic: exactly one succeeds."""
    token = _make_token()
    N = 100
    successes = []
    failures = []
    lock = threading.Lock()

    def try_consume():
        ok, err = token.verify_and_consume_atomic("write_file", None)
        with lock:
            if ok:
                successes.append(ok)
            else:
                failures.append(err)

    threads = [threading.Thread(target=try_consume) for _ in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(successes) == 1, f"Expected exactly 1 success, got {len(successes)}"
    assert len(failures) == N - 1, f"Expected {N-1} failures, got {len(failures)}"
    # All failures should mention "already consumed"
    for err in failures:
        assert "already consumed" in err.lower(), f"Unexpected error: {err}"


def test_verify_and_consume_atomic_threadpool_stress():
    """ThreadPoolExecutor stress test with 500 threads, exactly one success."""
    token = _make_token()
    N = 500
    success_count = [0]
    failure_count = [0]
    count_lock = threading.Lock()

    def try_consume():
        ok, _ = token.verify_and_consume_atomic("write_file", None)
        with count_lock:
            if ok:
                success_count[0] += 1
            else:
                failure_count[0] += 1

    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = [executor.submit(try_consume) for _ in range(N)]
        for f in as_completed(futures):
            f.result()  # propagate any exceptions

    assert success_count[0] == 1, f"Expected exactly 1 success, got {success_count[0]}"
    assert failure_count[0] == N - 1, f"Expected {N-1} failures, got {failure_count[0]}"


def test_verify_and_consume_atomic_different_tools_rejected():
    """Concurrent calls with wrong tool name are all rejected."""
    token = _make_token()
    N = 50
    failures = []

    def try_wrong_tool():
        ok, err = token.verify_and_consume_atomic("delete_file", None)
        if not ok:
            failures.append(err)

    threads = [threading.Thread(target=try_wrong_tool) for _ in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(failures) == N, f"Expected {N} failures, got {len(failures)}"
    assert token.consumed is False, "Token should not be consumed by wrong-tool calls"
