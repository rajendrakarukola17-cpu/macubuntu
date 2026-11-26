"""
Celina v5 Guarded Overnight Self-Improvement (`learning/self_improve.py`)

Implements the PCEC loop (Perceive -> Construct -> Evaluate -> Commit-as-Pending):
  1. Perceive: collects recent failed tasks (<24h) from `tasks.db`.
  2. Construct: generates a single bounded proposal (`new_skill` | `patch_tool` | `new_rule`).
  3. Guardrail: rejects any proposal attempting to bypass Tier-2 approval,
     touch credential paths (`.ssh`, `.gnupg`, `.aws`, `env`), or disable sandboxing.
  4. Evaluate: executes proposed skill code strictly inside Celina's Bubblewrap
     Python sandbox (`run_python_sandbox` / injectable runner) with hard timeout.
  5. Commit-as-Pending: stores the proposal with `status='pending'` for explicit
     human review — NEVER auto-activates code without user approval.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from .. import config
from ..planner.task_graph import TaskGraph

log = logging.getLogger("celina.learning")

VALID_PROPOSAL_TYPES: frozenset[str] = frozenset({"new_skill", "patch_tool", "new_rule"})
_FORBIDDEN_PROPOSAL_PATTERNS = (
    re.compile(r"tier1_auto_tools", re.IGNORECASE),
    re.compile(r"approval_cb\s*=\s*None", re.IGNORECASE),
    re.compile(r"(\.ssh|\.gnupg|\.aws|\.kube|OPENROUTER_API_KEY)", re.IGNORECASE),
    re.compile(r"shell\s*=\s*True", re.IGNORECASE),
)


class _DefaultBwrapSandboxRunner:
    """Thin adapter delegating to Celina's Bubblewrap Python sandbox in `tools.py`."""

    def run_python(self, code: str, timeout: int = 30) -> bool:
        from .. import tools as celina_tools

        out = celina_tools.run_python_sandbox(code, timeout_sec=min(int(timeout), 30))
        low = str(out).lower()
        return ("error:" not in low) and ("traceback" not in low) and ("denied" not in low)


