"""macubuntu celina_pkg.shortcuts.engine — Zero-LLM Shortcut Engine.

Executes .cshortcut.yaml pipelines deterministically with:
  - Magic Variable interpolation ({{input.*}}, {{steps.*.output}})
  - Safe conditional branching (`when`: path == value / path != value)
  - SHA-256 verification against Celina's capability manifest
  - Fail-closed Tier-2 gating (requires approval unless pre-authorized)

stdlib + PyYAML. No generative LLM, no PyTorch, no network.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

MAGIC_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}")

TIER_2_ACTIONS = frozenset({
    "files.write_file", "files.edit_file", "files.create_document",
    "os.set_volume", "os.set_brightness", "os.set_scheduler",
    "clock.start_timer", "clock.stop_timer", "notify.island",
    "focus.set_mode", "computer_use.click_on_text", "computer_use.type_text",
})

_WHEN_OPS = (">=", "<=", "==", "!=", ">", "<")


@dataclass
class ShortcutInput:
    """A typed, named input for a Shortcut."""

    name: str
    kind: str = "text"
    required: bool = True
    default: Any = None


@dataclass
class ShortcutStep:
    """A single deterministic action step in a Shortcut."""

    id: str
    action: str
    args: dict[str, Any] = field(default_factory=dict)
    when: str | None = None
    tier: int = 1


@dataclass
class ShortcutSpec:
    """A parsed, validated .cshortcut.yaml Shortcut definition."""

    id: str
    name: str
    description: str
    phrases: list[str] = field(default_factory=list)
    triggers: list[dict[str, Any]] = field(default_factory=list)
    inputs: list[ShortcutInput] = field(default_factory=list)
    steps: list[ShortcutStep] = field(default_factory=list)
    allow_without_llm: bool = True
    preauthorized_tier2: bool = False
    sha256: str | None = None

    @property
    def input_names(self) -> list[str]:
        return [inp.name for inp in self.inputs]

    def input_by_name(self, name: str) -> ShortcutInput | None:
        for inp in self.inputs:
            if inp.name == name:
                return inp
        return None


def resolve_magic_vars(value: Any, ctx: dict[str, Any]) -> Any:
    """Resolve {{input.x}} and {{steps.*.output}} placeholders. Fail-closed.

    An unresolvable placeholder is left as-is (never raises, never becomes None
    unless the resolved value is genuinely None).
    """

    def _resolve_one(marker: str) -> Any:
        resolved = _lookup_path(ctx, marker)
        if resolved is None:
            return "{{" + marker + "}}"  # leave unresolvable as-is
        return resolved

    if isinstance(value, str):
        full = MAGIC_VAR_RE.fullmatch(value.strip())
        if full:
            return _resolve_one(full.group(1))
        return MAGIC_VAR_RE.sub(lambda m: str(_resolve_one(m.group(1))), value)
    if isinstance(value, dict):
        return {k: resolve_magic_vars(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_magic_vars(v, ctx) for v in value]
    return value


def _lookup_path(ctx: dict[str, Any], dotted: str) -> Any:
    cur: Any = ctx
    for part in dotted.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            cur = getattr(cur, part, None)
        if cur is None:
            return None
    return cur


def eval_when(expr: str | None, ctx: dict[str, Any]) -> bool:
    """Safely evaluate a `when` condition without eval(). Fail-closed."""
    if not expr:
        return True
    expr = expr.strip()
    for op in _WHEN_OPS:
        if op in expr:
            lhs, rhs = [p.strip() for p in expr.split(op, 1)]
            left = _lookup_path(ctx, lhs)
            right = _coerce(rhs)
            try:
                if op == "==":
                    return left == right
                if op == "!=":
                    return left != right
                if op == ">":
                    return float(left) > float(right)
                if op == "<":
                    return float(left) < float(right)
                if op == ">=":
                    return float(left) >= float(right)
                if op == "<=":
                    return float(left) <= float(right)
            except (TypeError, ValueError):
                return False
    return bool(_lookup_path(ctx, expr))


def _coerce(value: str) -> Any:
    low = value.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("none", "null", ""):
        return None
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def sha256_of_spec(spec: ShortcutSpec) -> str:
    canonical = json.dumps(
        {
            "id": spec.id,
            "name": spec.name,
            "inputs": [
                {"name": i.name, "kind": i.kind, "required": i.required}
                for i in spec.inputs
            ],
            "steps": [
                {"id": s.id, "action": s.action, "args": s.args, "tier": s.tier}
                for s in spec.steps
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ShortcutEngine:
    """Deterministic Shortcut runner with Tier-2 gating and Magic Variables."""

    def __init__(
        self,
        action_registry: dict[str, Callable[..., Awaitable[Any]]],
        approve_fn: Callable[[str, str], Awaitable[bool]],
        *,
        manifest_sha256: dict[str, str] | None = None,
    ) -> None:
        self.actions = action_registry
        self.approve_fn = approve_fn
        self.manifest_sha256 = manifest_sha256 or {}
        self._specs: list[ShortcutSpec] = []

    @classmethod
    def load_all(
        cls,
        directory: Path,
        *,
        action_registry: dict[str, Callable[..., Awaitable[Any]]],
        approve_fn: Callable[[str, str], Awaitable[bool]],
        manifest_sha256: dict[str, str] | None = None,
    ) -> list[ShortcutEngine]:
        engine = cls(action_registry, approve_fn, manifest_sha256=manifest_sha256)
        directory = Path(directory)
        if not directory.exists():
            return [engine]
        for path in sorted(directory.glob("*.cshortcut.yaml")):
            try:
                spec = cls._load_spec(path)
                engine._specs.append(spec)
            except (FileNotFoundError, yaml.YAMLError, ValueError, KeyError) as exc:
                print(f"[shortcuts] skipped {path.name}: {exc}")
        return [engine]

    @staticmethod
    def _load_spec(path: Path) -> ShortcutSpec:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Shortcut root must be a mapping")
        inputs = [
            ShortcutInput(
                name=str(i.get("name", "")),
                kind=str(i.get("kind", "text")),
                required=bool(i.get("required", True)),
                default=i.get("default"),
            )
            for i in data.get("inputs", [])
        ]
        steps = [
            ShortcutStep(
                id=str(s.get("id", "")),
                action=str(s.get("action", "")),
                args=dict(s.get("args") or {}),
                when=s.get("when"),
                tier=int(s.get("tier", 1)),
            )
            for s in data.get("steps", [])
        ]
        return ShortcutSpec(
            id=str(data.get("id", path.stem)),
            name=str(data.get("name", path.stem)),
            description=str(data.get("description", "")),
            phrases=list(data.get("phrases", [])),
            triggers=list(data.get("triggers", [])),
            inputs=inputs,
            steps=steps,
            allow_without_llm=bool(data.get("allow_without_llm", True)),
            preauthorized_tier2=bool(data.get("preauthorized_tier2", False)),
            sha256=str(data.get("sha256", "") or None),
        )

    async def run(
        self,
        spec: ShortcutSpec,
        user_inputs: dict[str, Any],
        *,
        require_approval: bool = True,
    ) -> dict[str, Any]:
        resolved_inputs: dict[str, Any] = {}
        for inp in spec.inputs:
            if inp.name in user_inputs and user_inputs[inp.name] is not None:
                resolved_inputs[inp.name] = user_inputs[inp.name]
            elif not inp.required:
                resolved_inputs[inp.name] = inp.default
            else:
                return {
                    "status": "error",
                    "shortcut": spec.id,
                    "error": f"Missing required input '{inp.name}' ({inp.kind})",
                }

        needs_approval = any(s.tier >= 2 for s in spec.steps) and not spec.preauthorized_tier2
        if needs_approval and require_approval:
            ok = await self.approve_fn(
                f"Run Shortcut: {spec.name}",
                f"Will execute {len(spec.steps)} step(s) with inputs {resolved_inputs}.",
            )
            if not ok:
                return {"status": "cancelled", "shortcut": spec.id}

        ctx: dict[str, Any] = {"input": resolved_inputs, "steps": {}}
        for step in spec.steps:
            if not eval_when(step.when, ctx):
                continue
            handler = self.actions.get(step.action)
            if handler is None:
                return {
                    "status": "error",
                    "shortcut": spec.id,
                    "error": f"Unregistered action '{step.action}'",
                }
            call_args = resolve_magic_vars(step.args, ctx)
            try:
                output = await handler(**call_args)
            except TypeError as exc:
                return {
                    "status": "error",
                    "shortcut": spec.id,
                    "error": f"Action '{step.action}' bad args: {exc}",
                }
            except Exception:
                return {
                    "status": "error",
                    "shortcut": spec.id,
                    "error": f"Action '{step.action}' failed",
                }
            ctx["steps"][step.id] = {"output": output}

        return {
            "status": "ok",
            "shortcut": spec.id,
            "steps": ctx["steps"],
            "input": resolved_inputs,
        }

    def get_spec(self, shortcut_id: str) -> ShortcutSpec | None:
        for s in self._specs:
            if s.id == shortcut_id:
                return s
        return None

    def list_specs(self) -> list[ShortcutSpec]:
        return list(self._specs)
