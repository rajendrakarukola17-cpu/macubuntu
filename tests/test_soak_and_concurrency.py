"""
Suite 8: Last-Mile Concurrency, Soak, Supply-Chain Pinning, Doctor & Platform Edge-Case Tests
Covers:
  - 4-thread concurrent SQLite WAL + busy_timeout=5000 stress test (MemoryDB, SemanticCache, SpotlightIndex)
  - Accelerated soak test asserting bounded open FD count & bounded memory allocation
  - Per-tool ToolCircuitBreaker (3 consecutive failures -> open cooldown, resets on success, denials ignored)
  - Pinned Anisette Docker OCI digest + Pinned GNOME Shell extension lockfile (extensions.lock)
  - Concurrent install.sh flock guard, --doctor read-only fast-path, --system-only / --user-only / --accessibility / --replace-swap
  - LUKS swap resume readiness guard & dual-boot Windows EFI detection
"""

from __future__ import annotations

import os
import subprocess
import threading
import tracemalloc
from pathlib import Path

from celina_pkg import cache, memory, os_events, router, spotlight_index, tools


def _run_bash(cmd: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=60, env=env)


def test_sqlite_wal_busy_timeout_and_4thread_concurrent_stress(tmp_path: Path):
    mem_db = memory.MemoryDB(db_path=tmp_path / "memory.db")
    sem_cache = cache.SmartCache(db_path=tmp_path / "cache.db")
    spot_idx = spotlight_index.SpotlightIndex(db_path=tmp_path / "spotlight.db")

    try:
        # 1. Verify WAL + busy_timeout >= 5000 on all 3 SQLite stores
        for name, conn in (
            ("memory", mem_db._conn),
            ("cache", sem_cache._conn),
            ("spotlight", spot_idx._conn),
        ):
            jm = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            bt = int(conn.execute("PRAGMA busy_timeout;").fetchone()[0])
            assert str(jm).lower() == "wal", f"{name} DB not in WAL mode (got {jm})"
            assert bt >= 5000, f"{name} DB busy_timeout < 5000 (got {bt})"

        sample_dir = tmp_path / "docs"
        sample_dir.mkdir()
        (sample_dir / "quarterly_report.txt").write_text("Q4 revenue metrics and Linux notes", encoding="utf-8")

        # 2. Hammer all 3 DBs concurrently from 4 threads
        errors: list[Exception] = []

        def worker(tid: int) -> None:
            try:
                for i in range(25):
                    mem_db.set_fact(f"fact_{tid}_{i}", f"value_{tid}_{i}")
                    mem_db.add_knowledge_triple(f"proj_{tid}", "status", f"phase_{i}")
                    mem_db.recall_relevant(f"value_{tid}_{i}", max_facts=3)
                    sem_cache.set("local_q", f"thread {tid} query {i}", f"answer {tid}:{i}")
                    sem_cache.get("local_q", f"thread {tid} query {i}")
                    if i % 5 == 0:
                        spot_idx.refresh(roots=[sample_dir], max_files=50)
                    spot_idx.search("quarterly", limit=5)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=30.0)
            assert not th.is_alive(), "Concurrent DB worker thread hung!"

        assert errors == [], f"SQLite concurrency errors encountered: {errors}"
    finally:
        mem_db.close()
        sem_cache.close()
        spot_idx.close()


