"""Model-selection documentation is checked against local catalogs, offline."""
from pathlib import Path
import re

from celina_pkg import config, gear0_5_smolLM, model_selector

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "MODEL_SELECTION_ARCHITECTURE.md"
MODEL = "SmolLM2-135M-Instruct"


def test_default_smollm_resolves_to_catalog(monkeypatch):
    monkeypatch.delenv("CELINA_SMOLLM_MODEL", raising=False)
    assert config.SMOLLM_MODEL == MODEL
    assert gear0_5_smolLM.DEFAULT_SMOLLM_MODEL == MODEL
    assert gear0_5_smolLM.SMOLLM_MODELS[MODEL]["tag"] == "HuggingFaceTB/SmolLM2-135M-Instruct"
    decision = model_selector.make_full_model_decision()
    assert decision.smolLM["model"] == MODEL


def test_documented_catalog_rows_have_known_model_or_unverified_marker():
    text = DOC.read_text()
    sections = (("## 2.", "## 3.", set(model_selector.WHISPER_SPECS)),
                ("## 3.", "## 4.", set(model_selector.OLLAMA_CATALOG)),
                ("## 5.", "## 6.", set(gear0_5_smolLM.SMOLLM_MODELS)))
    for start, end, catalog in sections:
        section = text.split(start, 1)[1].split(end, 1)[0]
        rows = re.findall(r"^\| `([^`]+)` \|.*$", section, re.MULTILINE)
        assert rows, f"missing model rows in {start}"
        for line in section.splitlines():
            row = re.match(r"\| `([^`]+)` \|", line)
            if row:
                assert row.group(1) in catalog or "UNVERIFIED" in line, line
    assert "smollm-117m" not in text.lower()


def test_documented_cloud_provider_models_match_catalog_or_mark_unverified():
    from celina_pkg import provider_router

    section = DOC.read_text().split("## 6.", 1)[1].split("### Routing Strategies", 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("| ")][1:]
    known = {item.name.lower(): item.default_model for item in provider_router.PROVIDERS}
    assert len(rows) >= 11
    for line in rows:
        cells = [item.strip() for item in line.strip("|").split("|")]
        assert cells[0].lower() in known
        assert cells[2] == known[cells[0].lower()] or "UNVERIFIED" in line
