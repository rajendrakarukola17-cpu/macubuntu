"""Seed router restrictions and simulation regression; no host DB or actuators."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "features/celina"))
from celina_pkg import router, seed_knowledge


def fail_db():
    raise AssertionError("dry-run must not acquire or initialize a memory database")


def new_router():
    instance = router.CelinaRouter.__new__(router.CelinaRouter)
    instance._session_tier = None
    return instance


def make_seed(tmp_path):
    root = tmp_path / "seed"
    corpus = root / "corpus"
    corpus.mkdir(parents=True)
    (root / "SOUL.md").write_text("# Voice\n- Be concise\n", encoding="utf8")
    (corpus / "note.md").write_text("# Note\nAn example note\n", encoding="utf8")
    refs = root / "reference"
    refs.mkdir()
    (refs / "team.csv").write_text("name,role,topic\nAda,engineer,tool\n", encoding="utf8")
    return root


@pytest.mark.parametrize("how", ["prefix", "argument", "flag"])
def test_seed_router_dry_run_never_initializes_db(tmp_path, monkeypatch, how):
    root = make_seed(tmp_path)
    monkeypatch.setattr(seed_knowledge.memory, "get_db", fail_db)
    instance = new_router()
    text = f"/seed --dir {root} --include-ref"
    if how == "prefix":
        text = "simulate: " + text
    if how == "flag":
        text += " --dry-run"
    reply, source = instance.ask(text, dry_run=how == "argument")
    assert "dry_run=True" in reply
    assert "[DRY-RUN]" in reply
    assert "Total writes: 0" in reply
    assert source == "dry-run · seed"
    assert instance.last_trace.dry_run is True
    assert instance.last_trace.source == source


def test_seed_corpus_simulation_scans_without_initializing_db(tmp_path, monkeypatch):
    root = make_seed(tmp_path)
    monkeypatch.setattr(seed_knowledge.memory, "get_db", fail_db)
    result = seed_knowledge.seed_corpus(root / "corpus", include_reference=True,
                                       reference_dir=root / "reference", dry_run=True)
    assert result["files_scanned"] >= 1
    assert result["reference_packs"] == 1
    assert result["facts_seeded"] == result["triples_seeded"] == 0


def test_live_seed_router_denies_before_invoking_bundle(tmp_path, monkeypatch):
    root = make_seed(tmp_path)
    calls = []
    monkeypatch.setattr(seed_knowledge, "run_seed_bundle", lambda **kw: calls.append(kw))
    instance = new_router()
    reply, source = instance.ask(f"/seed --dir {root}", approval_cb=lambda *_: True)
    assert reply.startswith("Denied:") and "broker" in reply.lower()
    assert source == "seed"
    assert instance.last_trace.tools_called == []
    assert not calls
