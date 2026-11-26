# =============================================================================
# CELINA UPDATE 20 — PHASE 1 P0 SAFETY FIXES (CONSOLIDATED PATCH FILE)
# =============================================================================
#
# STATUS: DOCUMENTED ONLY — NOT APPLIED
# This file contains all P0 safety fixes gathered from the Phase 0 baseline
# audit. Each fix includes:
#   - Bug ID
#   - Evidence (from baseline.json / live run)
#   - File(s) affected
#   - Patch (unified diff format, ready to `git apply` or `patch -p1`)
#   - Verification test
#
# These must ALL be applied BEFORE any consolidation/refactor work begins.
# safe_zero_breakage rule: no public function/tool deleted or renamed.
#
# =============================================================================

# -----------------------------------------------------------------------------
# BUG P0-01: Case destruction in gear0_macro.py
# -----------------------------------------------------------------------------
# EVIDENCE [V]:
#   Input:  add macro addr as "123 Main St"
#   Output: ✓ Macro saved: 'addr' → '"123 main st"'
#   Root cause: query.lower() is applied before regex extraction, then the
#               lowercased value is stored. Value case is destroyed.
# FILES:
#   celina_pkg/gear0_macro.py
# PATCH:
# -----------------------------------------------------------------------------
# --- a/celina_pkg/gear0_macro.py
# +++ b/celina_pkg/gear0_macro.py
# @@ -84,11 +84,11 @@ def gear0_macro_classify(query: str) -> Optional[str]:
#      _EXPAND_RE = __import__("re").compile(...)
#      _ADD_MACRO_RE = __import__("re").compile(...)
#      _LIST_MACROS_RE = __import__("re").compile(...)
# 
#      def gear0_macro_classify(query: str) -> Optional[str]:
#          low = query.strip().lower()
#          if _EXPAND_RE.match(low):
#              return "expand"
#          if _ADD_MACRO_RE.match(low):
#              return "add"
#          if _LIST_MACROS_RE.match(low):
#              return "list"
#          return None
# 
#      def gear0_macro_execute(handler: str, query: str, dry_run: bool = False) -> Optional[str]:
#          try:
#              import re
#              if handler == "expand":
-                 m = _EXPAND_RE.match(query.lower())
+                 m = _EXPAND_RE.match(query)   # re.IGNORECASE in pattern; preserve case
#                  if m:
-                     return macro_expand(m.group(1))
+                     return macro_expand(m.group(1))   # original case preserved
#              if handler == "add":
-                 m = _ADD_MACRO_RE.match(query.lower())
+                 m = _ADD_MACRO_RE.match(query)   # re.IGNORECASE in pattern; preserve case
#                  if m:
-                     return macro_add(m.group(1), m.group(2).strip())
+                     return macro_add(m.group(1), m.group(2).strip())   # original case preserved
#              if handler == "list":
#                  return macro_list()
#          except Exception:
#              return None
#          return None
# 
#      # All regex patterns compiled with re.IGNORECASE flag (already present
#      # in _EXPAND_RE, _ADD_MACRO_RE, _LIST_MACROS_RE definitions).
# -----------------------------------------------------------------------------
# VERIFICATION:
#   python3 -c "
#   from celina_pkg.gear0_macro import gear0_macro_classify, gear0_macro_execute
#   h = gear0_macro_classify('add macro addr as \"123 Main St\"')
#   r = gear0_macro_execute(h, 'add macro addr as \"123 Main St"', dry_run=False)
#   assert '123 Main St' in str(r), f'Case destroyed: {r}'
#   assert 'main st' not in str(r).lower() or '123 Main St' in str(r)
#   print('P0-01 PASS: case preserved')
#   "
#
# -----------------------------------------------------------------------------
# BUG P0-02: Destructive trash fallback in plugins/self_heal.py
# -----------------------------------------------------------------------------
# EVIDENCE [V]:
#   self_heal.py line 87: fallback=FallbackStep("find_trash", "find",
#                                               ["~/.local/share/Trash", "-delete"])
#   Risk: `find -delete` on user paths can destroy non-trash files if path
#   resolution is wrong. Also os_shim.py uses shutil.rmtree on trash items.
# FILES:
#   celina_pkg/plugins/self_heal.py
#   celina_pkg/plugins/os_shim.py
# PATCH (self_heal.py):
# -----------------------------------------------------------------------------
# --- a/celina_pkg/plugins/self_heal.py
# +++ b/celina_pkg/plugins/self_heal.py
# @@ -80,10 +80,10 @@ DEFAULT_CHAINS: dict[str, FallbackChain] = {
#             action="empty_trash",
#             primary=FallbackStep("python_shim", "python"),
#             secondary=FallbackStep("rm_trash", "rm", ["-rf", "~/.local/share/Trash/files/*"]),
#-            fallback=FallbackStep("find_trash", "find", ["~/.local/share/Trash", "-delete"]),
#+            fallback=FallbackStep("send2trash", "python", ["-m", "send2trash", "~/.local/share/Trash/files/*"]),
#         ),
#     }
# -----------------------------------------------------------------------------
# PATCH (os_shim.py - empty_trash Linux branch):
# -----------------------------------------------------------------------------
# --- a/celina_pkg/plugins/os_shim.py
# +++ b/celina_pkg/plugins/os_shim.py
# @@ -128,14 +128,14 @@ def empty_trash(self) -> ShimResult:
#             trash_dirs = [
#                 Path.home() / ".local" / "share" / "Trash" / "files",
#                 Path.home() / ".local" / "share" / "Trash" / "info",
#             ]
#             removed = 0
#             for d in trash_dirs:
#                 if d.exists():
#                     for item in d.iterdir():
#                         try:
#                             if item.is_dir():
#-                                shutil.rmtree(item)
+                                shutil.move(str(item), str(Path.home() / ".local" / "share" / "Trash" / "files" / f"pending_{item.name}"))
#                             else:
#-                                item.unlink()
+                                shutil.move(str(item), str(Path.home() / ".local" / "share" / "Trash" / "files" / f"pending_{item.name}"))
#                             removed += 1
#                         except Exception:
#                             continue
# -----------------------------------------------------------------------------
# DEPENDENCY: add send2trash to requirements
# VERIFICATION:
#   - No `rm -rf` or `find -delete` on user paths (grep == 0)
#   - send2trash used for trash operations
#   - Trash files recoverable from ~/.local/share/Trash/files/
# -----------------------------------------------------------------------------
# BUG P0-03: Missing backend_state.json (no backend state tracking)
# -----------------------------------------------------------------------------
# EVIDENCE [V]:
#   /home/user/.celina/storage/shared/backend_state.json does not exist
#   get_backend_state() returns defaults but never persists
# FILES:
#   celina_pkg/gear0_storage.py
#   (caller: apply-update.sh or router startup)
# PATCH:
# -----------------------------------------------------------------------------
# --- a/celina_pkg/gear0_storage.py
# +++ b/celina_pkg/gear0_storage.py
# @@ -100,6 +100,12 @@ def get_backend_state() -> dict[str, Any]:
#     return {
#         "active_backend": "ollama",
#         "last_switch": None,
#         "switch_count": 0,
#         "data_preserved": True,
#     }
# 
# 
# +def initialize_storage() -> None:
# +    """Call once at Celina startup. Creates layout + writes initial state."""
# +    ensure_storage_layout()
# +    if not BACKEND_STATE_FILE.exists():
# +        set_backend_state(get_backend_state())
# +
# 
# -----------------------------------------------------------------------------
# CALLER PATCH (router.py - import + call at module init):
# -----------------------------------------------------------------------------
# --- a/celina_pkg/router.py
# +++ b/celina_pkg/router.py
# @@ -119,6 +119,7 @@ from . import (
#     voice,
# )
# +from .gear0_storage import initialize_storage
# 
# # Initialize storage backend state at import time
# +initialize_storage()
# 
# -----------------------------------------------------------------------------
# VERIFICATION:
#   - backend_state.json exists after import
#   - get_backend_state() returns persisted values
# -----------------------------------------------------------------------------
# BUG P0-04: Scattered storage roots (data integrity risk)
# -----------------------------------------------------------------------------
# EVIDENCE [V]:
#   11 databases across 3 roots:
#     /home/user/.celina/assistant.db
#     /home/user/.celina/lists.db
#     /home/user/.celina/iot.db
#     /home/user/.celina/routines.db
#     /home/user/.celina/graph.db
#     /home/user/.celina/storage/shared/habits.db
#     /home/user/.celina/storage/shared/celina_shared.db
#     /home/user/.celina/storage/shared/self_heal.db
#     /home/user/.celina/storage/checkpoints/test_1795017206/celina_shared.db
#     /home/user/.local/share/celina/gear0_ledger.db
#     /home/user/.local/share/celina/memory.db
#   Plus: /home/user/.config/celina/gear0_macros.json
#   Checkpoint manifest only covers 3 files → rollback loses ledger/macros/habits/self-heal
# FILES:
#   celina_pkg/gear0_ledger.py      (_DB_PATH = ~/.local/share/celina/gear0_ledger.db)
#   celina_pkg/gear0_macro.py       (_CONFIG_PATH = ~/.config/celina/gear0_macros.json)
#   celina_pkg/gear0_storage.py     (checkpoint manifest incomplete)
#   celina_pkg/plugins/habit_engine.py (_DB_PATH = ~/.celina/storage/shared/habits.db)
#   celina_pkg/plugins/self_heal.py (_DB_PATH = ~/.celina/storage/shared/self_heal.db)
#   celina_pkg/gear0_brain.py       (_BRAIN_DB_PATH = ~/.celina/assistant.db)
#   celina_pkg/gear0_graph.py       (_CONTACTS_DB_PATH = ~/.celina/graph.db)
#   celina_pkg/gear0_lists.py       (_LISTS_DB_PATH = ~/.celina/lists.db)
#   celina_pkg/gear0_routines.py    (_ROUTINES_DB_PATH = ~/.celina/routines.db)
#   celina_pkg/gear0_iot.py         (_IOT_DB_PATH = ~/.celina/iot.db)
#   celina_pkg/gear0_memory.py      (_MEMORY_DB_PATH = ~/.celina/memory.db)
# PATCH: Point all DBs and configs under ~/.celina/storage/
# -----------------------------------------------------------------------------
# --- a/celina_pkg/gear0_ledger.py
# +++ b/celina_pkg/gear0_ledger.py
# @@ -18,7 +18,7 @@
#      def _get_conn() -> sqlite3.Connection:
# -        _DB_PATH = Path.home() / ".local" / "share" / "celina" / "gear0_ledger.db"
# +        _DB_PATH = Path.home() / ".celina" / "storage" / "shared" / "gear0_ledger.db"
# -----------------------------------------------------------------------------
# --- a/celina_pkg/gear0_macro.py
# +++ b/celina_pkg/gear0_macro.py
# @@ -16,7 +16,7 @@
# -    _CONFIG_PATH = Path.home() / ".config" / "celina" / "gear0_macros.json"
# +    _CONFIG_PATH = Path.home() / ".celina" / "storage" / "shared" / "gear0_macros.json"
# -----------------------------------------------------------------------------
# --- a/celina_pkg/gear0_storage.py  (checkpoint manifest)
# +++ b/celina_pkg/gear0_storage.py
#   CHECKPOINT_MANIFEST = [
#       "shared/celina_shared.db",
#       "shared/habits.db",
#       "shared/self_heal.db",
# +     "shared/gear0_ledger.db",
# +     "shared/gear0_macros.json",
#       "shared/backend_state.json",
#   ]
# -----------------------------------------------------------------------------
# VERIFICATION:
#   - All *.db and macros.json under ~/.celina/storage/
#   - create_checkpoint() + restore_checkpoint() covers all DBs
#   - write → switch → restore → assert on ALL databases
# -----------------------------------------------------------------------------
# BUG P0-05: shell=True in plugins/os_shim.py (12 occurrences)
# -----------------------------------------------------------------------------
# EVIDENCE [V]:
#   12 shell=True calls in os_shim.py (rundll32, shutdown, powershell, etc.)
#   Risk: shell injection if any argument contains user-controlled input
# FILES:
#   celina_pkg/plugins/os_shim.py
# PATCH: Replace shell=True with argv-list equivalents
# -----------------------------------------------------------------------------
# --- a/celina_pkg/plugins/os_shim.py
# +++ b/celina_pkg/plugins/os_shim.py
# @@ -80,7 +80,7 @@ def sleep_computer(self) -> ShimResult:
#             return self._run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], "rundll32 SetSuspendState", shell=True)
# +            return self._run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], "rundll32 SetSuspendState")  # shell=False
# 
# @@ -90,7 +90,7 @@ def shutdown(self) -> ShimResult:
#             return self._run(["shutdown", "/s", "/t", "0"], "shutdown /s /t 0", shell=True)
# +            return self._run(["shutdown", "/s", "/t", "0"], "shutdown /s /t 0")  # shell=False
# 
# @@ -100,7 +100,7 @@ def restart(self) -> ShimResult:
#             return self._run(["shutdown", "/r", "/t", "0"], "shutdown /r /t 0", shell=True)
# +            return self._run(["shutdown", "/r", "/t", "0"], "shutdown /r /t 0")  # shell=False
# 
# @@ -112,7 +112,7 @@ def lock_screen(self) -> ShimResult:
#             return self._run(["rundll32.exe", "user32.dll,LockWorkStation"], "rundll32 LockWorkStation", shell=True)
# +            return self._run(["rundll32.exe", "user32.dll,LockWorkStation"], "rundll32 LockWorkStation")  # shell=False
# 
# @@ -143,7 +143,7 @@ def empty_trash(self) -> ShimResult:
#             return self._run(["powershell", "-Command", ps], "powershell Clear-RecycleBin", shell=True)
# +            return self._run(["powershell", "-Command", ps], "powershell Clear-RecycleBin")  # shell=False
# 
# @@ -156,7 +156,7 @@ def open_file(self, file_path: str) -> ShimResult:
#             return self._run(["start", "", str(path)], f"start {file_path}", shell=True)
# +            return self._run(["start", "", str(path)], f"start {file_path}")  # shell=False
# 
# @@ -166,7 +166,7 @@ def open_url(self, url: str) -> ShimResult:
#             return self._run(["start", "", url], f"start {url}", shell=True)
# +            return self._run(["start", "", url], f"start {url}")  # shell=False
# 
# @@ -182,7 +182,7 @@ def get_clipboard(self) -> ShimResult:
#             return self._run(["powershell", "-Command", "Get-Clipboard"], "powershell Get-Clipboard", shell=True)
# +            return self._run(["powershell", "-Command", "Get-Clipboard"], "powershell Get-Clipboard")  # shell=False
# 
# @@ -194,7 +194,7 @@ def set_clipboard(self, text: str) -> ShimResult:
#             return self._run(["powershell", "-Command", f"Set-Clipboard -Value '{text}'"], "powershell Set-Clipboard", shell=True)
# +            return self._run(["powershell", "-Command", f"Set-Clipboard -Value '{text}'"], "powershell Set-Clipboard")  # shell=False
# 
# @@ -206,7 +206,7 @@ def clear_clipboard(self) -> ShimResult:
#             return self._run(["powershell", "-Command", "Clear-Clipboard"], "powershell Clear-Clipboard", shell=True)
# +            return self._run(["powershell", "-Command", "Clear-Clipboard"], "powershell Clear-Clipboard")  # shell=False
# 
# @@ -229,7 +229,7 @@ def show_notification(self, title: str, body: str) -> ShimResult:
#             return self._run(["powershell", "-Command", ps], "powershell toast notification", shell=True)
# +            return self._run(["powershell", "-Command", ps], "powershell toast notification")  # shell=False
# 
# @@ -254,7 +254,7 @@ def take_screenshot(self, output_path: str = "/tmp/celina_screenshot.png") -> ShimResult:
#             return self._run(["powershell", "-Command", ps], "powershell screenshot", shell=True)
# +            return self._run(["powershell", "-Command", ps], "powershell screenshot")  # shell=False
# -----------------------------------------------------------------------------
# VERIFICATION:
#   - grep shell=True celina_pkg/plugins/os_shim.py == 0
#   - All Windows commands still functional (argv list form)
# -----------------------------------------------------------------------------
# BUG P0-06: Missing WAL + busy_timeout on ledger/audit DBs
# -----------------------------------------------------------------------------
# EVIDENCE [V]:
#   gear0_ledger.py: no journal_mode=WAL, no busy_timeout
#   gear0_audit.py: append-only log (no SQLite), but if converted, needs WAL
#   Concurrent writers (background swarm + Gear 0) → "database is locked"
# FILES:
#   celina_pkg/gear0_ledger.py
# PATCH:
# -----------------------------------------------------------------------------
# --- a/celina_pkg/gear0_ledger.py
# +++ b/celina_pkg/gear0_ledger.py
# @@ -22,6 +22,8 @@
#      def _get_conn() -> sqlite3.Connection:
#          _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
#          conn = sqlite3.connect(str(_DB_PATH))
# +        conn.execute("PRAGMA journal_mode=WAL")
# +        conn.execute("PRAGMA busy_timeout=3000")
#          conn.execute(
#              """CREATE TABLE IF NOT EXISTS expenses (
# -----------------------------------------------------------------------------
# VERIFICATION:
#   - All SQLite connections set WAL + busy_timeout
#   - Concurrent write test passes
# -----------------------------------------------------------------------------
# BUG P0-07: Missing per-step timeouts in self_heal fallback chain
# -----------------------------------------------------------------------------
# EVIDENCE [V]:
#   self_heal.py _execute_step(): subprocess.run(timeout=10) — single timeout
#   No per-step timeout policy; a hanging primary blocks forever
# FILES:
#   celina_pkg/plugins/self_heal.py
# PATCH:
# -----------------------------------------------------------------------------
# --- a/celina_pkg/plugins/self_heal.py
# +++ b/celina_pkg/plugins/self_heal.py
# @@ -1,5 +1,6 @@
+import signal
 # 
 # -----------------------------------------------------------------------------
 # @@ -117,7 +118,7 @@ def _execute_step(self, step: FallbackStep, *positional_args: str) -> tuple[bool, str, float]:
 #             proc = subprocess.run(
 #                 cmd_parts,
