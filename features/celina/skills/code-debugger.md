---
name: code-debugger
description: Systematic root-cause debugging and surgical fix workflow for Python, Bash, JS/TS, and Linux errors.
triggers: [debug, traceback, stack trace, exception, bug, failing test, crash, syntax error]
---
# Code Debugger Playbook (Open-Source Agent Skill)
1. **Reproduce & Locate First:** Read the exact failing file/lines via `read_file` or `search_in_files` before hypothesizing.
2. **Root Cause Over Symptoms:** Identify the *single root cause* (wrong state, null/type mismatch, missing env, race condition) rather than slapping try/except bandages.
3. **Minimal Surgical Patch:** Use `edit_file` (auto-backed up) to apply the smallest clean fix.
4. **Verify:** Propose running isolated Python verification via `run_python_sandbox` (Bubblewrap no-net sandbox, approval-gated) or the narrowest reproduction/test command via `run_shell_command` (approval-gated) to confirm the fix works.
5. **Verdict-First Output:** Lead with Root Cause (1 line) → Fix Applied (1–2 lines) → Verification Result.