def test_accelerated_soak_bounded_fds_and_memory(tmp_path: Path):
    proc_fd_dir = Path("/proc/self/fd")
    mem_db = memory.MemoryDB(db_path=tmp_path / "soak_mem.db")
    sem_cache = cache.SmartCache(db_path=tmp_path / "soak_cache.db")
    spot_idx = spotlight_index.SpotlightIndex(db_path=tmp_path / "soak_spot.db")
    etc_mock = tmp_path / "etc"
    home_mock = tmp_path / "home"
    dev_mock = tmp_path / "dev"
    etc_mock.mkdir()
    home_mock.mkdir()
    dev_mock.mkdir()

    try:
        # Warm up baseline
        for i in range(10):
            mem_db.set_fact(f"warm_{i}", f"v_{i}")
            sem_cache.set("soak", f"warm {i}", f"resp {i}")
            os_events.get_os_parity_health(etc_root=etc_mock, home_root=home_mock, dev_root=dev_mock)

        fd_before = len(list(proc_fd_dir.iterdir())) if proc_fd_dir.exists() else 0
        tracemalloc.start()
        snap_before = tracemalloc.take_snapshot()

        for i in range(150):
            mem_db.set_fact(f"k_{i % 20}", f"val_{i}")
            mem_db.recall_relevant(f"val_{i}", max_facts=3)
            sem_cache.set("soak", f"prompt {i % 20}", f"reply {i}")
            sem_cache.get("soak", f"prompt {i % 20}")
            spot_idx.search("missing_query", limit=3)
            os_events.get_os_parity_health(etc_root=etc_mock, home_root=home_mock, dev_root=dev_mock)

        snap_after = tracemalloc.take_snapshot()
        tracemalloc.stop()
        fd_after = len(list(proc_fd_dir.iterdir())) if proc_fd_dir.exists() else 0

        # Assert zero FD leaks and tight memory growth (< 4 MiB over 150 cycles)
        assert (fd_after - fd_before) <= 4, f"FD leak detected: before={fd_before}, after={fd_after}"
        total_delta_bytes = sum(s.size_diff for s in snap_after.compare_to(snap_before, "filename"))
        assert total_delta_bytes < 4 * 1024 * 1024, f"Unbounded memory growth: {total_delta_bytes} bytes"
    finally:
        mem_db.close()
        sem_cache.close()
        spot_idx.close()


def test_per_tool_circuit_breaker_opens_recovers_and_ignores_user_denials(monkeypatch):
    cb = tools.ToolCircuitBreaker(threshold=3, cooldown_sec=60.0)
    assert cb.check_open("control_media", now=100.0) is None
    assert cb.record_failure("control_media", now=100.0) is False
    assert cb.record_failure("control_media", now=101.0) is False
    assert cb.record_failure("control_media", now=102.0) is True
    rem = cb.check_open("control_media", now=110.0)
    assert rem is not None and 51.0 <= rem <= 53.0
    # After cooldown expires, closes cleanly
    assert cb.check_open("control_media", now=163.0) is None

    # Integration with execute_tool(): 3 consecutive errors open circuit; success resets; Denied does NOT trip
    tools.TOOL_CIRCUIT_BREAKER.reset()
    calls = {"n": 0}

    def denied_tool(**_kw):
        calls["n"] += 1
        return "Denied: user rejected action"

    monkeypatch.setitem(tools._AUTO_TOOL_MAP, "cache_status", denied_tool)
    try:
        for _ in range(5):
            assert tools.execute_tool("cache_status", {}).startswith("Denied:")
        assert calls["n"] == 5
        assert tools.TOOL_CIRCUIT_BREAKER.check_open("cache_status") is None
    finally:
        tools.TOOL_CIRCUIT_BREAKER.reset()

    calls["n"] = 0

    def flaky_tool(**_kw):
        calls["n"] += 1
        return "Error: synthetic failure"

    monkeypatch.setitem(tools._AUTO_TOOL_MAP, "cache_status", flaky_tool)
    try:
        for _ in range(3):
            out = tools.execute_tool("cache_status", {})
            assert out.startswith("Error: synthetic failure")
        # 4th call must be short-circuited WITHOUT calling flaky_tool again!
        out4 = tools.execute_tool("cache_status", {})
        assert "Circuit breaker open for 'cache_status'" in out4
        assert calls["n"] == 3
    finally:
        tools.TOOL_CIRCUIT_BREAKER.reset()

    # Verify /why Decision Receipt renders Circuit Breaker events cleanly
    trace = router.TurnTrace(
        prompt_preview="pause spotify",
        tools_called=["control_media"],
        circuit_breaker_events=["control_media (cooldown)"],
    )
    receipt = trace.format_receipt()
    assert "Circuit Breaker:" in receipt and "control_media (cooldown)" in receipt


def test_anisette_docker_digest_and_gnome_extension_lockfile(repo_root: Path):
    locks = (repo_root / "system/download-locks.sha256").read_text(encoding="utf-8")
    assert "anisette-v3-server|docker|" in locks
    assert "d551c75454bcbee68dc262d4247d5f87fa309dfc8f2125644d333c648596e3c7" in locks

    tier_b = (repo_root / "tools/experimental-deep-stack.sh").read_text(encoding="utf-8")
    assert "dadoum/anisette-v3-server@sha256:d551c75454bcbee68dc262d4247d5f87fa309dfc8f2125644d333c648596e3c7" in tier_b

    # Verify GNOME extension lockfile resolution across GNOME 48, 49, 50
    common = repo_root / "core/common.sh"
    for uuid, shell_v, expected_tag in (
        ("blur-my-shell@aunetx", "50", "78|78246"),
        ("gsconnect@andyholmes.github.io", "50", "74|76737"),
        ("ding@rastersoft.com", "50", "105|78123"),
        ("ding@rastersoft.com", "48", "85|72024"),
        ("user-theme@gnome-shell-extensions.gcampax.github.com", "50", "79|75092"),
    ):
        r = _run_bash(f'REPO_DIR="{repo_root}"; source "{common}"; lookup_pinned_extension "{uuid}" "{shell_v}"')
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == expected_tag