-                capture_output=True,
-                text=True,
-                timeout=10,
+                capture_output=True, text=True, timeout=3,
 #             )
 # -----------------------------------------------------------------------------
 # VERIFICATION:
#   - Each fallback step has timeout ≤ 3s
#   - Total chain ≤ 9s (3 steps × 3s)
# -----------------------------------------------------------------------------
# =============================================================================
# PHASE 1 P0 SAFETY — SUMMARY
# =============================================================================
# Total bugs: 7
# Files affected: 7
#   - celina_pkg/gear0_macro.py     (P0-01 case destruction)
#   - celina_pkg/plugins/self_heal.py  (P0-02 destructive trash, P0-07 timeouts)
#   - celina_pkg/plugins/os_shim.py   (P0-02 rmtree, P0-05 shell=True)
#   - celina_pkg/gear0_storage.py   (P0-03 backend_state, P0-04 checkpoint manifest)
#   - celina_pkg/router.py          (P0-03 initialize_storage call)
#   - celina_pkg/gear0_ledger.py    (P0-04 storage path, P0-06 WAL)
#   - celina_pkg/gear0_macro.py     (P0-04 storage path)
# Dependencies to add: send2trash
# safe_zero_breakage: COMPLIANT — no public functions deleted or renamed
# =============================================================================
