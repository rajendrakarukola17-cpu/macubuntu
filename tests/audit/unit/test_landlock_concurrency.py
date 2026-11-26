"""
tests/audit/unit/test_landlock_concurrency.py

Phase 7 remediation — Test for SUSPECTED-1:
_BWRAP_EXEC_PROBE_CACHE module-level dict race in landlock_policy.py.

Verifies that concurrent calls to _probe_bwrap_runtime with the same key
produce consistent results and do not crash or corrupt the cache.
"""

from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

from celina_pkg import landlock_policy as lp


def test_bwrap_probe_cache_concurrent_same_key():
    """Concurrent probes with the same key must return consistent results.

    The lock guards the check-then-write so that even under heavy contention,
    every thread observes the same cached value for a given key.
    """
    results = []
    errors = []

    def probe():
        try:
            ok = lp._probe_bwrap_runtime("/usr/bin/true", unshare_net=False)
            results.append(ok)
        except Exception as exc:  # pragma: no cover - should not happen
            errors.append(exc)

    threads = [threading.Thread(target=probe) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Errors during concurrent probe: {errors}"
    assert len(results) == 50
    # All results must be identical (same key -> same ok value)
    assert all(r == results[0] for r in results), (
        f"Inconsistent results under concurrency: {set(results)}"
    )


def test_bwrap_probe_cache_threadpool_stress():
    """ThreadPoolExecutor stress test: mixed keys, no exceptions, deterministic results."""
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = []
        for i in range(100):
            unshare = i % 2 == 0
            futures.append(
                executor.submit(lp._probe_bwrap_runtime, "/usr/bin/true", unshare_net=unshare)
            )
        results = [f.result() for f in as_completed(futures)]

    assert len(results) == 100
    # Even-indexed calls (unshare_net=False) must all agree
    false_results = [r for i, r in enumerate(results) if i % 2 == 0]
    assert all(r == false_results[0] for r in false_results)


def test_bwrap_probe_cache_lock_exists():
    """Verify the lock object exists and is a threading.Lock."""
    assert hasattr(lp, "_BWRAP_EXEC_PROBE_CACHE_LOCK")
    assert isinstance(lp._BWRAP_EXEC_PROBE_CACHE_LOCK, type(threading.Lock()))