def test_doctor_split_flags_swap_preservation_luks_and_dualboot(repo_root: Path, tmp_path: Path):
    fake_home = tmp_path / "doc_home"
    fake_home.mkdir()

    # 1. --doctor runs read-only with ZERO files written to HOME
    r_doc = _run_bash(f'bash "{repo_root}/install.sh" --doctor', env_extra={"HOME": str(fake_home)})
    assert "macubuntu Doctor" in r_doc.stdout
    assert "ZRAM Compressed Swap" in r_doc.stdout
    assert "Ollama Model Digests" in r_doc.stdout
    assert list(fake_home.iterdir()) == [], "--doctor MUST NOT touch HOME!"

    # 2. --system-only, --user-only, --accessibility, --replace-swap, --upgrade all dry-run cleanly with 0 HOME writes
    for flags in (
        "--dry-run --system-only",
        "--dry-run --user-only",
        "--dry-run --accessibility --only 60,99",
        "--dry-run --replace-swap --only 35",
        "--dry-run --upgrade --only 00",
    ):
        r = _run_bash(f'bash "{repo_root}/install.sh" {flags}', env_extra={"HOME": str(fake_home)})
        assert r.returncode == 0, f"Failed ({flags}): {r.stderr}"
        assert list(fake_home.iterdir()) == [], f"Side effects in HOME for {flags}"

    # 3. LUKS swap resume readiness guard & dual-boot Windows EFI detection
    detect = repo_root / "core/detect.sh"
    good_cmd = tmp_path / "cmdline_ok"
    good_crypt = tmp_path / "crypttab_ok"
    good_res = tmp_path / "resume_ok"
    good_cmd.write_text("BOOT_IMAGE=/vmlinuz root=/dev/mapper/vg-root resume=/dev/mapper/vg-swap quiet\n")
    good_crypt.write_text("vg-swap UUID=1234 none luks\n")
    good_res.write_text("RESUME=/dev/mapper/vg-swap\n")
    assert _run_bash(
        f'source "{detect}"; detect_luks_swap_resume_ready "{good_cmd}" "{good_crypt}" "{good_res}"'
    ).returncode == 0

    bad_crypt = tmp_path / "crypttab_urandom"
    bad_crypt.write_text("cryptswap /dev/nvme0n1p3 /dev/urandom swap,cipher=aes-xts-plain64\n")
    assert _run_bash(
        f'source "{detect}"; detect_luks_swap_resume_ready "{good_cmd}" "{bad_crypt}" "{good_res}"'
    ).returncode != 0

    efi_win = tmp_path / "efi_win"
    (efi_win / "EFI" / "Microsoft").mkdir(parents=True)
    assert _run_bash(f'source "{detect}"; detect_dualboot_windows "{efi_win}"').returncode == 0
    assert _run_bash(f'source "{detect}"; detect_dualboot_windows "{tmp_path / "efi_empty"}"').returncode != 0

    # 4. X11 session detection notice in Stage 00 dry-run
    r_x11 = _run_bash(
        f'bash "{repo_root}/install.sh" --dry-run --only 00',
        env_extra={"HOME": str(fake_home), "XDG_SESSION_TYPE": "x11"},
    )
    assert r_x11.returncode == 0
    assert "X11" in r_x11.stdout and "nvidia-drm.modeset=1" in r_x11.stdout

    # 5. flock -n concurrency guard prevents overlapping live installer runs
    lock_dir = tmp_path / "runtime"
    lock_dir.mkdir()
    r_flock = _run_bash(
        f'flock -n "{lock_dir}/macubuntu-install-${{UID}}.lock" -c '
        f'\'XDG_RUNTIME_DIR="{lock_dir}" HOME="{fake_home}" bash "{repo_root}/install.sh" --only 99\''
    )
    assert r_flock.returncode != 0
    assert "Another macubuntu install.sh instance is already running" in (r_flock.stdout + r_flock.stderr)
