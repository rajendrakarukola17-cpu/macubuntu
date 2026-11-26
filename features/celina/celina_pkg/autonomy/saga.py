"""Celina v5 — Capability #17: Capability-Based Routing with Saga Coordination

Replaces blind same-tool retry with distributed systems resilience:
  1. `CapabilityRouteTable`: maps abstract capabilities (e.g. `edit_office_document`,
     `search_workspace`, `fetch_web`) to ordered primary + alternative tools,
     automatically skipping session-disabled / circuit-broken tools.
  2. `SagaCoordinator`: records every completed forward step alongside its
     compensating rollback action (e.g., `PreExecutionCheckpoint.rollback` /
     `UndoJournal.undo`), and on terminal failure compensates all completed steps
     in reverse LIFO order while validating state transitions via `GovernedStateMachine`.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

from .. import governance, telemetry

CompensationFn = Callable[[], Any]


class CapabilityRouteTable:
    """Maps high-level capability intents to ordered fallback tool chains."""

    DEFAULT_ROUTES: dict[str, tuple[str, ...]] = {
        "edit_office_document": ("libreoffice_edit", "edit_document", "autonomous_gui_task"),
        "read_office_document": ("read_document", "read_file"),
        "search_workspace": ("spotlight_query", "search_files", "search_in_files"),
        "fetch_web": ("fetch_url_text", "fetch_webpage", "web_search"),
        "edit_text_file": ("edit_file", "write_file"),
        "gui_inspect": ("vision_analyze", "vision_ocr", "inspect_ui_elements", "read_screen", "find_text_on_screen"),
        "gui_actuate": ("computer_click", "ghostdesk_click", "click_on_text", "click_position"),
        "gui_type": ("computer_type", "type_text"),
        "mcp_isolated_desktop": ("ghostdesk", "kwin-mcp", "deskwright"),
        "mcp_accessibility_actuation": ("computer-use-linux", "vadgr-computer-use", "uacc"),
        "mcp_memory_graph": ("swadb-memory",),
    }

    def __init__(self, extra_routes: dict[str, tuple[str, ...]] | None = None):
        self._routes: dict[str, tuple[str, ...]] = dict(self.DEFAULT_ROUTES)
        if extra_routes:
            self._routes.update(extra_routes)
        self._tool_to_capability: dict[str, str] = {}
        for cap_name, chain in self._routes.items():
            for tool_name in chain:
                self._tool_to_capability.setdefault(tool_name, cap_name)

    def capability_for_tool(self, tool_name: str) -> str | None:
        return self._tool_to_capability.get(tool_name)

    def find_alternative(
        self,
        failed_tool: str,
        *,
        attempted: set[str] | None = None,
        is_tool_healthy: Callable[[str], bool] | None = None,
    ) -> str | None:
        """Returns the next healthy alternative tool providing the same capability, or None."""
        cap = self.capability_for_tool(failed_tool)
        if not cap:
            return None
        tried = set(attempted or set()) | {failed_tool}
        for candidate in self._routes.get(cap, ()):
            if candidate in tried:
                continue
            if is_tool_healthy is not None and not is_tool_healthy(candidate):
                continue
            return candidate
        return None


@dataclasses.dataclass
class SagaStepRecord:
    step_id: str
    tool_name: str
    compensate: CompensationFn
    description: str = ""
    compensated: bool = False
    compensation_result: str = ""


class SagaCoordinator:
    """Coordinates multi-step autonomous workflows with LIFO compensation rollback
    and hash-chained governed state transitions.
    """

    def __init__(
        self,
        saga_id: str,
        *,
        route_table: CapabilityRouteTable | None = None,
        tracer: telemetry.CelinaTracer | None = None,
    ):
        self.saga_id = saga_id
        self.route_table = route_table or CapabilityRouteTable()
        self.tracer = tracer or telemetry.get_tracer()
        self.state_machine = governance.GovernedStateMachine(saga_id, initial_state="pending")
        self.state_machine.transition("running", reason="saga_started")
        self._completed_steps: list[SagaStepRecord] = []

    def register_completed_step(
        self,
        step_id: str,
        tool_name: str,
        compensate: CompensationFn,
        *,
        description: str = "",
    ) -> None:
        self._completed_steps.append(
            SagaStepRecord(
                step_id=step_id,
                tool_name=tool_name,
                compensate=compensate,
                description=description,
            ),
        )

    def select_fallback_capability(
        self,
        failed_tool: str,
        *,
        attempted: set[str] | None = None,
        is_tool_healthy: Callable[[str], bool] | None = None,
    ) -> str | None:
        return self.route_table.find_alternative(
            failed_tool,
            attempted=attempted,
            is_tool_healthy=is_tool_healthy,
        )

    def compensate_all(self, *, reason: str = "step_failed") -> dict[str, Any]:
        """Executes compensating rollbacks for all completed steps in reverse LIFO order."""
        if self.state_machine.state in {"running", "failed", "done"}:
            self.state_machine.transition("compensating", reason=reason)

        results: list[dict[str, Any]] = []
        all_ok = True
        with self.tracer.start_span(
            f"saga.compensate.{self.saga_id}",
            kind="saga",
            inputs={"reason": reason, "steps_to_compensate": len(self._completed_steps)},
        ) as span:
            for step in reversed(self._completed_steps):
                if step.compensated:
                    continue
                try:
                    out = step.compensate()
                    step.compensated = True
                    step.compensation_result = str(out) if out is not None else "ok"
                    results.append(
                        {"step_id": step.step_id, "tool": step.tool_name, "ok": True, "detail": step.compensation_result},
                    )
                except Exception as exc:
                    all_ok = False
                    step.compensation_result = f"error: {exc}"
                    results.append(
                        {"step_id": step.step_id, "tool": step.tool_name, "ok": False, "detail": str(exc)},
                    )
            span.set_io(outputs={"compensated_count": len(results), "all_ok": all_ok})

        self.state_machine.transition("compensated" if all_ok else "failed", reason=f"compensated:{len(results)}")
        return {
            "saga_id": self.saga_id,
            "state": self.state_machine.state,
            "all_ok": all_ok,
            "compensated_steps": results,
        }

    def mark_complete(self) -> None:
        if self.state_machine.state == "running":
            self.state_machine.transition("done", reason="saga_completed")
