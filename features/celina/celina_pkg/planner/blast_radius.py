"""macubuntu celina_pkg.planner.blast_radius - Blast-radius estimator for proposed actions.

Estimates blast radius along three axes: reversibility, SSH disruption, ambiguity.
Returns a score (0-100) and risk band. Fail-closed: unknown => high risk.
stdlib-only: re, json, datetime.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass, field

from .. import policy as _policy

# Word-boundary regexes (P0-4 fix): no substring false positives on "confirm",
# "alarm", "add", "install", "wall", etc.
_IRREVERSIBLE_RE = re.compile(
    r"(?:\b(?:remove|delete|purge|uninstall|mask|disable|format|mkfs|shred|wipefs|fdisk|parted|reboot|shutdown|poweroff|rm|dd)\b"
    r"|chmod\s+-R|chown\s+-R|rm\s+-[a-zA-Z]*[rf]|curl\b.*\|\s*(?:ba)?sh|pkexec|sudo|crontab|iptables|nft|systemctl\s+(?:stop|disable)|kill\s+-9)",
    re.IGNORECASE,
)
_SSH_NET_RE = re.compile(
    r"\b(?:ssh|sshd|network|netplan|firewall|ufw|iptables|nft|resolv|dns|gateway|interface|wifi|ethernet|nmcli)\b",
    re.IGNORECASE,
)
_AMBIGUOUS_RE = re.compile(
    r"(?:\b(?:all|every|anything|everything|glob|recursive|force)\b|--force\b|\s-[a-zA-Z]*f\b|\*)",
    re.IGNORECASE,
)
_KNOWN_SAFE_READONLY_RE = re.compile(
    r"^\s*(?:read|get|list|show|inspect|search|find|check|status|stat|describe|query|recall|help|version)\b",
    re.IGNORECASE,
)


@dataclass
class BlastRadiusAssessment:
    score: int
    band: str
    reversibility: str
    ssh_disruption: bool
    ambiguity: str
    reasons: list = field(default_factory=list)
    assessed_at: str = field(
        default_factory=lambda: _dt.datetime.now(_dt.UTC).isoformat().replace("+00:00", "Z"),
    )


def _score_band(score: int) -> str:
    """Delegate to the canonical policy engine (audit item 4)."""
    return _policy.score_to_band(score)


class BlastRadiusEstimator:
    def __init__(
        self,
        *,
        custom_irreversible: list | None = None,
        custom_ssh: list | None = None,
        custom_ambiguous: list | None = None,
    ) -> None:
        self.irreversible_hints = tuple(custom_irreversible) if custom_irreversible else tuple()
        self.ssh_hints = tuple(custom_ssh) if custom_ssh else tuple()
        self.ambiguity_hints = tuple(custom_ambiguous) if custom_ambiguous else tuple()

    def assess(
        self,
        *,
        action: str = "",
        target: str | None = None,
        params: dict | None = None,
        context: dict | None = None,
    ) -> BlastRadiusAssessment:
        action = (action or "").strip()
        target = (target or "").strip()
        params = params or {}
        context = context or {}

        score = 0
        reasons = []

        low_txt = (action + " " + target + " " + json.dumps(params, default=str)).lower()

        reversible = True
        irr_hit = False
        if _IRREVERSIBLE_RE.search(low_txt):
            irr_hit = True
            reversible = False
            score += 55  # P0-4: irreversible always >= 55, never "low"
            reasons.append("irreversible action detected")

        rev = "irreversible" if not reversible else "reversible"

        ssh = False
        if _SSH_NET_RE.search(low_txt):
            ssh = True
            score += 30
            reasons.append("SSH/network disruption detected")

        amb_score = 0
        if not action or len(action) < 3:
            amb_score += 20
            reasons.append("action under-specified or missing")
        if not target and "target" not in params:
            amb_score += 15
            reasons.append("no explicit target")
        if _AMBIGUOUS_RE.search(low_txt):
            amb_score += 25
            reasons.append("ambiguity hint detected")
        if amb_score >= 40:
            ambiguity = "high"
            score += 25
        elif amb_score >= 20:
            ambiguity = "medium"
            score += 10
        else:
            ambiguity = "low"

        # P0-4: empty action fails closed (band == "high", score >= 60)
        if not action:
            score = max(score, 60)
            band = "high"
            reversibility = "irreversible"
        else:
            # P0-4: unknown non-readonly verbs fail-closed (score >= 30)
            if not irr_hit and not _KNOWN_SAFE_READONLY_RE.match(action):
                score = max(score, 30)
                rev = "irreversible"
                reasons.append("unknown mutating verb — fail-closed")
                reversible = False
                rev = "irreversible"
            score = min(score, 100)
            band = _score_band(score)
            reversibility = rev

        return BlastRadiusAssessment(
            score=score,
            band=band,
            reversibility=reversibility,
            ssh_disruption=ssh,
            ambiguity=ambiguity,
            reasons=reasons,
        )

    def is_safe_to_autoexec(self, *, assessment: BlastRadiusAssessment) -> bool:
        return (
            assessment.band == "low"
            and assessment.reversibility == "reversible"
            and not assessment.ssh_disruption
            and assessment.ambiguity == "low"
        )
