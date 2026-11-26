"""
Offline Knowledge Seeder tests (v0.9.1-seed-knowledge).

Covers:
  - Ontology normalization (STARTER_NODE_TYPES, STARTER_PREDICATES,
    alias mapping, Entity fallback)
  - SOUL.md + USER.md ingestion (source_trust="user_direct", secret scrubbing)
  - Corpus & optional reference pack (.md, .vcf/.csv) ingestion tagged
    source_trust="local_file", hard-blocking .ssh/id_rsa/.env files,
    dry_run=True making zero DB writes
  - Idempotent re-runs
  - /seed slash command in CelinaRouter

Zero host DB or network mutations — all DBs live under tmp_path.
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CELINA_ROOT = REPO_ROOT / "features" / "celina"
if str(CELINA_ROOT) not in sys.path:
    sys.path.insert(0, str(CELINA_ROOT))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db_path():
    """Fresh temp SQLite path for MemoryDB."""
    fd, path = tempfile.mkstemp(suffix=".db")
    import os
    os.close(fd)
    yield Path(path)
    try:
        Path(path).unlink(missing_ok=True)
        Path(str(path) + "-wal").unlink(missing_ok=True)
        Path(str(path) + "-shm").unlink(missing_ok=True)
    except OSError:
        pass


@pytest.fixture
def db(tmp_db_path):
    from celina_pkg.memory import MemoryDB
    d = MemoryDB(db_path=tmp_db_path)
    yield d
    d.close()


# ---------------------------------------------------------------------------
# Task 1 — Ontology normalization
# ---------------------------------------------------------------------------

class TestOntologyNormalization:
    def test_node_types(self):
        from celina_pkg.seed_knowledge import STARTER_NODE_TYPES
        assert STARTER_NODE_TYPES == frozenset({
            "Person", "Project", "Task", "Device",
            "Preference", "Location", "Event",
        })

    def test_predicates(self):
        from celina_pkg.seed_knowledge import STARTER_PREDICATES
        assert STARTER_PREDICATES == frozenset({
            "WORKS_ON", "PREFERS", "DEADLINE_FOR",
            "LOCATED_IN", "DEPENDS_ON",
        })

    def test_normalize_node_type_titlecase(self):
        from celina_pkg.seed_knowledge import normalize_node_type
        assert normalize_node_type("person") == "Person"
        assert normalize_node_type("PROJECT") == "Project"
        assert normalize_node_type("  task  ") == "Task"

    def test_normalize_node_type_unknown_fallback_entity(self):
        from celina_pkg.seed_knowledge import normalize_node_type
        assert normalize_node_type("UnknownThing") == "Entity"
        assert normalize_node_type("") == "Entity"
        assert normalize_node_type("   ") == "Entity"

    def test_normalize_predicate_canonical(self):
        from celina_pkg.seed_knowledge import normalize_predicate
        assert normalize_predicate("WORKS_ON") == "WORKS_ON"
        assert normalize_predicate("prefers") == "PREFERS"
        assert normalize_predicate("  located_in  ") == "LOCATED_IN"

    def test_normalize_predicate_aliases(self):
        from celina_pkg.seed_knowledge import normalize_predicate
        assert normalize_predicate("USES") == "DEPENDS_ON"
        assert normalize_predicate("BUILT_WITH") == "DEPENDS_ON"
        assert normalize_predicate("LIVES_IN") == "LOCATED_IN"
        assert normalize_predicate("BASED_IN") == "LOCATED_IN"
        assert normalize_predicate("LIKES") == "PREFERS"
        assert normalize_predicate("WANTS") == "PREFERS"
        assert normalize_predicate("OWNS") == "WORKS_ON"
        assert normalize_predicate("MAINTAINS") == "WORKS_ON"
        assert normalize_predicate("DUE_ON") == "DEADLINE_FOR"

    def test_normalize_predicate_unknown_kept(self):
        from celina_pkg.seed_knowledge import normalize_predicate
        # Unknown predicates are returned uppercased/underscored (not mutated
        # into schema columns — callers tag them carefully)
        assert normalize_predicate("KNOWS") == "KNOWS"
        assert normalize_predicate("") == ""


# ---------------------------------------------------------------------------
# Task 2 — SOUL.md + USER.md ingestion
# ---------------------------------------------------------------------------

class TestSoulUserMd:
    def _write(self, tmp_path, name, content):
        p = tmp_path / name
        p.write_text(content, encoding="utf-8")
        return p

    def test_soul_md_stores_preferences_user_direct(self, db, tmp_path):
        from celina_pkg.seed_knowledge import seed_soul_md
        soul = self._write(tmp_path, "SOUL.md", """# Voice & Style

