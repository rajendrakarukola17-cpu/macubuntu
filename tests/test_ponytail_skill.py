"""
Regression tests for Update 18: Ponytail Skill Integration.

Tests cover:
  - Ponytail availability detection
  - Decision ladder evaluation
  - Rule generation
  - Installation (mocked)
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "macubuntu" / "features" / "celina"))

from celina_pkg import ponytail_skill  # noqa: E402


def test_ponytail_not_available_without_install():
    """is_available should be False when Ponytail is not installed."""
    with patch.object(ponytail_skill.Path, "exists", return_value=False):
        skill = ponytail_skill.PonytailSkill()
        assert skill.is_available is False


def test_ponytail_ladder_has_six_questions():
    """PONYTAIL_LADDER should have exactly 6 questions."""
    assert len(ponytail_skill.PONYTAIL_LADDER) == 6
    assert "Does this even need to exist?" in ponytail_skill.PONYTAIL_LADDER[0]
    assert "Is the simplest solution good enough?" in ponytail_skill.PONYTAIL_LADDER[5]


def test_ponytail_evaluate_structure():
    """evaluate_code_need should return proper structure."""
    skill = ponytail_skill.PonytailSkill(ponytail_path="/fake/ponytail")
    skill._installed = True
    result = skill.evaluate_code_need("write a Python script to backup files")
    assert "task" in result
    assert "ladder" in result
    assert "recommendation" in result
    assert "expected_code_reduction_pct" in result
    assert "expected_token_savings_pct" in result
    assert result["task"] == "write a Python script to backup files"
    assert len(result["ladder"]) == 6


def test_ponytail_rules_string():
    """get_ponytail_rules should return a non-empty rules string."""
    skill = ponytail_skill.PonytailSkill(ponytail_path="/fake/ponytail")
    rules = skill.get_ponytail_rules()
    assert "Ponytail Decision Ladder" in rules
    assert "Does this even need to exist?" in rules
    assert "54% less code" in rules


def test_ponytail_install_without_git():
    """install should fail when git is not available."""
    with patch.object(ponytail_skill.shutil, "which", return_value=None):
        skill = ponytail_skill.PonytailSkill()
        success, message = skill.install()
        assert success is False
        assert "git not found" in message


def test_ponytail_already_installed():
    """install should return True when already installed."""
    skill = ponytail_skill.PonytailSkill(ponytail_path="/home/jtcviza/.local/share/ponytail")
    skill._installed = True
    success, message = skill.install()
    assert success is True


if __name__ == "__main__":
    test_ponytail_not_available_without_install()
    test_ponytail_ladder_has_six_questions()
    test_ponytail_evaluate_structure()
    test_ponytail_rules_string()
    test_ponytail_install_without_git()
    test_ponytail_already_installed()
    print("All ponytail_skill tests passed!")
