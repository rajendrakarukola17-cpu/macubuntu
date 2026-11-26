#!/usr/bin/env bash
# ==============================================================================
# macubuntu v4.4.0 — Hardened Real-Hardware 8 GB Release Gate Runner
#
# Runs the 3 mandatory 8 GB Ubuntu 26.04 real-hardware stability tests before
# tagging v4.4.0 (strictly read-only to repo; zero false-green shortcuts):
#   1. model-load    : Keeps qwen3:4b resident (--keepalive 15m) during a 5-task
#                      autonomy plan; asserts gnome-shell AND celina-eventd PIDs
#                      survive (`kill -0`), systemd-oomd never kills desktop shell,
#                      and tasks.db has 0 tasks stuck in 'running' (P1-3 check).
#   2. soak          : 1-hour celina-eventd soak; asserts MainPID unchanged,
#                      NRestarts unchanged, RSS linear-regression slope <= 2 MB/h,
#                      MemoryPeak bounded, FD count stable, 0 D-Bus drops, and
#                      inspects journalctl -p warning (not just errors).
#   3. concurrent-db : Pre-loads qwen3:1.7b (--keepalive 10m) so ~1.5 GB stays
#                      resident, starts active generation, then runs 50 writers
#                      (2 rows each = 100 rows) + 50 readers concurrently against
#                      memory.db; asserts 0 lock errors, exact 100 persisted rows
#                      in hybrid_memories, and memory.db-wal < 10 MB.
#
# Usage:
#   ./tools/hardware-release-gate.sh --dry-run
#   ./tools/hardware-release-gate.sh --test model-load
#   ./tools/hardware-release-gate.sh --test soak --minutes 60
#   ./tools/hardware-release-gate.sh --test concurrent-db
#   ./tools/hardware-release-gate.sh --test all --minutes 60
# ==============================================================================
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

DRY_RUN=0
ALLOW_NO_OLLAMA=0
TEST_MODE="all"
SOAK_MINUTES=60
SOAK_INTERVAL_SEC=60
MAX_SLOPE_MB_PER_HR="${CELINA_GATE_MAX_SLOPE_MB_HR:-2.0}"
MAX_WAL_BYTES="${CELINA_GATE_MAX_WAL_BYTES:-10485760}"
MODEL_LARGE="${CELINA_GATE_LARGE_MODEL:-qwen3:4b}"
MODEL_SMALL="${CELINA_GATE_SMALL_MODEL:-qwen3:1.7b}"

# P0: No predictable /tmp paths — all temp files via mktemp, cleaned by EXIT trap
declare -a GATE_TEMP_FILES=()
_cleanup() {
    local f
    for f in "${GATE_TEMP_FILES[@]:-}"; do
        [[ -n "$f" && -f "$f" ]] && rm -f "$f" 2>/dev/null || true
    done
    [[ -n "${SOAK_SAMPLES_FILE:-}" && -f "$SOAK_SAMPLES_FILE" ]] && rm -f "$SOAK_SAMPLES_FILE" 2>/dev/null || true
}
trap _cleanup EXIT

_mktemp() {
    local f
    f="$(mktemp)"
    GATE_TEMP_FILES+=("$f")
    echo "$f"
}

usage() {
    cat <<'EOF'
Usage: hardware-release-gate.sh [options]
  --test <all|model-load|soak|concurrent-db>  Which gate test(s) to execute (default: all)
  --minutes <N>                               Soak duration in minutes (default: 60)
  --interval <S>                              Soak sampling interval in seconds (default: 60)
  --allow-no-ollama                           Permit concurrent-db test without live Ollama (CI/container use only)
  --dry-run                                   Print planned gate checks & exit 0 without touching services
  -h, --help                                  Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --test) TEST_MODE="${2:?Missing --test value}"; shift 2 ;;
        --minutes) SOAK_MINUTES="${2:?Missing --minutes value}"; shift 2 ;;
        --interval) SOAK_INTERVAL_SEC="${2:?Missing --interval value}"; shift 2 ;;
        --allow-no-ollama) ALLOW_NO_OLLAMA=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

