"""macubuntu celina_pkg.runtime.tier_policy — Celina tier policy engine.

Defines CelinaTierPolicy, a hardware-adaptive feature/behavior profile that
scales Celina subsystems to the detected ai_tier from
/etc/macubuntu/hardware.json. All knobs default to the most restrictive
(fail-closed) values; higher tiers opt into progressively richer behavior.

Subsystems wire in by accepting an optional `tier_policy` kwarg (safe default
None => fail-closed defaults), keeping every existing call site functional
unchanged.

Fields
------
serialize_heavy_models      : bool   — preload/keep heavy LLM models resident
enable_ladybug_graph        : bool   — LadybugDB vector KG backend
enable_gliner_slots         : bool   — GLiNER named-entity slot extraction
keep_voice_hot              : bool   — keep voice/wakeword model loaded
enable_astra_1fps_buffer    : bool   — 1 fps camera/observation buffer
enable_libkrun_sandbox      : bool   — libkrun VM sandbox for code execution
spotlight_batch_size        : int    — Spotlight index batch size
ram_psi_evict_threshold     : float  — memory PSI avg10 threshold for model eviction

stdlib only: json, dataclasses, pathlib.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class CelinaTierPolicy:
    """Hardware-adaptive Celina behavior policy. Fail-closed by default."""

    serialize_heavy_models: bool = False
    enable_ladybug_graph: bool = False
    enable_gliner_slots: bool = False
    keep_voice_hot: bool = False
    enable_astra_1fps_buffer: bool = False
    enable_libkrun_sandbox: bool = False
    spotlight_batch_size: int = 4
    ram_psi_evict_threshold: float = 15.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CelinaTierPolicy:
        """Build from a dict, ignoring unknown keys (forward-compatible)."""
        known = {f.name for f in cls.__dataclass_fields__.values()}
        kwargs = {k: v for k, v in data.items() if k in known}
        return cls(**kwargs)

    def save(self, path: str | Path) -> None:
        """Atomically write policy JSON (idempotent, DRY_RUN-safe friendly)."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(p)

    @classmethod
    def load(cls, path: str | Path) -> CelinaTierPolicy:
        """Load policy JSON; return fail-closed defaults on any failure."""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            return cls.from_dict(data)
        except Exception:
            return cls()


# Maps ai_tier -> tier-appropriate policy. Higher tiers opt into richer
# behavior; every field defaults to the fail-closed value on unknown tier.
TIER_POLICIES: dict[str, CelinaTierPolicy] = {
    "8gb": CelinaTierPolicy(
        serialize_heavy_models=False,
        enable_ladybug_graph=False,
        enable_gliner_slots=False,
        keep_voice_hot=False,
        enable_astra_1fps_buffer=False,
        enable_libkrun_sandbox=False,
        spotlight_batch_size=4,
        ram_psi_evict_threshold=15.0,
    ),
    "16gb": CelinaTierPolicy(
        serialize_heavy_models=False,
        enable_ladybug_graph=True,
        enable_gliner_slots=True,
        keep_voice_hot=False,
        enable_astra_1fps_buffer=False,
        enable_libkrun_sandbox=False,
        spotlight_batch_size=8,
        ram_psi_evict_threshold=20.0,
    ),
    "32gb": CelinaTierPolicy(
        serialize_heavy_models=True,
        enable_ladybug_graph=True,
        enable_gliner_slots=True,
        keep_voice_hot=True,
        enable_astra_1fps_buffer=True,
        enable_libkrun_sandbox=True,
        spotlight_batch_size=16,
        ram_psi_evict_threshold=25.0,
    ),
}


def policy_for_ai_tier(ai_tier: str | None, ram_mb: int | None = None) -> CelinaTierPolicy:
    """Return the CelinaTierPolicy for the given ai_tier.

    Falls back to the fail-closed default ("8gb") when ai_tier is None,
    empty, or unrecognized. ram_mb is accepted for future use / call-site
    compatibility and does not alter policy selection (policy derives from
    ai_tier only, which already factors RAM via hardware.json).
    """
    tier = (ai_tier or "").strip().lower()
    return TIER_POLICIES.get(tier, TIER_POLICIES["8gb"])


def policy_from_hardware_json(
    hw_path: str | Path = Path("/etc/macubuntu/hardware.json"),
) -> CelinaTierPolicy:
    """Build a CelinaTierPolicy from /etc/macubuntu/hardware.json.

    Returns fail-closed defaults when the file is missing or unreadable.
    """
    try:
        data = json.loads(Path(hw_path).read_text(encoding="utf-8"))
    except Exception:
        return TIER_POLICIES["8gb"]
    ai_tier = str(data.get("ai_tier", "8gb"))
    ram_mb = data.get("ram_mb")
    return policy_for_ai_tier(ai_tier, ram_mb)
