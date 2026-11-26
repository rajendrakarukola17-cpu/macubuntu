"""
Regression tests for Update 18: Google Artemis Bridge for Android automation.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "macubuntu" / "features" / "celina"))

from celina_pkg import artemis_bridge  # noqa: E402


def test_artemis_not_available_without_install():
    """is_available should be False when Artemis is not installed."""
    with patch.object(artemis_bridge.Path, "exists", return_value=False), \
         patch.object(artemis_bridge.shutil, "which", return_value=None):
        bridge = artemis_bridge.ArtemisBridge()
        assert bridge.is_available is False


def test_artemis_status_structure():
    """get_status should return proper structure."""
    bridge = artemis_bridge.ArtemisBridge(artemis_path="/fake/path")
    status = bridge.get_status()
    assert "available" in status
    assert "running" in status
    assert "path" in status
    assert "mcp_port" in status
    assert "license" in status
    assert "source" in status
    assert "capabilities" in status
    assert status["license"] == "Apache-2.0"
    assert "android" in str(status["capabilities"]).lower()


def test_artemis_run_task_not_available():
    """run_task should return error when Artemis not available."""
    bridge = artemis_bridge.ArtemisBridge(artemis_path=None)
    result = bridge.run_task("open Chrome")
    assert result["success"] is False
    assert "not installed" in result["error"].lower()


def test_artemis_mcp_call_falls_back_to_cli():
    """When MCP fails, should fall back to CLI."""
    bridge = artemis_bridge.ArtemisBridge(artemis_path="/fake/artemis")
    bridge._server_proc = None  # Not running

    with patch.object(bridge, "_run_via_mcp", return_value={"success": False, "error": "connection refused"}), \
         patch.object(bridge, "_run_via_cli", return_value={"success": True, "result": "done", "method": "cli"}):
        result = bridge.run_task("open Chrome", approval_cb=lambda *a, **k: True)
        assert result["success"] is True
        assert result["method"] == "cli"


def test_artemis_capabilities_include_mcp():
    """Capabilities should include MCP server integration."""
    bridge = artemis_bridge.ArtemisBridge(artemis_path="/fake/path")
    status = bridge.get_status()
    caps = " ".join(status["capabilities"]).lower()
    assert "mcp" in caps
    assert "android" in caps


if __name__ == "__main__":
    test_artemis_not_available_without_install()
    test_artemis_status_structure()
    test_artemis_run_task_not_available()
    test_artemis_mcp_call_falls_back_to_cli()
    test_artemis_capabilities_include_mcp()
    print("All artemis_bridge tests passed!")
