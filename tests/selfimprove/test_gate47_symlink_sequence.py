# Copyright 2026 Celina contributors.
"""Reject a proposed symlink followed by a write through the same path."""
from pathlib import Path

import pytest

from celina_pkg.selfimprove.l7.policy import PolicyViolationError, validate_patch_proposal
from celina_pkg.selfimprove.l7.types import PatchPolicyConfig, PatchProposal

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "selfimprove/protected_hashes.json"


@pytest.mark.parametrize("name", ["alias", "selfimprove/protected_hashes.json"])
def test_symlink_creation_then_write_through_rejected(name: str) -> None:
    link = (
        f"diff --git a/{name} b/{name}\nnew file mode 120000\n"
        f"--- /dev/null\n+++ b/{name}\n@@ -0,0 +1 @@\n+selfimprove/protected_hashes.json\n"
    )
    write = (f"diff --git a/{name} b/{name}\n--- a/{name}\n+++ b/{name}\n"
             "@@ -1 +1 @@\n-old\n+new\n")
    with pytest.raises(PolicyViolationError):
        validate_patch_proposal(PatchProposal("symlink", link + write, "test"), MANIFEST, PatchPolicyConfig())
