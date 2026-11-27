"""
Shared pytest fixtures for the macubuntu security, reliability & hardware test suite.

P0-11 / memory v2: redirect HOME / XDG_CONFIG_HOME / XDG_DATA_HOME to a temp
dir for the entire test session so the EvidenceLedger and config paths never
touch the real user's ~/.local/share/celina/ or ~/.config/celina/ audit state.
config.py resolves HOME at import time, and pytest loads conftest.py before test
modules, so redirecting here (at conftest import time) is early enough.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# --- Redirect HOME & XDG to a temp dir BEFORE any test module imports celina_pkg ---
_TEST_HOME = Path(tempfile.mkdtemp(prefix="macubuntu-test-home-"))
_TEST_XDG_CONFIG = _TEST_HOME / ".config"
_TEST_XDG_DATA = _TEST_HOME / ".local" / "share"
_TEST_XDG_CONFIG.mkdir(parents=True, exist_ok=True)
_TEST_XDG_DATA.mkdir(parents=True, exist_ok=True)

os.environ["HOME"] = str(_TEST_HOME)
os.environ["XDG_CONFIG_HOME"] = str(_TEST_XDG_CONFIG)
os.environ["XDG_DATA_HOME"] = str(_TEST_XDG_DATA)

# When the suite runs as root (CI containers, sudo), tests that spawn fresh
# interpreter processes must inherit an isolated target home too.  config.py
# resolves TARGET_HOME -> SUDO_USER -> Path.home(); pin TARGET_HOME to the
# temp home and clear SUDO_USER so subprocesses never write to /root.
if os.geteuid() == 0:
    os.environ["TARGET_HOME"] = str(_TEST_HOME)
    os.environ.pop("SUDO_USER", None)

REPO_ROOT = Path(__file__).resolve().parent.parent
CELINA_ROOT = REPO_ROOT / "features" / "celina"
if str(CELINA_ROOT) not in sys.path:
    sys.path.insert(0, str(CELINA_ROOT))


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(autouse=True, scope="session")
def _isolate_evidence_ledger_state():
    """Isolate EvidenceLedger anchor/ledger state for deterministic test runs.

    HOME/XDG are already redirected at conftest import time (above).
    This fixture restores original env on teardown and discards the temp home dir.
    """
    old_home = os.environ.get("HOME")
    old_xdg_config = os.environ.get("XDG_CONFIG_HOME")
    old_xdg_data = os.environ.get("XDG_DATA_HOME")

    yield

    if old_home is not None:
        os.environ["HOME"] = old_home
    else:
        os.environ.pop("HOME", None)
    if old_xdg_config is not None:
        os.environ["XDG_CONFIG_HOME"] = old_xdg_config
    else:
        os.environ.pop("XDG_CONFIG_HOME", None)
    if old_xdg_data is not None:
        os.environ["XDG_DATA_HOME"] = old_xdg_data
    else:
        os.environ.pop("XDG_DATA_HOME", None)

    shutil.rmtree(_TEST_HOME, ignore_errors=True)
