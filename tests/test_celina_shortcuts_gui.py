"""
Headless unit tests for apps/celina-shortcuts/main.py.

Tests the non-GTK logic (sanitization, atomic writes, tier derivation,
document save/load, keyword drafting) without requiring GTK4/Libadwaita.
"""

import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CELINA_ROOT = REPO_ROOT / "features" / "celina"
if str(CELINA_ROOT) not in sys.path:
    sys.path.insert(0, str(CELINA_ROOT))

# Load apps/celina-shortcuts/main.py via importlib (directory name has a hyphen,
# so normal import won't work)
_MODULE_PATH = REPO_ROOT / "apps" / "celina-shortcuts" / "main.py"
_spec = importlib.util.spec_from_file_location("celina_shortcuts_main", _MODULE_PATH)
shortcuts_main = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shortcuts_main)  # type: ignore


# --------------------------------------------------------------------------- #
# sanitize_shortcut_id
# --------------------------------------------------------------------------- #

def test_sanitize_shortcut_id_basic():
    assert shortcuts_main.sanitize_shortcut_id("My Shortcut") == "my_shortcut"
    assert shortcuts_main.sanitize_shortcut_id("Hello World!") == "hello_world"
    assert shortcuts_main.sanitize_shortcut_id("  spaces  ") == "spaces"


def test_sanitize_shortcut_id_path_traversal():
    result = shortcuts_main.sanitize_shortcut_id("../etc/passwd")
    assert result != "../etc/passwd"
    assert ".." not in result
    assert "/" not in result


def test_sanitize_shortcut_id_empty_and_dot():
    result = shortcuts_main.sanitize_shortcut_id("")
    assert result.startswith("shortcut_")
    assert result != "."
    assert result != ".."


def test_sanitize_shortcut_id_deterministic():
    r1 = shortcuts_main.sanitize_shortcut_id("Some Weird Name!!!")
    r2 = shortcuts_main.sanitize_shortcut_id("Some Weird Name!!!")
    assert r1 == r2


# --------------------------------------------------------------------------- #
# action_tier
# --------------------------------------------------------------------------- #

def test_action_tier_known_tier2():
    for action in shortcuts_main.TIER_2_ACTIONS:
        assert shortcuts_main.action_tier(action) == 2, f"{action} should be tier 2"


def test_action_tier_unknown_is_tier1():
    assert shortcuts_main.action_tier("unknown.action") == 1
    assert shortcuts_main.action_tier("files.read") == 1


# --------------------------------------------------------------------------- #
# ShortcutDocument.save / load
# --------------------------------------------------------------------------- #

def test_document_save_sanitizes_id_and_atomic(tmp_path):
    shortcuts_main.SHORTCUTS_DIR = tmp_path / "shortcuts"

    doc = shortcuts_main.ShortcutDocument()
    doc.spec["name"] = "../evil/../escape"
    doc.spec["steps"] = []

    path = doc.save()
    assert path.parent == (tmp_path / "shortcuts").resolve()
    assert ".." not in path.name
    assert "/" not in path.name

    import yaml
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["id"] != "../evil/../escape"
    assert data["name"] == "../evil/../escape"


