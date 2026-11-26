"""No real input: ensure a changed semantic match cannot reach an actuator."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "features/celina"))
from celina_pkg import automation, semantic_blueprint


def test_changed_element_position_refused_before_actuation(monkeypatch):
    before = semantic_blueprint.build_blueprint_from_records("App", "Win", [
        {"role": "push button", "label": "Save", "x": 100, "y": 100}])
    after = semantic_blueprint.build_blueprint_from_records("App", "Win", [
        {"role": "push button", "label": "Save", "x": 300, "y": 100}])
    monkeypatch.setattr(automation, "is_stop_requested", lambda: False)
    monkeypatch.setattr(automation, "get_active_window_info", lambda: {"app": "App", "title": "Win"})
    monkeypatch.setattr(automation, "get_semantic_blueprint", lambda **kw: after if kw.get("force_refresh") else before)
    monkeypatch.setattr(automation.input_broker, "check_allowed", lambda **kw: (True, ""))
    called = []
    monkeypatch.setattr(automation, "_try_atspi_direct_action", lambda *a, **k: (called.append("direct") or True, "activated"))
    monkeypatch.setattr(automation, "_exec_move_and_click", lambda *a, **k: called.append("pointer"))
    result = automation.click_on_text("Save", approval_cb=lambda *_: True)
    assert "changed" in result.lower()
    assert not called


def test_direct_refusal_must_not_fall_back_to_pointer(monkeypatch):
    bp = semantic_blueprint.build_blueprint_from_records("App", "Win", [
        {"role": "push button", "label": "polkit", "x": 100, "y": 100}])
    monkeypatch.setattr(automation, "is_stop_requested", lambda: False)
    monkeypatch.setattr(automation, "get_active_window_info", lambda: {"app": "App", "title": "Win"})
    monkeypatch.setattr(automation, "get_semantic_blueprint", lambda **kw: bp)
    monkeypatch.setattr(automation.input_broker, "check_allowed", lambda **kw: (True, ""))
    monkeypatch.setattr(automation, "_exec_move_and_click", lambda *a, **k: (_ for _ in ()).throw(AssertionError("actuator reached")))
    assert "blocked" in automation.click_on_text("polkit", approval_cb=lambda *_: True).lower()
