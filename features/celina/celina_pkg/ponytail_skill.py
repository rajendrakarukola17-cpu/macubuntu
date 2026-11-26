"""
Celina AI (Celly) - Ponytail Skill Integration (v4.5, Update 18)

Ponytail — "The best code is the code you never wrote."
An AI agent skill that guides code generation toward minimalism using a
"Ladder of Laziness" decision framework. Before writing any code, the
agent asks six questions; only if all answers are "no" does it write
the minimum amount of working code.

Source: https://github.com/DietrichGebert/ponytail
License: MIT
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("celina.ponytail")

# Ponytail decision ladder questions
PONYTAIL_LADDER = [
    "Does this even need to exist?",
    "Can I reuse something already installed?",
    "Can a single line handle it?",
    "Can a native tool do it better?",
    "Can I delete something instead?",
    "Is the simplest solution good enough?",
]


class PonytailSkill:
    """
    Ponytail skill integration for Celina.

    Provides code-generation guidance that favors minimalism over
    over-engineering. When Celina needs to write code, Ponytail's
    decision ladder is consulted first to avoid unnecessary code.
    """

    def __init__(self, ponytail_path: Optional[str] = None):
        self.ponytail_path = ponytail_path or self._find_ponytail()
        self._installed = self.ponytail_path is not None

    @staticmethod
    def _find_ponytail() -> Optional[str]:
        """Find Ponytail installation on the system."""
        candidates = [
            "/home/jtcviza/.config/claude/skills/ponytail",
            os.path.expanduser("~/.config/claude/skills/ponytail"),
            os.path.expanduser("~/.local/share/ponytail"),
            "/opt/ponytail",
            "/home/jtcviza/Downloads/ponytail",
        ]
        for path in candidates:
            if Path(path).exists():
                return path
        return None

    @property
    def is_available(self) -> bool:
        """Check if Ponytail is installed."""
        return self._installed

    def evaluate_code_need(self, task_description: str) -> dict[str, Any]:
        """
        Run the Ponytail decision ladder on a code-generation task.

        Returns a dict with the ladder answers and a recommendation:
        - "write_minimal": Write the minimum code
        - "reuse": Reuse existing solution
        - "skip": Don't write code at all
        """
        answers = {}
        all_no = True

        for i, question in enumerate(PONYTAIL_LADDER, 1):
            # In a real implementation, this would query an LLM or use rules
            # For now, we return the questions for the agent to answer
            answers[f"q{i}"] = {
                "question": question,
                "answer": None,  # Agent fills this in
            }

        recommendation = {
            "task": task_description,
            "ladder": answers,
            "recommendation": "Apply Ponytail decision ladder before coding",
            "expected_code_reduction_pct": 54,
            "expected_token_savings_pct": 22,
        }

        return recommendation

    def get_ponytail_rules(self) -> str:
        """Return the Ponytail decision ladder as a rules string for injection into prompts."""
        rules = """# Ponytail Decision Ladder — Code Minimization Rules
Before writing ANY code, answer all six questions. Only write code if all answers are "no".

1. Does this even need to exist?
2. Can I reuse something already installed?
3. Can a single line handle it?
4. Can a native tool do it better?
5. Can I delete something instead?
6. Is the simplest solution good enough?

If any answer is "yes", do NOT write code — reuse, delete, or skip instead.
Expected results: 54% less code, 22% fewer tokens, 27% faster development.
"""
        return rules

    def install(self) -> tuple[bool, str]:
        """Install Ponytail from GitHub."""
        if not shutil.which("git"):
            return False, "git not found — cannot install Ponytail"

        install_dir = Path.home() / ".local" / "share" / "ponytail"
        if install_dir.exists():
            return True, f"Ponytail already installed at {install_dir}"

        try:
            subprocess.run(
                ["git", "clone", "https://github.com/DietrichGebert/ponytail", str(install_dir)],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.ponytail_path = str(install_dir)
            self._installed = True
            return True, f"Ponytail installed at {install_dir}"
        except subprocess.CalledProcessError as exc:
            return False, f"Failed to install Ponytail: {exc.stderr}"
        except Exception as exc:
            return False, f"Failed to install Ponytail: {exc}"


__all__ = ["PonytailSkill", "PONYTAIL_LADDER"]
