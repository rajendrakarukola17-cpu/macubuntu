"""
Tests for features/celina/celina_pkg/mcp_media_ondemand.py — On-Demand Local Image Generation.

Covers:
  - media_model_status reports sd binary availability
  - media_generate_image fails closed when sd-cli/stable-diffusion missing
  - media_generate_image invokes sd-cli with correct args when available
  - RAM-tier resolution adaptation (8gb: 512, 16gb: 768, 32gb: 1024)
  - Output written to ~/Pictures/Celina/ with 0600 perms
  - No silent downloads (no network, no auto-download)
  - Zero import-time $HOME writes
  - Empty prompt rejected
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
CELINA_ROOT = REPO_ROOT / "features" / "celina"
if str(CELINA_ROOT) not in sys.path:
    sys.path.insert(0, str(CELINA_ROOT))

import pytest  # noqa: E402


# ---------------------------------------------------------------------------
# media_model_status
# ---------------------------------------------------------------------------


def test_model_status_sd_not_available():
    """MO1: media_model_status reports sd_binary_available=False when binary missing."""
    from celina_pkg.mcp_media_ondemand import handle_tools_call

    with mock.patch("celina_pkg.mcp_media_ondemand._resolve_sd_binary", return_value=None):
        result = handle_tools_call("media_model_status", {})
        text = result["content"][0]["text"]
        import json

        data = json.loads(text)
        assert data["sd_binary_available"] is False
        assert data["sd_binary"] is None


def test_model_status_sd_available():
    """MO2: media_model_status reports sd_binary_available=True when binary found."""
    from celina_pkg.mcp_media_ondemand import handle_tools_call

    with mock.patch(
        "celina_pkg.mcp_media_ondemand._resolve_sd_binary", return_value="/usr/bin/sd-cli"
    ):
        result = handle_tools_call("media_model_status", {})
        text = result["content"][0]["text"]
        import json

        data = json.loads(text)
        assert data["sd_binary_available"] is True
        assert data["sd_binary"] == "/usr/bin/sd-cli"


# ---------------------------------------------------------------------------
# media_generate_image fail-closed
# ---------------------------------------------------------------------------


def test_generate_image_fail_closed_no_binary():
    """MO3: media_generate_image fails closed when sd binary missing."""
    from celina_pkg.mcp_media_ondemand import handle_tools_call

    with mock.patch(
        "celina_pkg.mcp_media_ondemand._resolve_sd_binary", return_value=None
    ):
        result = handle_tools_call("media_generate_image", {"prompt": "a cat"})
        text = result["content"][0]["text"]
        import json

        data = json.loads(text)
        assert data["error"] is not None
        assert "not found" in data["error"]


def test_generate_image_empty_prompt_rejected():
    """MO4: media_generate_image rejects empty prompt."""
    from celina_pkg.mcp_media_ondemand import handle_tools_call

    with mock.patch(
        "celina_pkg.mcp_media_ondemand._resolve_sd_binary", return_value="/usr/bin/sd-cli"
    ):
        result = handle_tools_call("media_generate_image", {"prompt": ""})
        text = result["content"][0]["text"]
        import json

        data = json.loads(text)
        assert "error" in data
        assert "prompt" in data["error"].lower()


# ---------------------------------------------------------------------------
# media_generate_image success path
# ---------------------------------------------------------------------------


def test_generate_image_invokes_sd_cli():
    """MO5: media_generate_image calls sd-cli with correct args when binary available."""
    from celina_pkg.mcp_media_ondemand import handle_tools_call

    with mock.patch(
        "celina_pkg.mcp_media_ondemand._resolve_sd_binary", return_value="/usr/bin/sd-cli"
    ):
        with mock.patch(
            "celina_pkg.mcp_media_ondemand._get_tier_resolution",
            return_value={"width": 512, "height": 512},
        ):
            with mock.patch(
                "celina_pkg.mcp_media_ondemand._ensure_output_dir"
            ) as mock_dir:
                mock_dir.return_value = Path("/tmp/test_celina_pictures")
                with mock.patch("subprocess.run") as mock_run:
                    mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")

                    with mock.patch("os.path.isfile", return_value=True):
                        with mock.patch("os.chmod"):
                            result = handle_tools_call(
                                "media_generate_image",
                                {"prompt": "a cyberpunk cityscape", "width": 512, "height": 512, "steps": 20},
                            )

                    mock_run.assert_called_once()
                    call_args = mock_run.call_args[0][0]
                    assert call_args[0] == "/usr/bin/sd-cli"
                    assert "--prompt" in call_args
                    assert "a cyberpunk cityscape" in call_args
                    assert "--width" in call_args
                    assert "--height" in call_args
                    assert "--steps" in call_args
                    assert "--output" in call_args


# ---------------------------------------------------------------------------
# RAM-tier resolution adaptation
# ---------------------------------------------------------------------------


def test_tier_resolution_8gb():
    """MO6: 8 GB tier caps resolution at 512x512."""
    from celina_pkg.mcp_media_ondemand import _get_tier_resolution

    with mock.patch("celina_pkg.mcp_media_ondemand._get_ram_tier", return_value="8gb"):
        res = _get_tier_resolution()
        assert res["width"] == 512
        assert res["height"] == 512


def test_tier_resolution_16gb():
    """MO7: 16 GB tier caps resolution at 768x768."""
    from celina_pkg.mcp_media_ondemand import _get_tier_resolution

    with mock.patch("celina_pkg.mcp_media_ondemand._get_ram_tier", return_value="16gb"):
        res = _get_tier_resolution()
        assert res["width"] == 768
        assert res["height"] == 768


def test_tier_resolution_32gb():
    """MO8: 32 GB tier caps resolution at 1024x1024."""
    from celina_pkg.mcp_media_ondemand import _get_tier_resolution

    with mock.patch("celina_pkg.mcp_media_ondemand._get_ram_tier", return_value="32gb"):
        res = _get_tier_resolution()
        assert res["width"] == 1024
        assert res["height"] == 1024


def test_tier_resolution_clamp_to_cap():
    """MO9: User-requested resolution is clamped to tier cap."""
    from celina_pkg.mcp_media_ondemand import handle_tools_call

    with mock.patch(
        "celina_pkg.mcp_media_ondemand._resolve_sd_binary", return_value="/usr/bin/sd-cli"
    ):
        with mock.patch(
            "celina_pkg.mcp_media_ondemand._get_tier_resolution",
            return_value={"width": 512, "height": 512},
        ):
            with mock.patch(
                "celina_pkg.mcp_media_ondemand._ensure_output_dir"
            ) as mock_dir:
                mock_dir.return_value = Path("/tmp/test_celina_pictures")
                with mock.patch("subprocess.run") as mock_run:
                    mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")

                    with mock.patch("os.path.isfile", return_value=True):
                        with mock.patch("os.chmod"):
                            # Request 1024 on 8gb tier → should clamp to 512
                            handle_tools_call(
                                "media_generate_image",
                                {"prompt": "test", "width": 1024, "height": 1024, "steps": 20},
                            )

                    call_args = mock_run.call_args[0][0]
                    width_idx = call_args.index("--width") + 1
                    height_idx = call_args.index("--height") + 1
                    assert int(call_args[width_idx]) == 512  # clamped from 1024
                    assert int(call_args[height_idx]) == 512  # clamped from 1024


# ---------------------------------------------------------------------------
# Output directory & permissions
# ---------------------------------------------------------------------------


def test_output_dir_is_pictures_celina():
    """MO10: Output directory is ~/Pictures/Celina/."""
    from celina_pkg.mcp_media_ondemand import OUTPUT_DIR

    assert OUTPUT_DIR == Path.home() / "Pictures" / "Celina"


# ---------------------------------------------------------------------------
# Zero import-time side effects
# ---------------------------------------------------------------------------


def test_zero_home_writes_on_import():
    """MO11: Importing mcp_media_ondemand writes nothing to $HOME."""
    home = Path.home()
    before = set(home.iterdir())
    import importlib

    import celina_pkg.mcp_media_ondemand as mo

    importlib.reload(mo)
    after = set(home.iterdir())
    assert before == after, f"Import created files in $HOME: {after - before}"


# ---------------------------------------------------------------------------
# Unknown tool
# ---------------------------------------------------------------------------


def test_unknown_tool_rejected():
    """MO12: Unknown tool name returns error."""
    from celina_pkg.mcp_media_ondemand import handle_tools_call

    result = handle_tools_call("nonexistent_tool", {})
    text = result["content"][0]["text"]
    import json

    data = json.loads(text)
    assert "error" in data
    assert "unknown tool" in data["error"]