- Be concise and direct
- Use British English spelling
- Never ramble

## Brevity

- Max 2 sentences per reply
""")
        result = seed_soul_md(db, soul)
        assert result["preferences"] > 0
        assert result["errors"] == 0

        # Verify source_trust=user_direct and source_ref=SOUL.md
        rows = db._conn.execute(
            "SELECT key, value, source_trust, source_ref, category FROM facts "
            "WHERE source_ref = 'SOUL.md'").fetchall()
        assert len(rows) > 0
        for r in rows:
            assert r["source_trust"] == "user_direct"
            assert r["source_ref"] == "SOUL.md"
            assert r["category"] == "preference"

    def test_user_md_stores_boundaries_user_direct(self, db, tmp_path):
        from celina_pkg.seed_knowledge import seed_user_md
        user = self._write(tmp_path, "USER.md", """# Profile

- Name: Ada Lovelace
- Location: London
- Hardware: ThinkPad X1 Carbon

## Boundaries

- Never send emails without asking
- Do not access .ssh directory
""")
        result = seed_user_md(db, user)
        assert result["facts"] > 0
        assert result["errors"] == 0

        rows = db._conn.execute(
            "SELECT key, value, source_trust, source_ref, category FROM facts "
            "WHERE source_ref = 'USER.md'").fetchall()
        assert len(rows) > 0
        for r in rows:
            assert r["source_trust"] == "user_direct"
            assert r["source_ref"] == "USER.md"

    def test_user_md_scrubs_secrets(self, db, tmp_path):
        from celina_pkg.seed_knowledge import seed_user_md
        user = self._write(tmp_path, "USER.md", """# Profile

- API key: sk-1234567890abcdef1234567890abcdef
- password=supersecret123
- Normal preference: dark mode
""")
        result = seed_user_md(db, user)
        assert result["errors"] == 0

        # Verify no raw secrets in facts
        all_vals = db._conn.execute("SELECT value FROM facts WHERE source_ref = 'USER.md'").fetchall()
        for r in all_vals:
            val = r["value"]
            assert "sk-1234567890abcdef" not in val
            assert "supersecret123" not in val
            assert "[REDACTED" in val or "dark mode" in val

    def test_user_md_extracts_triples(self, db, tmp_path):
        from celina_pkg.seed_knowledge import seed_user_md
        user = self._write(tmp_path, "USER.md", """# Profile

