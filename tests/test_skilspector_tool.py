"""
Regression tests for Update 18: SkillSpector Security Scanner.

Tests cover:
  - SkillSpector availability detection
  - Skill scanning (mocked)
  - Security gating (allow/block)
  - Status reporting
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "macubuntu" / "features" / "celina"))

from celina_pkg import skilspector_tool  # noqa: E402


def test_skilspector_not_available_without_install():
    """is_available should be False when SkillSpector is not installed."""
    with patch.object(skilspector_tool.Path, "exists", return_value=False), \
         patch.object(skilspector_tool.shutil, "which", return_value=None):
        tool = skilspector_tool.SkillSpectorTool()
        assert tool.is_available is False


def test_skilspector_status_structure():
    """get_status should return proper structure."""
    tool = skilspector_tool.SkillSpectorTool(skilspector_path="/fake/skillspector")
    status = tool.get_status()
    assert "available" in status
    assert "path" in status
    assert "license" in status
    assert "source" in status
    assert "capabilities" in status
    assert "risk_levels" in status
    assert "recommendations" in status
    assert "LOW" in status["risk_levels"]
    assert "CRITICAL" in status["risk_levels"]


def test_skilspector_scan_not_available():
    """scan_skill should return error when SkillSpector not available."""
    tool = skilspector_tool.SkillSpectorTool(skilspector_path=None)
    result = tool.scan_skill("./my-skill")
    assert result["success"] is False
    assert "not installed" in result["error"].lower()


def test_skilspector_scan_and_gate_block_high():
    """scan_and_gate should block HIGH severity skills."""
    tool = skilspector_tool.SkillSpectorTool(skilspector_path="/fake/skillspector")
    # Mock scan_skill to return HIGH severity
    with patch.object(tool, "scan_skill", return_value={
        "success": True,
        "severity": "HIGH",
        "recommendation": "DO_NOT_INSTALL",
        "issues": [{"rule": "prompt_injection", "severity": "HIGH"}],
    }):
        result = tool.scan_and_gate("./my-skill", max_severity="MEDIUM")
        assert result["allowed"] is False
        assert "DO_NOT_INSTALL" in result["reason"] or "HIGH" in result["reason"]


def test_skilspector_scan_and_gate_allow_low():
    """scan_and_gate should allow LOW severity skills."""
    tool = skilspector_tool.SkillSpectorTool(skilspector_path="/fake/skillspector")
    with patch.object(tool, "scan_skill", return_value={
        "success": True,
        "severity": "LOW",
        "recommendation": "SAFE",
        "issues": [],
    }):
        result = tool.scan_and_gate("./my-skill", max_severity="MEDIUM")
        assert result["allowed"] is True
        assert "passed" in result["reason"].lower()


def test_skilspector_risk_levels():
    """RISK_SEVERITY should have the expected levels."""
    assert "LOW" in skilspector_tool.RISK_SEVERITY
    assert "MEDIUM" in skilspector_tool.RISK_SEVERITY
    assert "HIGH" in skilspector_tool.RISK_SEVERITY
    assert "CRITICAL" in skilspector_tool.RISK_SEVERITY


def test_skilspector_recommendations():
    """RISK_RECOMMENDATION should have the expected levels."""
    assert "SAFE" in skilspector_tool.RISK_RECOMMENDATION
    assert "CAUTION" in skilspector_tool.RISK_RECOMMENDATION
    assert "DO_NOT_INSTALL" in skilspector_tool.RISK_RECOMMENDATION


if __name__ == "__main__":
    test_skilspector_not_available_without_install()
    test_skilspector_status_structure()
    test_skilspector_scan_not_available()
    test_skilspector_scan_and_gate_block_high()
    test_skilspector_scan_and_gate_allow_low()
    test_skilspector_risk_levels()
    test_skilspector_recommendations()
    print("All skilspector_tool tests passed!")