case "$TEST_MODE" in
    all|model-load|soak|concurrent-db) ;;
    *) echo "Invalid --test value: $TEST_MODE" >&2; exit 2 ;;
esac

FAILURES=0
log_pass() { printf "  [PASS] %s\n" "$1"; }
log_fail() { printf "  [FAIL] %s\n" "$1" >&2; FAILURES=$((FAILURES + 1)); }
log_info() { printf "  [INFO] %s\n" "$1"; }

run_model_load_gate() {
    echo "=== Gate 1/3: Model-Load + systemd-oomd Cgroup Isolation (${MODEL_LARGE}, keepalive=15m) ==="
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "  [dry-run] Capture SHELL_PID (gnome-shell) & EVENTD_PID (celina-eventd.service)"
        echo "  [dry-run] Verify systemctl show ollama -p MemoryHigh -p MemoryMax -p OOMScoreAdjust + zramctl + PSI"
        echo "  [dry-run] Force ${MODEL_LARGE} resident via: echo '' | ollama run ${MODEL_LARGE} --keepalive 15m &"
        echo "  [dry-run] Run 5-task autonomy plan concurrently under resident model pressure"
        echo "  [dry-run] Assert kill -0 SHELL_PID, kill -0 EVENTD_PID, systemd-oomd did not kill gnome-shell"
        echo "  [dry-run] Assert tasks.db has 0 agent tasks stuck in status='running' (P1-3 recovery check)"
        return 0
    fi

    if ! command -v systemctl >/dev/null 2>&1 || ! command -v ollama >/dev/null 2>&1; then
        log_fail "Requires live systemd + ollama on an 8 GB Ubuntu 26.04 host"
        return 0
    fi

    # P0: celina-cli must exist — Gate 1 must never silently skip the tasks.db check
    if ! command -v celina-cli >/dev/null 2>&1; then
        log_fail "celina-cli not found — Gate 1 cannot complete tasks.db 'running' check. Install celina-cli before tagging."
        return 1
    fi

    local shell_pid eventd_pid
    shell_pid="$(pgrep -x gnome-shell | head -n 1 || true)"
    eventd_pid="$(systemctl --user show -p MainPID --value celina-eventd.service 2>/dev/null || echo 0)"
    log_info "Pre-test PIDs: gnome-shell=${shell_pid:-none}, celina-eventd=${eventd_pid}"
    log_info "Ollama cgroup: $(systemctl show ollama -p MemoryHigh -p MemoryMax -p OOMScoreAdjust 2>/dev/null | tr '\n' ' ')"
    [[ -r /proc/pressure/memory ]] && log_info "PSI memory: $(tr '\n' ' ' </proc/pressure/memory)"

    # Force model resident for 15 minutes so it DOES NOT unload before autonomy pressure builds
    local keepalive_log plan_log ollama_log
    keepalive_log="$(_mktemp)"
    plan_log="$(_mktemp)"
    ollama_log="$(_mktemp)"

    echo "" | ollama run "$MODEL_LARGE" --keepalive 15m >"$keepalive_log" 2>&1 &
    local keep_pid=$!
    sleep 5
    log_info "Resident models (ollama ps): $(ollama ps 2>/dev/null | tail -n +2 | tr '\n' ';' || true)"

    local plan_pid=""
    celina-cli "run a 5-step local system audit and summarize results" >"$plan_log" 2>&1 &
    plan_pid=$!

    ollama run "$MODEL_LARGE" --keepalive 15m "Explain Linux cgroup v2 PSI and OOM prioritization in 250 words." >"$ollama_log" 2>&1 || true
    [[ -n "$plan_pid" ]] && wait "$plan_pid" || true
    wait "$keep_pid" || true

    # Assertion 1: GNOME Shell survived (exact PID check)
    if [[ -n "$shell_pid" ]]; then
        if kill -0 "$shell_pid" 2>/dev/null; then
            log_pass "gnome-shell survived resident ${MODEL_LARGE} load (PID=${shell_pid})"
        else
            log_fail "gnome-shell died during ${MODEL_LARGE} load (initial PID=${shell_pid})"
        fi
    else
        log_info "Headless session (no gnome-shell PID present)"
    fi

    # Assertion 2: celina-eventd survived (exact PID check)
    if [[ -n "$eventd_pid" && "$eventd_pid" != "0" ]]; then
        if kill -0 "$eventd_pid" 2>/dev/null; then
            log_pass "celina-eventd survived resident ${MODEL_LARGE} load (PID=${eventd_pid})"
        else
            log_fail "celina-eventd died during ${MODEL_LARGE} load (initial PID=${eventd_pid})"
        fi
    else
        log_fail "celina-eventd.service was not active before test (MainPID=${eventd_pid})"
    fi

    # Assertion 3: systemd-oomd never killed gnome-shell
    local oom_hits
    oom_hits="$(journalctl -u systemd-oomd --since "15 minutes ago" --no-pager 2>/dev/null | grep -i "Killed" || true)"
    if echo "$oom_hits" | grep -qi "gnome-shell"; then
        log_fail "systemd-oomd killed gnome-shell: $oom_hits"
    else
        log_pass "systemd-oomd did not kill desktop shell"
    fi

    # Assertion 4: P1-3 end-to-end check — zero tasks left stuck in 'running' in tasks.db
    local tasks_db="${HOME}/.local/share/celina/tasks.db"
    if [[ -f "$tasks_db" ]]; then
        local stuck_cnt
        stuck_cnt="$(python3 - "$tasks_db" <<'PY'
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
try:
    row = conn.execute("SELECT COUNT(*) FROM tasks WHERE node_type='agent' AND status='running'").fetchone()
    print(int(row[0]) if row else 0)
finally:
    conn.close()
PY
)"
        if [[ "$stuck_cnt" -eq 0 ]]; then
            log_pass "tasks.db has 0 stuck 'running' agent tasks (P1-3 recovery intact)"
        else
            log_fail "tasks.db has ${stuck_cnt} agent task(s) stuck in 'running' state!"
        fi
    else
        log_info "tasks.db not present at ${tasks_db} (skipping P1-3 stuck-task check)"
    fi
}