class SelfImprover:
    def __init__(self, graph: TaskGraph, llm: Any, sandbox_runner: Optional[Any] = None):
        self.graph = graph
        self.llm = llm
        self.sandbox = sandbox_runner if sandbox_runner is not None else _DefaultBwrapSandboxRunner()

    def run_nightly(self) -> Optional[dict[str, Any]]:
        failures = self._recent_failures()
        if not failures:
            return None
        proposal = self._propose(failures)
        if proposal is None:
            return None

        safe, reason = self._validate_safety(proposal)
        if not safe:
            pid = self._store_proposal(
                proposal,
                source_task_ids=[f["id"] for f in failures],
                test_result=f"rejected_safety:{reason}",
                status="rejected",
            )
            return {"id": pid, "status": "rejected", "test_result": f"rejected_safety:{reason}"}

        test_result = self._test_in_sandbox(proposal)
        pid = self._store_proposal(
            proposal,
            source_task_ids=[f["id"] for f in failures],
            test_result=test_result,
            status="pending",
        )
        return {"id": pid, "status": "pending", "test_result": test_result, "type": proposal["type"]}

    def _recent_failures(self) -> list[dict[str, Any]]:
        with self.graph._lock:
            rows = self.graph._conn.execute(
                """
                SELECT id, goal, last_error FROM tasks
                WHERE status = 'failed' AND updated_at > datetime('now', '-1 day')
                ORDER BY updated_at DESC
                LIMIT 20
                """
            ).fetchall()
        return [{"id": str(r["id"]), "goal": str(r["goal"]), "error": str(r["last_error"] or "")} for r in rows]

    def _propose(self, failures: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if self.llm is None:
            return None
        prompt = (
            "These Celina tasks failed in the last 24h:\n"
            f"{json.dumps(failures, indent=2)}\n\n"
            "Propose ONE of:\n"
            "1. A new skill (a reusable script stored in skills/proposed/) that automates the failed workflow.\n"
            "2. A patch to an existing tool's argument-handling logic.\n"
            "3. A new proactive rule for proactive/rules.json.\n\n"
            'Return ONLY valid JSON: {"type":"new_skill"|"patch_tool"|"new_rule","payload":{...}}'
        )
        raw = self.llm.complete(
            system="You are Celina's guarded self-improvement planner. Never weaken security or Tier-2 approval.",
            user=prompt,
            temperature=0.1,
        )
        text = (raw or "").strip()
        if text.startswith("```"):
            text = "\n".join(ln for ln in text.splitlines()[1:] if not ln.strip().startswith("```")).strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        ptype = str(parsed.get("type") or "").strip()
        payload = parsed.get("payload")
        if ptype not in VALID_PROPOSAL_TYPES or not isinstance(payload, dict):
            return None
        return {"type": ptype, "payload": payload}

    def _validate_safety(self, proposal: dict[str, Any]) -> tuple[bool, str]:
        serialized = json.dumps(proposal, sort_keys=True)
        for pat in _FORBIDDEN_PROPOSAL_PATTERNS:
            if pat.search(serialized):
                return False, f"forbidden_pattern:{pat.pattern}"
        # Prevent path traversal on proposed skill file names
        name = str((proposal.get("payload") or {}).get("name") or "")
        if ".." in name or "/" in name or "\\" in name:
            return False, "invalid_skill_name_path_traversal"
        return True, "ok"

    def _test_in_sandbox(self, proposal: dict[str, Any]) -> str:
        if proposal["type"] != "new_skill":
            return "not_run"
        code = str(proposal["payload"].get("code") or "")
        if not code.strip():
            return "failed"
        try:
            ok = bool(self.sandbox.run_python(code, timeout=30))
            return "passed" if ok else "failed"
        except Exception:
            return "failed"

    def _store_proposal(
        self,
        proposal: dict[str, Any],
        *,
        source_task_ids: list[str],
        test_result: str,
        status: str = "pending",
    ) -> str:
        pid = str(uuid.uuid4())
        with self.graph._lock:
            self.graph._conn.execute(
                """
                INSERT INTO proposals (id, source_task_ids, proposal_type, payload, test_result, status)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    pid,
                    json.dumps(source_task_ids),
                    proposal["type"],
                    json.dumps(proposal["payload"], sort_keys=True),
                    test_result,
                    status,
                ),
            )
            self.graph._conn.commit()
        log.info("Proposal %s staged (status=%s, test=%s)", pid, status, test_result)
        return pid

    def list_proposals(self, *, status: Optional[str] = None) -> list[dict[str, Any]]:
        with self.graph._lock:
            if status:
                rows = self.graph._conn.execute(
                    "SELECT * FROM proposals WHERE status = ? ORDER BY created_at DESC", (status,)
                ).fetchall()
            else:
                rows = self.graph._conn.execute("SELECT * FROM proposals ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    def set_proposal_status(self, proposal_id: str, new_status: str) -> bool:
        if new_status not in {"approved", "rejected", "pending"}:
            raise ValueError(f"Unsupported proposal status: {new_status}")
        with self.graph._lock:
            cur = self.graph._conn.execute(
                "UPDATE proposals SET status = ? WHERE id = ?", (new_status, proposal_id)
            )
            self.graph._conn.commit()
            return int(cur.rowcount or 0) > 0

    # ------------------------------------------------------------------
    # MARS Single-Cycle Metacognitive Reflection (Principle + Procedural)
    # + Offline Knowledge Compilation ("The Brain That Goes Quiet")
    # ------------------------------------------------------------------
    def run_mars_reflection(
        self,
        failures: Optional[list[dict[str, Any]]] = None,
        *,
        memory_engine: Optional[Any] = None,
    ) -> dict[str, Any]:
        """
        MARS single-recurrence metacognitive reflection:
          - Extracts BOTH:
              1) `principles` (Principle-Based Reflection: abstract guardrails/invariants)
              2) `procedures` (Procedural Reflection: step-by-step recovery playbooks)
            in ONE pass without multi-turn recursive self-improvement loops.
          - Persists distilled rules into `CelinaMemory` (when supplied) so future
            `graph_walk()` and `recall()` calls surface them immediately.
        """
        items = list(failures) if failures is not None else self._recent_failures()
        if not items:
            return {"cycle": "mars_single_pass", "principles": [], "procedures": [], "stored_in_memory": 0}

        principles: list[dict[str, str]] = []
        procedures: list[dict[str, Any]] = []

        # Single-pass LLM reflection if an LLM is wired, with deterministic fallback
        parsed: Optional[dict[str, Any]] = None
        if self.llm is not None and hasattr(self.llm, "complete"):
            try:
                raw = self.llm.complete(
                    system=(
                        "Perform ONE-CYCLE MARS metacognitive reflection. Return strict JSON with keys "
                        "'principles' ([{rule, avoids_error}]) and 'procedures' ([{goal_pattern, steps}])."
                    ),
                    user=json.dumps(items[:10]),
                    temperature=0.2,
                )
                cand = json.loads(raw)
                if isinstance(cand, dict) and ("principles" in cand or "procedures" in cand):
                    parsed = cand
            except Exception:
                parsed = None

        if parsed is not None:
            principles = list(parsed.get("principles") or [])
            procedures = list(parsed.get("procedures") or [])
        else:
            seen_err_classes: set[str] = set()
            for f in items:
                goal_txt = str(f.get("goal") or "").strip()
                err_txt = str(f.get("last_error") or "unknown_error").strip()
                err_key = err_txt.split(":")[0].strip().lower()[:48]
                if err_key not in seen_err_classes:
                    seen_err_classes.add(err_key)
                    principles.append(
                        {
                            "rule": f"Pre-verify preconditions before repeating actions prone to '{err_key}'",
                            "avoids_error": err_txt[:120],
                        }
                    )
                procedures.append(
                    {
                        "goal_pattern": goal_txt[:120],
                        "steps": [
                            "Inspect target state / graph_walk prior failed attempts first",
                            f"Avoid failing path ({err_key}); switch via CapabilityRouteTable fallback",
                            "Verify exit status & record evidence before marking done",
                        ],
                    }
                )

        stored = 0
        if memory_engine is not None and hasattr(memory_engine, "remember"):
            for idx, p in enumerate(principles):
                rule_txt = str(p.get("rule") or "")
                err_txt = str(p.get("avoids_error") or "error")
                if rule_txt:
                    memory_engine.remember(
                        f"MARS Principle: {rule_txt} (avoids: {err_txt})",
                        importance=0.9,
                        meta={"source": "mars_principle"},
                        entities=[f"mars_principle_{idx}", err_txt[:40]],
                    )
                    if hasattr(memory_engine, "associate"):
                        memory_engine.associate(err_txt[:40], f"mars_principle_{idx}", "mars_principle")
                    stored += 1
            for idx, proc in enumerate(procedures):
                gp = str(proc.get("goal_pattern") or "task")
                steps_txt = " -> ".join(str(s) for s in (proc.get("steps") or []))
                if steps_txt:
                    memory_engine.remember(
                        f"MARS Procedure for '{gp}': {steps_txt}",
                        importance=0.85,
                        meta={"source": "mars_procedure"},
                        entities=[gp[:40], f"mars_proc_{idx}"],
                    )
                    if hasattr(memory_engine, "associate"):
                        memory_engine.associate(gp[:40], f"mars_proc_{idx}", "mars_procedure")
                    stored += 1

        return {
            "cycle": "mars_single_pass",
            "principles": principles,
            "procedures": procedures,
            "stored_in_memory": stored,
        }

    def compile_recurring_queries(
        self,
        memory_engine: Any,
        queries: list[str],
        *,
        big_model_fn: Callable[[str], str],
        source_model: str = "big-offline",
        confidence: float = 0.95,
    ) -> list[str]:
        """
        Offline Knowledge Compilation ("The Brain That Goes Quiet"):
        Invokes the big model during idle/nightly maintenance to pre-compute
        verified answers for recurring queries and stores them in CelinaMemory's
        BM25 compiled store so runtime turns stay fast on the small model.
        """
        compiled_ids: list[str] = []
        for q in queries or []:
            q_clean = str(q or "").strip()
            if not q_clean:
                continue
            ans = str(big_model_fn(q_clean) or "").strip()
            if not ans:
                continue
            cid = memory_engine.compile_knowledge(
                q_clean,
                ans,
                source_model=source_model,
                confidence=confidence,
            )
            compiled_ids.append(cid)
        return compiled_ids


def main() -> int:
    parser = argparse.ArgumentParser(description="Celina v5 Guarded Overnight Self-Improvement Runner")
    parser.add_argument("--list", action="store_true", help="List staged proposals")
    parser.add_argument("--dry-run", action="store_true", help="Report recent failures without calling LLM")
    args = parser.parse_args()

    graph = TaskGraph(config.BASE_DIR / "tasks.db")
    improver = SelfImprover(graph, llm=None)
    try:
        if args.list:
            print(json.dumps(improver.list_proposals(), indent=2))
            return 0
        if args.dry_run:
            print(json.dumps({"recent_failures": improver._recent_failures()}, indent=2))
            return 0
        print(json.dumps({"status": "idle", "pending_proposals": len(improver.list_proposals(status="pending"))}))
    finally:
        graph.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
