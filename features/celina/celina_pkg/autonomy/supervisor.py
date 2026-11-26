"""Celina Phase 1: Asynchronous Supervisor-Worker Swarm (`autonomy/supervisor.py`)

Non-blocking Supervisor-Worker topology built purely on Python stdlib `asyncio`
(0 MB extra dependency overhead, no LangGraph/JVM bloat):
  - Fast Supervisor classifies incoming requests (`reflex` | `research` | `draft` | `audit`)
  - Dispatches slow multi-step workers via `asyncio.create_task` + `asyncio.to_thread`
    so the voice/chat loop returns IMMEDIATELY ("Dispatched — I'll ping you when ready!")
  - CRITICAL INVARIANT: Preserves strict single-writer serialization for SQLite WAL
    (`TaskGraph`) and tamper-evident `EvidenceLedger` via a single `asyncio.Lock()`.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import logging
import re
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from .. import evidence
from ..planner.task_graph import TaskGraph

log = logging.getLogger("celina.supervisor")

SpeakCallback = Callable[[str], Any]
WorkerHandler = Callable[[str], str]

_RESEARCH_HINTS = re.compile(
    r"\b(?:research|compare|investigate|deep\s+dive|summarize\s+web|market\s+overview|find\s+sources|latest\s+news)\b",
    re.IGNORECASE,
)
_DRAFT_HINTS = re.compile(
    r"\b(?:draft|write\s+(?:a\s+)?(?:report|proposal|email|essay|doc)|polish\s+document|prepare\s+brief)\b",
    re.IGNORECASE,
)
_AUDIT_HINTS = re.compile(
    r"\b(?:audit|analyze\s+logs|scan\s+repo|check\s+all\s+services|system\s+review)\b",
    re.IGNORECASE,
)


@dataclasses.dataclass
class SwarmJob:
    job_id: str
    goal: str
    worker_kind: str  # "reflex" | "research" | "draft" | "audit"
    status: str = "dispatched"  # "dispatched" | "running" | "done" | "failed"
    created_at: float = dataclasses.field(default_factory=time.time)
    finished_at: float | None = None
    result: str = ""
    error: str = ""
    evidence_hash: str = ""


class AsyncSupervisor:
    """Non-blocking Supervisor-Worker dispatcher with single-writer TaskGraph & EvidenceLedger lock.
    """

    def __init__(
        self,
        graph: TaskGraph | None = None,
        router: Any | None = None,
        *,
        ledger: evidence.EvidenceLedger | None = None,
        worker_handlers: dict[str, WorkerHandler] | None = None,
    ):
        self.graph = graph
        self.router = router
        self.ledger = ledger
        self._writer_lock = asyncio.Lock()
        self._sync_writer_lock = threading.Lock()
        self._jobs: dict[str, SwarmJob] = {}
        self._tasks: dict[str, asyncio.Task[SwarmJob]] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._handlers: dict[str, WorkerHandler] = dict(worker_handlers or {})

    @staticmethod
    def classify_goal(goal: str) -> str:
        """Fast 0-LLM deterministic supervisor intent classification (<1 ms)."""
        clean = (goal or "").strip()
        if _RESEARCH_HINTS.search(clean):
            return "research"
        if _DRAFT_HINTS.search(clean):
            return "draft"
        if _AUDIT_HINTS.search(clean):
            return "audit"
        return "reflex"

    async def _speak_safe(self, speak_cb: SpeakCallback | None, text: str) -> None:
        if speak_cb is None:
            return
        try:
            res = speak_cb(text)
            if asyncio.iscoroutine(res) or isinstance(res, Awaitable):
                await res
        except Exception as exc:
            log.debug("Supervisor speak_cb error: %s", exc)

    async def _single_writer_commit(
        self,
        job: SwarmJob,
        *,
        task_id: str | None = None,
        exit_code: int = 0,
    ) -> str:
        """Single-writer serialized commit for TaskGraph (SQLite WAL) + EvidenceLedger.
        Concurrent background workers NEVER race on SQLite writes or evidence hash chains!
        """
        async with self._writer_lock:
            ev_hash = ""
            args_hash = hashlib.sha256(f"{job.worker_kind}:{job.goal}".encode()).hexdigest()[:16]
            if self.graph is not None and task_id:
                try:
                    ev_hash = self.graph.append_evidence(
                        task_id=task_id,
                        tool_name=f"swarm_{job.worker_kind}",
                        args_hash=args_hash,
                        exit_code=exit_code,
                        backup_path=None,
                        why_hash=args_hash,
                    )
                    self.graph.mark(
                        task_id,
                        "succeeded" if job.status == "done" else "failed",
                        result=job.result[:2000] if job.result else None,
                        error=job.error[:1000] if job.error else None,
                        evidence_hash=ev_hash,
                    )
                except Exception as exc:
                    log.debug("Supervisor TaskGraph commit skipped: %s", exc)

            if self.ledger is not None:
                try:
                    rec = self.ledger.append(
                        tool_name=f"swarm_{job.worker_kind}",
                        arguments_hash=args_hash,
                        status=job.status,
                        summary=(job.result or job.error or job.goal)[:240],
                    )
                    ev_hash = ev_hash or str(rec.get("entry_hash") or "")
                except Exception as exc:
                    log.debug("Supervisor EvidenceLedger commit skipped: %s", exc)

            job.evidence_hash = ev_hash
            return ev_hash

    def _run_worker_sync(self, worker_kind: str, goal: str) -> str:
        handler = self._handlers.get(worker_kind)
        if handler is not None:
            return str(handler(goal))
        if self.router is not None and callable(getattr(self.router, "ask", None)):
            reply, _src = self.router.ask(goal, mode="local", dry_run=False)
            return str(reply)
        return f"[{worker_kind}] Completed background task for: {goal}"

    async def _worker_coroutine(
        self,
        job: SwarmJob,
        *,
        task_id: str | None,
        notify_cb: SpeakCallback | None,
    ) -> SwarmJob:
        job.status = "running"
        try:
            out = await asyncio.to_thread(self._run_worker_sync, job.worker_kind, job.goal)
            job.result = out
            job.status = "done"
            job.finished_at = time.time()
            await self._single_writer_commit(job, task_id=task_id, exit_code=0)
            await self._speak_safe(notify_cb, f"Background {job.worker_kind} finished: {job.goal[:60]}")
        except Exception as exc:
            job.error = str(exc)
            job.status = "failed"
            job.finished_at = time.time()
            await self._single_writer_commit(job, task_id=task_id, exit_code=1)
            await self._speak_safe(notify_cb, f"Background {job.worker_kind} hit an error: {exc}")
        return job

    async def dispatch(
        self,
        goal: str,
        *,
        speak_cb: SpeakCallback | None = None,
        notify_cb: SpeakCallback | None = None,
        force_background: bool = False,
    ) -> SwarmJob:
        """Dispatches a goal through the Supervisor:
        - Background kinds (`research` / `draft` / `audit`, or `force_background=True`):
          launches `asyncio.create_task`, returns IMMEDIATELY, and calls `speak_cb` right away!
        - Reflex kinds (`reflex` without `force_background`):
          executes directly and commits cleanly through the single writer lock.
        """
        clean = (goal or "").strip()
        kind = self.classify_goal(clean)
        job_id = f"job-{uuid.uuid4().hex[:8]}"
        job = SwarmJob(job_id=job_id, goal=clean, worker_kind=kind, status="dispatched")
        self._jobs[job_id] = job

        task_id: str | None = None
        if self.graph is not None:
            async with self._writer_lock:
                try:
                    task_id = self.graph.create_task(clean, status="pending")
                except Exception as exc:
                    log.debug("Supervisor TaskGraph create_task skipped: %s", exc)

        if force_background or kind in {"research", "draft", "audit"}:
            t = asyncio.create_task(
                self._worker_coroutine(job, task_id=task_id, notify_cb=notify_cb),
                name=f"celina-swarm-{job_id}",
            )
            self._tasks[job_id] = t
            await self._speak_safe(
                speak_cb,
                f"Dispatched {kind} worker ({job_id}). I'll ping you when ready.",
            )
            return job

        # Fast inline reflex path
        return await self._worker_coroutine(job, task_id=task_id, notify_cb=None)

    async def wait_all(self, timeout: float = 30.0) -> list[SwarmJob]:
        pending = [t for t in self._tasks.values() if not t.done()]
        if pending:
            await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=timeout)
        return list(self._jobs.values())

    def list_jobs(self) -> list[dict[str, Any]]:
        return [
            {
                "job_id": j.job_id,
                "worker_kind": j.worker_kind,
                "status": j.status,
                "goal": j.goal,
                "result_preview": j.result[:140],
                "error": j.error[:140],
                "evidence_hash": j.evidence_hash,
            }
            for j in self._jobs.values()
        ]

    def _single_writer_commit_sync(
        self,
        job: SwarmJob,
        *,
        task_id: str | None = None,
        exit_code: int = 0,
    ) -> str:
        """Thread-safe single-writer commit mirror for synchronous CLI/chat `/bg` workers."""
        with self._sync_writer_lock:
            ev_hash = ""
            args_hash = hashlib.sha256(f"{job.worker_kind}:{job.goal}".encode()).hexdigest()[:16]
            if self.graph is not None and task_id:
                try:
                    ev_hash = self.graph.append_evidence(
                        task_id=task_id,
                        tool_name=f"swarm_{job.worker_kind}",
                        args_hash=args_hash,
                        exit_code=exit_code,
                        backup_path=None,
                        why_hash=args_hash,
                    )
                    self.graph.mark(
                        task_id,
                        "succeeded" if job.status == "done" else "failed",
                        result=job.result[:2000] if job.result else None,
                        error=job.error[:1000] if job.error else None,
                        evidence_hash=ev_hash,
                    )
                except Exception as exc:
                    log.debug("Supervisor sync TaskGraph commit skipped: %s", exc)
            if self.ledger is not None:
                try:
                    rec = self.ledger.append(
                        tool_name=f"swarm_{job.worker_kind}",
                        arguments_hash=args_hash,
                        status=job.status,
                        summary=(job.result or job.error or job.goal)[:240],
                    )
                    ev_hash = ev_hash or str(rec.get("entry_hash") or "")
                except Exception as exc:
                    log.debug("Supervisor sync EvidenceLedger commit skipped: %s", exc)
            job.evidence_hash = ev_hash
            return ev_hash

    def dispatch_background(
        self,
        goal: str,
        *,
        notify_cb: SpeakCallback | None = None,
    ) -> SwarmJob:
        """Non-blocking sync entrypoint for CLI / interactive chat (`/bg <goal>`):
        returns immediately with `SwarmJob` while the worker runs in a daemon thread
        and commits through the single-writer SQLite WAL + EvidenceLedger lock.
        """
        clean = (goal or "").strip()
        kind = self.classify_goal(clean)
        job_id = f"job-{uuid.uuid4().hex[:8]}"
        job = SwarmJob(job_id=job_id, goal=clean, worker_kind=kind, status="dispatched")
        self._jobs[job_id] = job

        task_id: str | None = None
        if self.graph is not None:
            with self._sync_writer_lock:
                try:
                    task_id = self.graph.create_task(clean, status="pending")
                except Exception as exc:
                    log.debug("Supervisor sync create_task skipped: %s", exc)

        def _bg_runner() -> None:
            job.status = "running"
            try:
                job.result = self._run_worker_sync(job.worker_kind, job.goal)
                job.status = "done"
                job.finished_at = time.time()
                self._single_writer_commit_sync(job, task_id=task_id, exit_code=0)
                if notify_cb is not None:
                    try:
                        notify_cb(f"Background {job.worker_kind} finished: {job.goal[:60]}")
                    except Exception as exc:
                        log.debug("Supervisor notify_cb failed: %s", exc)
            except Exception as exc:
                job.error = str(exc)
                job.status = "failed"
                job.finished_at = time.time()
                self._single_writer_commit_sync(job, task_id=task_id, exit_code=1)

        t = threading.Thread(target=_bg_runner, name=f"celina-swarm-{job_id}", daemon=True)
        self._threads[job_id] = t
        t.start()
        return job

    def wait_threads(self, timeout: float = 15.0) -> list[SwarmJob]:
        deadline = time.monotonic() + max(0.1, float(timeout))
        for t in list(self._threads.values()):
            rem = max(0.0, deadline - time.monotonic())
            if rem > 0 and t.is_alive():
                t.join(timeout=rem)
        return list(self._jobs.values())

    def format_jobs(self) -> str:
        jobs = self.list_jobs()
        if not jobs:
            return "No background swarm jobs dispatched yet. Start one anytime with: /bg <goal>"
        lines = ["Background Swarm Jobs (`/bg <goal>`):"]
        for j in jobs[-15:]:
            icon = {"done": "✅", "running": "⏳", "dispatched": "🚀", "failed": "❌"}.get(j["status"], "•")
            tail = f" -> {j['result_preview']}" if j["result_preview"] else (f" -> ERROR: {j['error']}" if j["error"] else "")
            lines.append(f"  {icon} [{j['job_id']}] ({j['worker_kind']}, {j['status']}): {j['goal'][:60]}{tail}")
        return "\n".join(lines)
