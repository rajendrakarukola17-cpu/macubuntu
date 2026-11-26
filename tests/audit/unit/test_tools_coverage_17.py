"""
tests/audit/unit/test_tools_coverage_17.py

Phase 7d — TASK 1: tools.py branch coverage tests (round 17).
Targets: _SemanticToolIndex._ensure_loaded (embedding cache path).

Covers: cache hit (fingerprint matches + blobs exist), disk cache hit,
disk cache miss (exception), embed and cache write, embed failure returns [],
custom model (skip disk cache), cache write OSError.

No real network or disk writes beyond temp file. Mocks embeddings module,
config.atomic_write_private_text, config.EMBED_DIM.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import os
from unittest.mock import patch, MagicMock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

from celina_pkg import tools  # noqa: E402


def _make_index(cache_path=None):
    if cache_path is None:
        cache_path = Path(tempfile.mktemp(suffix=".json"))
    return tools._SemanticToolIndex(cache_path=cache_path)


def _make_schemas():
    return [
        {
            "function": {
                "name": "test_tool",
                "description": "A test tool",
                "parameters": {"properties": {"arg1": {"description": "first arg"}}},
            }
        }
    ]


def _make_engine(model_fp="test-model", embed_result=None):
    eng = MagicMock()
    eng.model_fingerprint = model_fp
    if embed_result is None:
        embed_result = [[0.1, 0.2, 0.3]]
    eng.embed_texts = MagicMock(return_value=embed_result)
    return eng


# ---------------------------------------------------------------------------
# Cache hit: fingerprint matches and blobs exist
# ---------------------------------------------------------------------------

def test_ensure_loaded_cache_hit():
    """CB: _fingerprint == fp and _blobs non-empty → return cached blobs."""
    idx = _make_index()
    idx._fingerprint = "cached-fp"
    idx._blobs = [("tool", b"data")]
    schemas = _make_schemas()
    with patch("celina_pkg.embeddings.get_engine") as get_engine:
        eng = _make_engine(model_fp="test-model")
        # compute fingerprint with "test-model" won't match "cached-fp"
        # so we need to set _fingerprint to match
        idx._fingerprint = idx._compute_fingerprint(schemas, "test-model")
        get_engine.return_value = eng
        result = idx._ensure_loaded(schemas)
        assert result == [("tool", b"data")]
        eng.embed_texts.assert_not_called()


# ---------------------------------------------------------------------------
# Disk cache hit
# ---------------------------------------------------------------------------

def test_ensure_loaded_disk_cache_hit():
    """CB: cache file exists with matching fp → load from disk."""
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
        cache_path = Path(f.name)
        import base64, json
        fp = "disk-cache-fp"
        blobs = [("tool", b"vector-data")]
        payload = {
            "fp": fp,
            "items": {k: base64.b64encode(b).decode("ascii") for k, b in blobs},
        }
        f.write(json.dumps(payload) + "\n")

    try:
        idx = _make_index(cache_path)
        schemas = _make_schemas()
        # Compute fp that matches the disk cache
        idx._fingerprint = ""  # force disk cache path
        idx._blobs = []  # force disk cache path

        with patch("celina_pkg.embeddings.get_engine") as get_engine:
            eng = _make_engine(model_fp="custom:model")  # custom → skip disk cache... wait
            # We need non-custom to use disk cache
            eng.model_fingerprint = "real-model"
            get_engine.return_value = eng

            # Override _compute_fingerprint to return our fp
            orig = idx._compute_fingerprint
            idx._compute_fingerprint = lambda schemas, model_fp: fp
            try:
                result = idx._ensure_loaded(schemas)
                # Disk cache hit should return blobs from disk
                assert len(result) > 0
                assert result[0][0] == "tool"
            finally:
                idx._compute_fingerprint = orig
    finally:
        cache_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Disk cache exception → fall through to embed
# ---------------------------------------------------------------------------

def test_ensure_loaded_disk_cache_exception():
    """CB: cache file read raises exception → fall through to embed."""
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
        cache_path = Path(f.name)
        f.write("not valid json{{{{")

    try:
        idx = _make_index(cache_path)
        schemas = _make_schemas()
        idx._fingerprint = ""
        idx._blobs = []

        with patch("celina_pkg.embeddings.get_engine") as get_engine:
            eng = _make_engine(model_fp="real-model")
            get_engine.return_value = eng

            # Override _compute_fingerprint to return a known fp
            orig = idx._compute_fingerprint
            idx._compute_fingerprint = lambda schemas, model_fp: "embed-fp"
            try:
                result = idx._ensure_loaded(schemas)
                # Should fall through to embed since disk cache read failed
                eng.embed_texts.assert_called()
                assert len(result) > 0
            finally:
                idx._compute_fingerprint = orig
    finally:
        cache_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Embed failure → return []
# ---------------------------------------------------------------------------

def test_ensure_loaded_embed_failure():
    """CB: eng.embed_texts returns empty or wrong length → return []."""
    idx = _make_index()
    idx._fingerprint = ""
    idx._blobs = []
    schemas = _make_schemas()

    with patch("celina_pkg.embeddings.get_engine") as get_engine:
        eng = _make_engine(model_fp="custom:model", embed_result=[])  # empty → fail
        get_engine.return_value = eng
        result = idx._ensure_loaded(schemas)
        assert result == []


def test_ensure_loaded_embed_wrong_length():
    """CB: eng.embed_texts returns wrong number of vectors → return []."""
    idx = _make_index()
    idx._fingerprint = ""
    idx._blobs = []
    schemas = _make_schemas()

    with patch("celina_pkg.embeddings.get_engine") as get_engine:
        eng = _make_engine(model_fp="custom:model", embed_result=[[0.1, 0.2], [0.3, 0.4]])  # 2 vecs, 1 schema
        get_engine.return_value = eng
        result = idx._ensure_loaded(schemas)
        assert result == []


# ---------------------------------------------------------------------------
# Custom model → skip disk cache, go straight to embed
# ---------------------------------------------------------------------------

def test_ensure_loaded_custom_model():
    """CB: custom model → skip disk cache, embed directly."""
    idx = _make_index()
    idx._fingerprint = ""
    idx._blobs = []
    schemas = _make_schemas()

    with patch("celina_pkg.embeddings.get_engine") as get_engine:
        eng = _make_engine(model_fp="custom:model")
        get_engine.return_value = eng
        result = idx._ensure_loaded(schemas)
        eng.embed_texts.assert_called_once()
        assert len(result) > 0


# ---------------------------------------------------------------------------
# Successful embed → cache write (non-custom model)
# ---------------------------------------------------------------------------

def test_ensure_loaded_cache_write():
    """CB: non-custom model → write cache after embed."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        cache_path = Path(f.name)

    try:
        idx = _make_index(cache_path)
        idx._fingerprint = ""
        idx._blobs = []
        schemas = _make_schemas()

        written = []
        orig_write = tools.config.atomic_write_private_text
        tools.config.atomic_write_private_text = lambda path, content: written.append((path, content))
        try:
            with patch("celina_pkg.embeddings.get_engine") as get_engine:
                eng = _make_engine(model_fp="real-model")
                get_engine.return_value = eng
                result = idx._ensure_loaded(schemas)
                assert len(result) > 0
                # Cache write was attempted
                assert len(written) > 0
        finally:
            tools.config.atomic_write_private_text = orig_write
    finally:
        cache_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Cache write OSError → pass (non-fatal)
# ---------------------------------------------------------------------------

def test_ensure_loaded_cache_write_oserror():
    """CB: cache write raises OSError → pass, still return blobs."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        cache_path = Path(f.name)

    try:
        idx = _make_index(cache_path)
        idx._fingerprint = ""
        idx._blobs = []
        schemas = _make_schemas()

        def failing_write(path, content):
            raise OSError("disk full")

        orig_write = tools.config.atomic_write_private_text
        tools.config.atomic_write_private_text = failing_write
        try:
            with patch("celina_pkg.embeddings.get_engine") as get_engine:
                eng = _make_engine(model_fp="real-model")
                get_engine.return_value = eng
                result = idx._ensure_loaded(schemas)
                assert len(result) > 0  # Still returns blobs despite write failure
        finally:
            tools.config.atomic_write_private_text = orig_write
    finally:
        cache_path.unlink(missing_ok=True)