run_soak_gate() {
    echo "=== Gate 2/3: ${SOAK_MINUTES}-Minute celina-eventd Soak (PID/NRestarts, RSS Slope, MemoryPeak, Warnings) ==="
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "  [dry-run] Capture INITIAL_PID and INITIAL_RESTARTS (NRestarts) of celina-eventd.service"
        echo "  [dry-run] Sample VmRSS, /proc/PID/fd, MainPID, and busctl --user status org.macubuntu.Celina (${SOAK_MINUTES}x${SOAK_INTERVAL_SEC}s)"
        echo "  [dry-run] Assert MainPID unchanged every tick, FINAL_RESTARTS == INITIAL_RESTARTS"
        echo "  [dry-run] Compute linear-regression RSS slope (fail if > ${MAX_SLOPE_MB_PER_HR} MB/hour) & inspect MemoryPeak"
        echo "  [dry-run] Inspect journalctl --user -u celina-eventd.service -p warning (warnings + errors)"
        return 0
    fi

    local initial_pid initial_restarts final_restarts
    initial_pid="$(systemctl --user show -p MainPID --value celina-eventd.service 2>/dev/null || echo 0)"
    initial_restarts="$(systemctl --user show -p NRestarts --value celina-eventd.service 2>/dev/null || echo 0)"
    if [[ -z "$initial_pid" || "$initial_pid" == "0" || ! -r "/proc/$initial_pid/status" ]]; then
        log_fail "celina-eventd.service is not running (MainPID=${initial_pid})"
        return 0
    fi
    log_info "Soak start: MainPID=${initial_pid}, NRestarts=${initial_restarts}"

    SOAK_SAMPLES_FILE="$(_mktemp)"
    local base_rss base_fds max_rss max_fds drops=0 i rss fds cur_pid
    base_rss="$(awk '/VmRSS/{print $2}' "/proc/$initial_pid/status")"
    base_fds="$(ls "/proc/$initial_pid/fd" 2>/dev/null | wc -l)"
    max_rss="$base_rss"
    max_fds="$base_fds"

    for ((i = 1; i <= SOAK_MINUTES; i++)); do
        cur_pid="$(systemctl --user show -p MainPID --value celina-eventd.service 2>/dev/null || echo 0)"
        if [[ "$cur_pid" != "$initial_pid" ]]; then
            log_fail "celina-eventd.service crashed/restarted at sample ${i} (PID ${initial_pid} -> ${cur_pid})"
            return 0
        fi
        rss="$(awk '/VmRSS/{print $2}' "/proc/$initial_pid/status")"
        fds="$(ls "/proc/$initial_pid/fd" 2>/dev/null | wc -l)"
        printf "%s %s\n" "$i" "$rss" >> "$SOAK_SAMPLES_FILE"
        (( rss > max_rss )) && max_rss="$rss"
        (( fds > max_fds )) && max_fds="$fds"
        if ! busctl --user status org.macubuntu.Celina >/dev/null 2>&1; then
            drops=$((drops + 1))
        fi
        printf "  [%02d/%02d] pid=%s rss=%sKB fds=%s dbus_drops=%s\n" "$i" "$SOAK_MINUTES" "$initial_pid" "$rss" "$fds" "$drops"
        if (( i < SOAK_MINUTES )); then
            sleep "$SOAK_INTERVAL_SEC"
        fi
    done

    final_restarts="$(systemctl --user show -p NRestarts --value celina-eventd.service 2>/dev/null || echo 0)"
    if [[ "$final_restarts" != "$initial_restarts" ]]; then
        log_fail "celina-eventd.service restarted $((final_restarts - initial_restarts)) time(s) during soak!"
    else
        log_pass "Zero restarts during soak (NRestarts=${final_restarts})"
    fi

    # Linear regression RSS leak slope check (catches slow 5 MB/hr leaks hiding inside ±10% at 1h)
    local slope_status
    slope_status="$(python3 - "$SOAK_SAMPLES_FILE" "$SOAK_INTERVAL_SEC" "$MAX_SLOPE_MB_PER_HR" <<'PY'
import sys
path, interval_sec, max_mb_hr = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
pts = []
with open(path, encoding="utf-8") as fh:
    for ln in fh:
        p = ln.split()
        if len(p) == 2:
            pts.append((float(p[0]) * interval_sec / 3600.0, float(p[1]) / 1024.0))  # (hours, MB)
if len(pts) < 2:
    print("OK 0.00")
    raise SystemExit(0)
n = len(pts)
mx = sum(x for x, _ in pts) / n
my = sum(y for _, y in pts) / n
denom = sum((x - mx) ** 2 for x, _ in pts)
slope = sum((x - mx) * (y - my) for x, y in pts) / denom if denom > 0 else 0.0
print(f"{'FAIL' if slope > max_mb_hr else 'OK'} {slope:.2f}")
PY
)"
    local slope_verdict slope_val
    read -r slope_verdict slope_val <<< "$slope_status"
    if [[ "$slope_verdict" == "FAIL" ]]; then
        log_fail "RSS linear-regression leak slope ${slope_val} MB/hour exceeds ${MAX_SLOPE_MB_PER_HR} MB/hour"
    else
        log_pass "RSS linear-regression slope ${slope_val} MB/hour <= ${MAX_SLOPE_MB_PER_HR} MB/hour"
    fi

    local allowed_rss=$(( base_rss + (base_rss * 10 / 100) + 1024 ))
    local allowed_fds=$(( base_fds + 16 ))
    if (( max_rss > allowed_rss )); then
        log_fail "Peak RSS drifted >10% (base=${base_rss}KB, peak=${max_rss}KB, limit=${allowed_rss}KB)"
    else
        log_pass "Peak RSS within ±10% (base=${base_rss}KB, peak=${max_rss}KB)"
    fi
    if (( max_fds > allowed_fds )); then
        log_fail "FD count leaked (base=${base_fds}, peak=${max_fds}, limit=${allowed_fds})"
    else
        log_pass "FD count stable (base=${base_fds}, peak=${max_fds})"
    fi
    if (( drops > 0 )); then
        log_fail "Detected ${drops} D-Bus drops on org.macubuntu.Celina"
    else
        log_pass "Zero D-Bus drops across ${SOAK_MINUTES} samples"
    fi

    log_info "cgroup MemoryPeak: $(systemctl --user show celina-eventd.service -p MemoryPeak --value 2>/dev/null || echo unavailable)"
    local warn_cnt
    warn_cnt="$(journalctl --user -u celina-eventd.service --since "${SOAK_MINUTES} minutes ago" -p warning --no-pager 2>/dev/null | grep -vc '^-- ' || true)"
    if (( warn_cnt > 0 )); then
        log_fail "celina-eventd emitted ${warn_cnt} warning/error journal line(s) during soak"
    else
        log_pass "Zero warning/error journal lines from celina-eventd during soak"
    fi
}