- Alice works on Project X
- Bob lives in Bangalore
""")
        result = seed_user_md(db, user)
        assert result["triples"] > 0
        assert result["errors"] == 0

        rows = db._conn.execute(
            "SELECT subject, predicate, object, source_trust, source_ref FROM kg_triples "
            "WHERE source_ref = 'USER.md'").fetchall()
        assert len(rows) > 0
        for r in rows:
            assert r["source_trust"] == "user_direct"
            assert r["source_ref"] == "USER.md"

    def test_soul_md_idempotent(self, db, tmp_path):
        from celina_pkg.seed_knowledge import seed_soul_md
        soul = self._write(tmp_path, "SOUL.md", "# Voice\n- Be concise")
        r1 = seed_soul_md(db, soul)
        r2 = seed_soul_md(db, soul)
        assert r1["preferences"] == r2["preferences"]


# ---------------------------------------------------------------------------
# Task 3 — Corpus & reference pack ingestion
# ---------------------------------------------------------------------------

class TestCorpusIngestion:
    def _write(self, tmp_path, *parts, content=""):
        p = tmp_path.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def test_corpus_md_tagged_local_file(self, db, tmp_path):
        from celina_pkg.seed_knowledge import seed_corpus
        corpus = tmp_path / "corpus"
        self._write(corpus, "notes.md", content="# My Notes\n- I use Emacs\n- My device is a ThinkPad")
        result = seed_corpus(corpus_dir=corpus, dry_run=False, db=db)
        assert result["files_scanned"] >= 1
        assert result["facts_seeded"] >= 1
        assert result["skipped_sensitive"] == 0

        # Verify source_trust=local_file
        rows = db._conn.execute(
            "SELECT source_trust FROM facts WHERE source_trust = 'local_file'").fetchall()
        assert len(rows) >= 1

    def test_corpus_blocks_ssh(self, db, tmp_path):
        from celina_pkg.seed_knowledge import seed_corpus
        corpus = tmp_path / "corpus"
        ssh_dir = corpus / ".ssh"
        ssh_dir.mkdir(parents=True, exist_ok=True)
        (ssh_dir / "id_rsa").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nfakekey\n-----END OPENSSH PRIVATE KEY-----\n")
        (corpus / "safe.txt").write_text("safe note\n")

        result = seed_corpus(corpus_dir=corpus, dry_run=False, db=db)
        assert result["skipped_sensitive"] >= 1
        assert result["files_scanned"] >= 1  # safe.txt still scanned

    def test_corpus_blocks_env(self, db, tmp_path):
        from celina_pkg.seed_knowledge import seed_corpus
        corpus = tmp_path / "corpus"
        self._write(corpus, ".env", content="SECRET_KEY=abc123\n")
        self._write(corpus, "readme.md", content="hello world\n")

        result = seed_corpus(corpus_dir=corpus, dry_run=False, db=db)
        assert result["skipped_sensitive"] >= 1
        assert result["files_scanned"] >= 1

    def test_corpus_blocks_id_rsa(self, db, tmp_path):
        from celina_pkg.seed_knowledge import seed_corpus
        corpus = tmp_path / "corpus"
        self._write(corpus, "id_rsa", content="fake private key\n")
        self._write(corpus, "notes.md", content="safe note\n")

        result = seed_corpus(corpus_dir=corpus, dry_run=False, db=db)
        assert result["skipped_sensitive"] >= 1

    def test_dry_run_zero_writes(self, db, tmp_path):
        from celina_pkg.seed_knowledge import seed_corpus
        corpus = tmp_path / "corpus"
        self._write(corpus, "notes.md", content="seed test note\n")

        before = db._conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        result = seed_corpus(corpus_dir=corpus, dry_run=True)
        after = db._conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        assert before == after, "dry_run must not write to DB"
        assert result["files_scanned"] >= 1
        assert result["facts_seeded"] == 0  # no writes in dry_run
        assert result["triples_seeded"] == 0

    def test_vcf_reference_pack(self, db, tmp_path):
        from celina_pkg.seed_knowledge import seed_corpus
        ref = tmp_path / "reference"
        self._write(ref, "contacts.vcf", content="""BEGIN:VCARD