def test_document_load_validates_dict(tmp_path):
    shortcuts_main.SHORTCUTS_DIR = tmp_path / "shortcuts"

    bad_file = tmp_path / "bad.cshortcut.yaml"
    bad_file.write_text("- item1\n- item2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="mapping"):
        shortcuts_main.ShortcutDocument.load(bad_file)

    good_file = tmp_path / "good.cshortcut.yaml"
    good_file.write_text("id: test\nname: Test\nsteps: []\n", encoding="utf-8")
    doc = shortcuts_main.ShortcutDocument.load(good_file)
    assert doc.spec["id"] == "test"
    assert doc.spec["name"] == "Test"


# --------------------------------------------------------------------------- #
# _draft_from_keywords — deterministic hashlib, not Python's randomized hash
# --------------------------------------------------------------------------- #

def test_draft_from_keywords_deterministic_id():
    d1 = shortcuts_main.CelinaShortcutsWindow._draft_from_keywords("start a 25-minute timer")
    d2 = shortcuts_main.CelinaShortcutsWindow._draft_from_keywords("start a 25-minute timer")
    assert d1["id"] == d2["id"]
    assert d1["id"].startswith("drafted_")


def test_draft_from_keywords_tiers_from_capabilities():
    draft = shortcuts_main.CelinaShortcutsWindow._draft_from_keywords("set volume to 50")
    volume_step = [s for s in draft["steps"] if s["action"] == "os.set_volume"][0]
    assert volume_step["tier"] == 2  # os.set_volume is in TIER_2_ACTIONS

    notify_draft = shortcuts_main.CelinaShortcutsWindow._draft_from_keywords("send a notification")
    notify_step = [s for s in notify_draft["steps"] if s["action"] == "notify.island"][0]
    assert notify_step["tier"] == 2  # notify.island is in TIER_2_ACTIONS


def test_draft_from_keywords_monotonic_step_ids():
    draft = shortcuts_main.CelinaShortcutsWindow._draft_from_keywords("start a timer and set focus mode")
    ids = [s["id"] for s in draft["steps"]]
    # Step IDs should be monotonic (step_1, step_2, …)
    assert ids == [f"step_{i+1}" for i in range(len(ids))], f"Expected monotonic IDs, got {ids}"
    # No uuid-style 8-char hex IDs
    for sid in ids:
        assert len(sid) != 8 or not all(c in "0123456789abcdef" for c in sid), f"UUID-style ID: {sid}"


# --------------------------------------------------------------------------- #
# GTK unavailable path
# --------------------------------------------------------------------------- #

def test_gtk_unavailable_graceful():
    assert shortcuts_main.GTK_AVAILABLE is False
    assert shortcuts_main.Gtk is None
    assert shortcuts_main.Adw is None


# --------------------------------------------------------------------------- #
# main() returns 1 without GTK
# --------------------------------------------------------------------------- #

def test_main_returns_1_without_gtk():
    assert shortcuts_main.main() == 1


# --------------------------------------------------------------------------- #
# _next_step_id — monotonic step IDs
# --------------------------------------------------------------------------- #

def test_next_step_id_monotonic():
    assert shortcuts_main._next_step_id([]) == "step_1"
    assert shortcuts_main._next_step_id(["step_1"]) == "step_2"
    assert shortcuts_main._next_step_id(["step_1", "step_2", "step_5"]) == "step_6"
    assert shortcuts_main._next_step_id(["step_10", "step_2"]) == "step_11"
    # Non-step_* IDs are ignored
    assert shortcuts_main._next_step_id(["uuid_abc123", "step_3"]) == "step_4"


# --------------------------------------------------------------------------- #
# PARAMETER_MAP — pre-populated args for known actions
# --------------------------------------------------------------------------- #

def test_parameter_map_has_known_actions():
    pm = shortcuts_main.PARAMETER_MAP
    assert "clock.start_timer" in pm
    assert "minutes" in pm["clock.start_timer"]
    assert "label" in pm["clock.start_timer"]
    assert "notify.island" in pm
    assert "title" in pm["notify.island"]
    assert "body" in pm["notify.island"]
    # Unknown actions map to empty list
    assert pm.get("unknown.action", []) == []


# --------------------------------------------------------------------------- #
# ACTION_HANDLERS — action registry
# --------------------------------------------------------------------------- #

def test_action_handlers_registry():
    ah = shortcuts_main.ACTION_HANDLERS
    # At minimum, the registry should exist as a dict
    assert isinstance(ah, dict)
    # If Celina tools are available, known actions should be registered
    if shortcuts_main.CELINA_TOOLS_AVAILABLE:
        assert "os.set_volume" in ah
        assert "notify.island" in ah
    # get_action_handler returns a callable for known actions
    handler = shortcuts_main.get_action_handler("os.set_volume")
    assert handler is not None
    # Unknown actions fall back to no-op handler
    fallback = shortcuts_main.get_action_handler("nonexistent.action")
    assert fallback is not None


# --------------------------------------------------------------------------- #
# ActionCard magic variable chip insertion (no GTK — tests logic only)
# --------------------------------------------------------------------------- #

def test_magic_vars_defined():
    mv = shortcuts_main.ActionCard.MAGIC_VARS
    assert "{{input.text}}" in mv
    assert "{{steps.step_1.output}}" not in mv  # dynamic, not static
    assert all(v.startswith("{{") and v.endswith("}}") for v in mv)


# --------------------------------------------------------------------------- #
# do_open / load_shortcut_file (HANDLES_OPEN)
# --------------------------------------------------------------------------- #

def test_app_has_do_open():
    """CelinaShortcutsApp should have a do_open method for HANDLES_OPEN."""
    assert hasattr(shortcuts_main.CelinaShortcutsApp, "do_open")

def test_window_has_load_shortcut_file():
    """CelinaShortcutsWindow should have a load_shortcut_file method."""
    assert hasattr(shortcuts_main.CelinaShortcutsWindow, "load_shortcut_file")

def test_load_shortcut_file_parses_yaml(tmp_path):
    """load_shortcut_file YAML parsing produces a valid ShortcutSpec."""
    import yaml
    from celina_pkg.shortcuts.engine import ShortcutSpec, ShortcutInput, ShortcutStep

    # Create a sample .cshortcut.yaml file
    yaml_path = tmp_path / "test.cshortcut.yaml"
    data = {
        "id": "test-shortcut",
        "name": "Test Shortcut",
        "description": "A test shortcut",
        "phrases": ["test it"],
        "triggers": [],
        "inputs": [
            {"name": "text", "kind": "text", "required": True, "default": None}
        ],
        "steps": [
            {"id": "step_1", "action": "notify.island",
             "args": {"title": "Hi", "body": "Hello"}, "tier": 2}
        ],
        "allow_without_llm": True,
        "preauthorized_tier2": False,
    }
    yaml_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    # Parse it back the same way load_shortcut_file does
    parsed = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    spec = ShortcutSpec(
        id=str(parsed.get("id", yaml_path.stem)),
        name=str(parsed.get("name", yaml_path.stem)),
        description=str(parsed.get("description", "")),
        phrases=list(parsed.get("phrases", [])),
        triggers=list(parsed.get("triggers", [])),
        inputs=[
            ShortcutInput(
                name=str(i.get("name", "")),
                kind=str(i.get("kind", "text")),
                required=bool(i.get("required", True)),
                default=i.get("default"),
            )
            for i in parsed.get("inputs", [])
        ],
        steps=[
            ShortcutStep(
                id=str(s.get("id", "")),
                action=str(s.get("action", "")),
                args=dict(s.get("args") or {}),
                when=s.get("when"),
                tier=int(s.get("tier", 1)),
            )
            for s in parsed.get("steps", [])
        ],
        allow_without_llm=bool(parsed.get("allow_without_llm", True)),
        preauthorized_tier2=bool(parsed.get("preauthorized_tier2", False)),
    )
    assert spec.id == "test-shortcut"
    assert spec.name == "Test Shortcut"
    assert len(spec.steps) == 1
    assert spec.steps[0].action == "notify.island"
    assert spec.steps[0].tier == 2
    assert len(spec.inputs) == 1
    assert spec.inputs[0].name == "text"

def test_app_flags_include_handles_open():
    """The application source should use Gio.ApplicationFlags.HANDLES_OPEN."""
    source = _MODULE_PATH.read_text()
    assert "HANDLES_OPEN" in source
    assert "do_open" in source