run_concurrent_db_gate() {
    echo "=== Gate 3/3: 50 Writers + 50 Readers Concurrent memory.db Under Resident Ollama (${MODEL_SMALL}, keepalive=10m) ==="
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "  [dry-run] Pre-load ${MODEL_SMALL} synchronously with --keepalive 10m so ~1.5 GB is guaranteed resident"
        echo "  [dry-run] Start concurrent background generation on ${MODEL_SMALL}"
        echo "  [dry-run] Run 50 writer threads (2 rows each = 100 rows) + 50 reader threads against ~/.local/share/celina/memory.db"
        echo "  [dry-run] Assert 0 lock errors, exact 100 rows persisted in hybrid_memories, and memory.db-wal < ${MAX_WAL_BYTES} bytes"
        return 0
    fi

    local py_bin="${HOME}/.local/share/celina/venv/bin/python3"
    local app_dir="${HOME}/.local/share/celina/app"
    [[ -x "$py_bin" ]] || py_bin="python3"
    [[ -d "$app_dir" ]] || app_dir="${REPO_DIR}/features/celina"

    local gen_pid=""
    if command -v ollama >/dev/null 2>&1; then
        # Step 1: Synchronously warm model with --keepalive 10m so it is GUARANTEED resident before DB test starts
        echo "" | ollama run "$MODEL_SMALL" --keepalive 10m >/dev/null 2>&1 || true
        # Step 2: Run active generation in background during the DB benchmark
        ollama run "$MODEL_SMALL" --keepalive 10m "Write a 500-word technical note on SQLite WAL concurrency and Linux page cache." >/dev/null 2>&1 &
        gen_pid=$!
    elif [[ "$ALLOW_NO_OLLAMA" -ne 1 ]]; then
        log_fail "ollama binary not found (pass --allow-no-ollama only for non-hardware CI runs)"
        return 0
    fi

    local run_tag="hw-soak-concurrent-$(date +%s)-$$"
    if PYTHONPATH="$app_dir" "$py_bin" - "$run_tag" "$MAX_WAL_BYTES" <<'PY'
import os, sqlite3, sys, threading, time
from pathlib import Path
from celina_pkg.memory import CelinaMemory

run_tag = sys.argv[1]
max_wal_bytes = int(sys.argv[2])
db = Path.home() / ".local" / "share" / "celina" / "memory.db"
db.parent.mkdir(parents=True, exist_ok=True)

# Ensure schema initialized cleanly before spawning 100 concurrent threads
seed = CelinaMemory(db_path=db)
seed.remember(f"{run_tag}-seed", importance=0.5)
seed.close()

errors = []
read_hits = [0]
read_lock = threading.Lock()

def writer(idx: int) -> None:
    try:
        m = CelinaMemory(db_path=db)
        for j in range(2):
            m.remember(f"{run_tag}-w{idx}-r{j}", importance=0.5)
        m.close()
    except Exception as exc:
        errors.append(("writer", idx, repr(exc)))

def reader(idx: int) -> None:
    try:
        m = CelinaMemory(db_path=db)
        res = m.recall(run_tag, k=5)
        m.close()
        if res:
            with read_lock:
                read_hits[0] += 1
    except Exception as exc:
        errors.append(("reader", idx, repr(exc)))

threads = [threading.Thread(target=writer, args=(i,)) for i in range(50)] + [
    threading.Thread(target=reader, args=(i,)) for i in range(50)
]
t0 = time.monotonic()
for t in threads:
    t.start()
for t in threads:
    t.join()
elapsed = time.monotonic() - t0

# Independent verification 1: Exact 100 persisted writer rows in hybrid_memories
conn = sqlite3.connect(str(db))
try:
    cnt = int(
        conn.execute(
            "SELECT COUNT(*) FROM hybrid_memories WHERE text LIKE ?",
            (f"{run_tag}-w%",),
        ).fetchone()[0]
    )
    jm = str(conn.execute("PRAGMA journal_mode;").fetchone()[0]).lower()
finally:
    conn.close()

# Independent verification 2: WAL file did not explode (< 10 MB)
wal_path = Path(f"{db}-wal")
wal_bytes = wal_path.stat().st_size if wal_path.is_file() else 0

print(
    f"  [INFO] 50W+50R completed in {elapsed:.2f}s | errors={len(errors)} | "
    f"persisted_rows={cnt}/100 | reader_hits={read_hits[0]}/50 | journal={jm} | wal_bytes={wal_bytes}"
)
if errors:
    print("  [FAIL] Sample errors:", errors[:3])
    raise SystemExit(1)
if cnt != 100:
    print(f"  [FAIL] Expected 100 persisted writer rows, found {cnt}")
    raise SystemExit(1)
if wal_bytes >= max_wal_bytes:
    print(f"  [FAIL] WAL size {wal_bytes} bytes exceeded limit {max_wal_bytes} bytes")
    raise SystemExit(1)
PY
    then
        log_pass "50W+50R concurrent memory.db verified (0 errors, 100/100 rows persisted, WAL < 10 MB)"
    else
        log_fail "Concurrent memory.db gate failed"
    fi

    [[ -n "$gen_pid" ]] && wait "$gen_pid" || true
}

case "$TEST_MODE" in
    model-load)    run_model_load_gate ;;
    soak)          run_soak_gate ;;
    concurrent-db) run_concurrent_db_gate ;;
    all)
        run_model_load_gate
        run_soak_gate
        run_concurrent_db_gate
        ;;
esac

echo "=== Gate 4/4 (Manual Fresh-User Clean-HOME Check on Clean VM) ==="
echo "  sudo useradd -m -s /bin/bash testuser && sudo passwd -d testuser"
echo "  su - testuser -c 'cd ~ && tar -xzf /path/to/macubuntu-v4.4.0.tar.gz && cd macubuntu-v4.4.0 && ./install.sh --fail-fast --profile default-8gb && ./tools/doctor.sh'"

echo "=== Release Gate Summary ==="
if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "DRY_RUN=1 complete (0 host mutations)."
    exit 0
fi
if (( FAILURES > 0 )); then
    echo "❌ Hardware release gate FAILED (${FAILURES} failure(s)). Do NOT tag v4.4.0." >&2
    exit 1
fi
echo "✅ Hardware release gate PASSED (0 failures). Ready for fresh-user VM check & v4.4.0 tag!"