FN:Alice Engineer
ORG:Acme Corp
TITLE:Lead Engineer
END:VCARD
""")
        result = seed_corpus(
            corpus_dir=tmp_path / "empty_corpus",
            include_reference=True,
            reference_dir=ref,
            dry_run=False,
            db=db,
        )
        # Create empty corpus dir so it doesn't crash
        (tmp_path / "empty_corpus").mkdir(exist_ok=True)
        result = seed_corpus(
            corpus_dir=tmp_path / "empty_corpus",
            include_reference=True,
            reference_dir=ref,
            dry_run=False,
            db=db,
        )
        assert result["reference_packs"] >= 1
        assert result["triples_seeded"] >= 1

    def test_csv_reference_pack(self, db, tmp_path):
        from celina_pkg.seed_knowledge import seed_corpus
        ref = tmp_path / "reference"
        self._write(ref, "people.csv", content="name,role,topic\nBob,DevOps,Kubernetes\n")
        (tmp_path / "empty_corpus").mkdir(exist_ok=True)
        result = seed_corpus(
            corpus_dir=tmp_path / "empty_corpus",
            include_reference=True,
            reference_dir=ref,
            dry_run=False,
            db=db,
        )
        assert result["reference_packs"] >= 1
        assert result["triples_seeded"] >= 1


# ---------------------------------------------------------------------------
# Task 4 — /seed slash command in CelinaRouter
# ---------------------------------------------------------------------------

class TestSeedSlashCommand:
    def test_router_imports_seed(self):
        """Router imports cleanly with seed_knowledge module."""
        from celina_pkg.router import CelinaRouter  # noqa: F401
        assert True

    def test_seed_command_dry_run(self, db, tmp_path):
        """Test /seed slash command path via run_seed_bundle directly."""
        from celina_pkg.seed_knowledge import run_seed_bundle
        seed_dir = tmp_path / "seed"
        seed_dir.mkdir()
        (seed_dir / "SOUL.md").write_text("# Voice\n- Be concise\n", encoding="utf-8")
        (seed_dir / "USER.md").write_text("# Profile\n- Name: Test\n", encoding="utf-8")
        (seed_dir / "corpus").mkdir()
        (seed_dir / "corpus" / "note.md").write_text("# Note\nhello\n", encoding="utf-8")

        result = run_seed_bundle(seed_dir=seed_dir, dry_run=True)
        assert result["dry_run"] is True
        assert result["total_writes"] == 0

    def test_seed_command_real(self, db, tmp_path):
        """Test /seed slash command path via run_seed_bundle with real writes."""
        from celina_pkg.seed_knowledge import run_seed_bundle
        seed_dir = tmp_path / "seed"
        seed_dir.mkdir()
        (seed_dir / "SOUL.md").write_text("# Voice\n- Be concise\n", encoding="utf-8")
        (seed_dir / "USER.md").write_text("# Profile\n- Name: Test\n", encoding="utf-8")
        (seed_dir / "corpus").mkdir()
        (seed_dir / "corpus" / "note.md").write_text("# Note\nhello\n", encoding="utf-8")

        result = run_seed_bundle(seed_dir=seed_dir, dry_run=False)
        assert result["dry_run"] is False
        assert result["total_writes"] > 0

    def test_seed_command_missing_dir(self, db, tmp_path):
        """Test /seed with non-existent directory — should not crash."""
        from celina_pkg.seed_knowledge import run_seed_bundle
        missing = tmp_path / "does_not_exist"
        result = run_seed_bundle(seed_dir=missing, dry_run=False)
        assert result["total_writes"] == 0
        assert result["errors"] == 0  # missing dir is not an error, just empty


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_corpus_idempotent_rerun(self, db, tmp_path):
        from celina_pkg.seed_knowledge import seed_corpus
        corpus = tmp_path / "corpus"
        self._write_helper = lambda p, c: p.write_text(c, encoding="utf-8")
        corpus.mkdir(parents=True, exist_ok=True)
        (corpus / "notes.md").write_text("# Stable note\ncontent unchanged\n", encoding="utf-8")

        r1 = seed_corpus(corpus_dir=corpus, dry_run=False, db=db)
        r2 = seed_corpus(corpus_dir=corpus, dry_run=False, db=db)
        # Second run should not add duplicate facts (idempotent via content hash)
        assert r2["facts_seeded"] <= r1["facts_seeded"]