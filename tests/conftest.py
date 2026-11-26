"""Shared pytest fixtures for the macubuntu security, reliability & hardware test suite."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CELINA_ROOT = REPO_ROOT / "features" / "celina"
if str(CELINA_ROOT) not in sys.path:
    sys.path.insert(0, str(CELINA_ROOT))


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT
