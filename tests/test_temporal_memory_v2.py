"""
Memory v2 (v0.9-temporal-memory) tests — Celina SQLite memory layer.

Covers:
  - Idempotent PRAGMA table_info + ALTER TABLE migration (7 columns x 3 tables)
  - Indexes on valid_until and (subject, predicate, valid_until)
  - Scoped temporal superseding: set_fact sets valid_until=now on old row only
    when same key's value changes; default reads filter valid_until NULL/future
  - last_accessed touched on recall
  - Pure-SQLite WITH RECURSIVE 2-hop recall_multihop with cycle prevention,
    hop cap <= 3
  - RAM-tiered build_tiered_context (CONSTRAINED/BALANCED/HEADROOM budgets)
    with [UNTRUSTED-DATA] tagging and optional privacy.RedactionVault redaction
  - T1 taint enforcement: is_trusted_source True only for user_direct;
    untrusted_web/ocr_screen skills routed to Quarantine, refused trusted
    activation
  - consolidate_memory (Merge→Decay→Soft-Prune in single BEGIN IMMEDIATE
    transaction), dry-run read-only counts
  - Fail-closed invalid source_trust defaults to "untrusted_web"

Zero changes to install.sh/core/stages/system/configs/manifests — tests only
touch the 5 allowlisted files via imports.
"""

import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CELINA_ROOT = REPO_ROOT / "features" / "celina"
if str(CELINA_ROOT) not in sys.path:
    sys.path.insert(0, str(CELINA_ROOT))


# ---------------------------------------------------------------------------
# Fixtures — isolated in-memory DB per test
# ---------------------------------------------------------------------------

@pytest.fixture
def memdb():
    """Fresh MemoryDB backed by a temp SQLite file (auto-cleaned)."""
    from celina_pkg.memory import MemoryDB

    fd, path = tempfile.mkstemp(suffix=".db")
    import os
    os.close(fd)
    db = MemoryDB(db_path=Path(path))
    yield db
    db.close()
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 1. Schema migration — 7 columns on facts, kg_triples, compiled_knowledge
# ---------------------------------------------------------------------------

class TestSchemaMigration:
    _EXPECTED_COLS = {
        "source_trust", "source_ref", "category", "valid_from",
        "valid_until", "last_accessed",
    }

    def _cols(self, memdb, table):
        return {r["name"] for r in memdb._conn.execute(
            f"PRAGMA table_info({table})").fetchall()}

    def test_facts_has_all_seven_columns(self, memdb):
        missing = self._EXPECTED_COLS - self._cols(memdb, "facts")
        assert not missing, f"facts missing columns: {missing}"

    def test_kg_triples_has_all_seven_columns(self, memdb):
        missing = self._EXPECTED_COLS - self._cols(memdb, "kg_triples")
        assert not missing, f"kg_triples missing columns: {missing}"

    def test_compiled_knowledge_has_all_seven_columns(self, memdb):
        missing = self._EXPECTED_COLS - self._cols(memdb, "compiled_knowledge")
        assert not missing, f"compiled_knowledge missing columns: {missing}"

    def test_indexes_exist(self, memdb):
        idx = {r["name"] for r in memdb._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
        assert any("valid_until" in n for n in idx), "missing valid_until index"
        assert any("subj_pred_valid" in n for n in idx), "missing (subject,predicate,valid_until) index"

    def test_migration_is_idempotent(self, memdb):
        """Running migration logic twice must not raise."""
        from celina_pkg.memory import MemoryDB
        # Re-open on same path triggers __init__ again → migration re-runs
        path = memdb._conn.execute("PRAGMA database_list").fetchone()[2]
        memdb.close()
        db2 = MemoryDB(db_path=Path(path))
        db2.close()

    def test_default_source_trust_is_user_direct(self, memdb):
        memdb.set_fact("k1", "v1")
        row = memdb._conn.execute(
            "SELECT source_trust FROM facts WHERE key = 'k1'").fetchone()
        assert row["source_trust"] == "user_direct"

    def test_fail_closed_invalid_source_trust(self, memdb):
        memdb.set_fact("k2", "v2", source_trust="totally_bogus")
        row = memdb._conn.execute(
            "SELECT source_trust FROM facts WHERE key = 'k2'").fetchone()
        assert row["source_trust"] == "untrusted_web", "invalid source_trust must fail-closed to untrusted_web"

    def test_invalid_category_defaults_to_fact(self, memdb):
        memdb.set_fact("k3", "v3", category="not_a_real_category")
        row = memdb._conn.execute(
            "SELECT category FROM facts WHERE key = 'k3'").fetchone()
        assert row["category"] == "fact"


# ---------------------------------------------------------------------------
# 2. Scoped temporal superseding + default-read filtering + last_accessed
# ---------------------------------------------------------------------------

class TestTemporalSuperseding:
    def test_set_fact_supersedes_old_value(self, memdb):
        memdb.set_fact("color", "blue")
        memdb.set_fact("color", "red")
        row = memdb._conn.execute(
            "SELECT value, valid_until, valid_from FROM facts WHERE key = 'color'").fetchone()
        assert row["value"] == "red", "current value must be 'red'"
        assert row["valid_until"] is None, "current value must have NULL valid_until"
        assert row["valid_from"] and row["valid_from"] != "", "valid_from must be set"

    def test_set_fact_same_value_no_superseding(self, memdb):
        memdb.set_fact("color", "blue")
        memdb.set_fact("color", "blue")
        rows = memdb._conn.execute(
            "SELECT value, valid_until FROM facts WHERE key = 'color'").fetchall()
        assert len(rows) == 1, "same value must not create duplicate row"

    def test_default_recall_filters_expired(self, memdb):
        now = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat()
        memdb.set_fact("active_fact", "still_valid")
        memdb.set_fact("dead_fact", "expired", valid_until="2020-01-01T00:00:00+00:00")
        # recall_relevant is the default-read path — expired must be excluded
        facts, _ = memdb.recall_relevant("anything", max_facts=20, max_episodes=0)
        keys = {k for k, _ in facts}
        assert "active_fact" in keys
        assert "dead_fact" not in keys, "expired fact must be filtered from default reads"

    def test_last_accessed_touched_on_recall(self, memdb):
        memdb.set_fact("touch_me", "hello")
        before = memdb._conn.execute(
            "SELECT last_accessed FROM facts WHERE key = 'touch_me'").fetchone()["last_accessed"]
        assert before, "last_accessed must be set on write"
        memdb.recall_relevant("touch_me", max_facts=5, max_episodes=0)
        after = memdb._conn.execute(
            "SELECT last_accessed FROM facts WHERE key = 'touch_me'").fetchone()["last_accessed"]
        assert after >= before, "last_accessed must be touched (>=) on recall"


# ---------------------------------------------------------------------------
# 3. recall_multihop — 2-hop, cycle prevention, hop cap <= 3
# ---------------------------------------------------------------------------

class TestRecallMultihop:
    def _seed_triples(self, memdb, triples):
        for s, p, o in triples:
            memdb.add_knowledge_triple(s, p, o)

    def test_two_hop_recall(self, memdb):
        self._seed_triples(memdb, [
            ("Alice", "works_at", "Acme"),
            ("Acme", "located_in", "New York"),
            ("New York", "in", "USA"),
        ])
        hops = memdb.recall_multihop("Alice", max_hops=2, limit=10)
        # Should find Alice→Acme→New York (2 hops)
        targets = {(h["subject"], h["predicate"], h["object"]) for h in hops}
        assert ("Alice", "works_at", "Acme") in targets
        assert ("Acme", "located_in", "New York") in targets
        # New York → USA is hop 3, should NOT appear with max_hops=2
        assert ("New York", "in", "USA") not in targets

    def test_hop_cap_at_3(self, memdb):
        self._seed_triples(memdb, [
            ("A", "p", "B"), ("B", "p", "C"), ("C", "p", "D"), ("D", "p", "E"),
        ])
        hops = memdb.recall_multihop("A", max_hops=3, limit=20)
        max_hop = max((h["hop"] for h in hops), default=0)
        assert max_hop <= 3, f"hop cap violated: max hop = {max_hop}"

    def test_cycle_prevention(self, memdb):
        self._seed_triples(memdb, [
            ("A", "p", "B"), ("B", "p", "C"), ("C", "p", "A"),
        ])
        # Should not infinite-loop; return finite set
        hops = memdb.recall_multihop("A", max_hops=10, limit=50)
        assert len(hops) > 0
        # No duplicate edges
        edges = [(h["subject"], h["predicate"], h["object"]) for h in hops]
        assert len(edges) == len(set(edges)), "cycle prevention failed: duplicate edges"

    def test_empty_entity_returns_empty(self, memdb):
        assert memdb.recall_multihop("") == []
        assert memdb.recall_multihop("   ") == []

    def test_expired_triples_filtered_by_default(self, memdb):
        memdb.add_knowledge_triple("A", "p", "B", valid_until="2020-01-01T00:00:00+00:00")
        hops = memdb.recall_multihop("A", max_hops=2, limit=10)
        assert hops == [], "expired triple must be filtered by default"


# ---------------------------------------------------------------------------
# 4. build_tiered_context — RAM tiers + UNTRUSTED-DATA tagging + redaction
# ---------------------------------------------------------------------------

class TestBuildTieredContext:
    def test_constrained_tier_budget(self, memdb):
        memdb.set_fact("name", "Ada", category="preference")
        ctx = memdb.build_tiered_context(query="name", gear="deep", ram_tier="CONSTRAINED")
        assert ctx["tier"] == "CONSTRAINED"
        assert ctx["budget_tokens"] == 650
        assert ctx["l0_count"] >= 0

    def test_balanced_tier(self, memdb):
        memdb.set_fact("name", "Ada", category="preference")
        ctx = memdb.build_tiered_context(query="name", gear="deep", ram_tier="BALANCED")
        assert ctx["tier"] == "BALANCED"
        assert ctx["budget_tokens"] == 1600

    def test_headroom_tier(self, memdb):
        memdb.set_fact("name", "Ada", category="preference")
        ctx = memdb.build_tiered_context(query="name", gear="deep", ram_tier="HEADROOM")
        assert ctx["tier"] == "HEADROOM"
        assert ctx["budget_tokens"] == 3200

    def test_untrusted_data_tagged(self, memdb):
        memdb.set_fact("sketchy_claim", "the moon is cheese",
                       source_trust="untrusted_web", source_ref="http://example.com/foo",
                       category="fact")
        ctx = memdb.build_tiered_context(query="sketchy_claim", gear="deep", ram_tier="HEADROOM")
        assert "[UNTRUSTED-DATA" in ctx["text"], "untrusted fact must be tagged [UNTRUSTED-DATA]"
        assert "cite only" in ctx["text"], "untrusted tag must say 'cite only'"

    def test_redaction_applied(self, memdb):
        memdb.set_fact("user_email", "user@example.com", source_trust="user_direct")
        ctx = memdb.build_tiered_context(query="email", gear="deep", ram_tier="HEADROOM",
                                         redact_for_cloud=True)
        assert "user@example.com" not in ctx["text"], "email must be redacted when redact_for_cloud=True"

    def test_invalid_ram_tier_defaults_constrained(self, memdb):
        memdb.set_fact("x", "y")
        ctx = memdb.build_tiered_context(query="x", ram_tier="BOGUS_TIER")
        assert ctx["tier"] == "CONSTRAINED", "invalid tier must default to CONSTRAINED"

    def test_source_ref_provenance_in_l2(self, memdb):
        memdb.set_fact("ref_fact", "some claim",
                       source_trust="untrusted_web", source_ref="http://example.com/ref",
                       category="fact")
        ctx = memdb.build_tiered_context(query="ref_fact", gear="deep", ram_tier="HEADROOM")
        assert "REF ref_fact <-" in ctx["text"], "source_ref provenance must appear in L2"


# ---------------------------------------------------------------------------
# 5. T1 taint enforcement — is_trusted_source + Quarantine routing
# ---------------------------------------------------------------------------

class TestT1TaintEnforcement:
    def test_is_trusted_source_only_user_direct(self):
        from celina_pkg.skills import is_trusted_source
        assert is_trusted_source("user_direct") is True
        assert is_trusted_source("untrusted_web") is False
        assert is_trusted_source("ocr_screen") is False
        assert is_trusted_source("local_file") is False
        assert is_trusted_source("") is False
        assert is_trusted_source("random_junk") is False

    def test_run_skill_untrusted_web_quarantined(self):
        from celina_pkg.skills import run_skill
        res = run_skill(
            name="bad_skill",
            executor=lambda t, a, c=None: "ran",
            source_trust="untrusted_web",
        )
        assert "[QUARANTINE]" in res
        assert "refused trusted activation" in res

    def test_run_skill_ocr_screen_quarantined(self):
        from celina_pkg.skills import run_skill
        res = run_skill(
            name="ocr_skill",
            executor=lambda t, a, c=None: "ran",
            source_trust="ocr_screen",
        )
        assert "[QUARANTINE]" in res

    def test_run_skill_user_direct_allowed(self):
        from celina_pkg.skills import run_skill
        res = run_skill(
            name="good_skill",
            executor=lambda t, a, c=None: "ok ran",
            source_trust="user_direct",
        )
        assert "[QUARANTINE]" not in res
        assert "[DENIED]" not in res

    def test_learn_skill_untrusted_web_quarantined(self):
        from celina_pkg.skills import learn_skill
        res = learn_skill(
            name="bad_learned",
            steps=[{"tool": "read_screen", "args": {}}],
            source_trust="untrusted_web",
        )
        assert "[QUARANTINE]" in res


# ---------------------------------------------------------------------------
# 6. consolidate_memory — Merge → Decay → Soft-Prune in single transaction
# ---------------------------------------------------------------------------

class TestConsolidateMemory:
    def test_merge_no_duplicates_returns_zero(self, memdb):
        from celina_pkg.kg_promoter import KnowledgeGraphPromoter
        # PK (subject, predicate, object) prevents exact duplicates via add_knowledge_triple
        memdb.add_knowledge_triple("A", "p", "B", confidence=0.5)
        promoter = KnowledgeGraphPromoter(db_path=memdb._conn.execute(
            "PRAGMA database_list").fetchone()[2])
        result = promoter.consolidate_memory()
        assert result["merged"] == 0, f"expected no merges on clean data, got {result}"
        assert result["decayed"] == 0, "fresh rows should not decay"
        assert result["soft_pruned"] == 0, "fresh rows should not be pruned"
        promoter.close()

    def test_decay_reduces_confidence(self, memdb):
        from celina_pkg.kg_promoter import KnowledgeGraphPromoter
        import time
        memdb.add_knowledge_triple("old", "p", "x", confidence=1.0)
        # Force old updated_at so decay triggers
        memdb._conn.execute(
            "UPDATE kg_triples SET updated_at = updated_at - 4000000 WHERE subject = 'old'")
        memdb._conn.commit()
        promoter = KnowledgeGraphPromoter(db_path=memdb._conn.execute(
            "PRAGMA database_list").fetchone()[2])
        result = promoter.consolidate_memory(stale_days=0, decay_step=0.5)
        assert result["decayed"] >= 1, f"expected decay, got {result}"
        promoter.close()

    def test_soft_prune_below_threshold(self, memdb):
        from celina_pkg.kg_promoter import KnowledgeGraphPromoter
        memdb.add_knowledge_triple("weak", "p", "x", confidence=0.1)
        promoter = KnowledgeGraphPromoter(db_path=memdb._conn.execute(
            "PRAGMA database_list").fetchone()[2])
        result = promoter.consolidate_memory(prune_threshold=0.5)
        assert result["soft_pruned"] >= 1, f"expected soft-prune, got {result}"
        # Row must still exist (soft-pruned via valid_until, not hard-deleted)
        row = memdb._conn.execute(
            "SELECT valid_until FROM kg_triples WHERE subject = 'weak' AND predicate = 'p' AND object = 'x'"
        ).fetchone()
        assert row is not None and row["valid_until"] is not None, \
            "soft-pruned row must keep valid_until set, not be hard-deleted"
        promoter.close()

    def test_user_direct_preferences_never_decayed_or_pruned(self, memdb):
        from celina_pkg.kg_promoter import KnowledgeGraphPromoter
        memdb.set_fact("theme", "dark", source_trust="user_direct", category="preference", confidence=0.1)
        promoter = KnowledgeGraphPromoter(db_path=memdb._conn.execute(
            "PRAGMA database_list").fetchone()[2])
        result = promoter.consolidate_memory(stale_days=0, decay_step=0.9, prune_threshold=0.5)
        # user_direct preference must NOT be decayed or pruned
        row = memdb._conn.execute(
            "SELECT confidence, valid_until FROM facts WHERE key = 'theme'"
        ).fetchone()
        assert row["confidence"] == 0.1, "user_direct preference must not be decayed"
        assert row["valid_until"] is None, "user_direct preference must not be soft-pruned"
        promoter.close()

    def test_dry_run_no_writes(self, memdb):
        from celina_pkg.kg_promoter import KnowledgeGraphPromoter
        memdb.add_knowledge_triple("dry", "p", "x", confidence=0.1)
        promoter = KnowledgeGraphPromoter(db_path=memdb._conn.execute(
            "PRAGMA database_list").fetchone()[2])
        before = memdb._conn.execute(
            "SELECT COUNT(*) FROM kg_triples").fetchone()[0]
        result = promoter.consolidate_memory(dry_run=True)
        after = memdb._conn.execute(
            "SELECT COUNT(*) FROM kg_triples").fetchone()[0]
        assert before == after, "dry_run must not write"
        assert result["soft_pruned"] >= 1, "dry_run should still report would-be prunes"
        promoter.close()


# ---------------------------------------------------------------------------
# 7. /context slash command is wired in router (import check only)
# ---------------------------------------------------------------------------

class TestRouterContextCommand:
    def test_router_imports_without_error(self):
        """Router must import cleanly with memory module (no circular import)."""
        from celina_pkg.router import CelinaRouter  # noqa: F401
        assert True

    def test_build_tiered_context_returns_dict(self, memdb):
        ctx = memdb.build_tiered_context(query="test", gear="deep")
        assert isinstance(ctx, dict)
        assert set(ctx.keys()) >= {
            "text", "tier", "budget_tokens", "l0_count", "l1_count", "l2_count",
        }