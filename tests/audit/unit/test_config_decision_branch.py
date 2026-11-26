"""Coverage for the explicitly supplied model-decision path."""

from unittest.mock import sentinel

from celina_pkg import config


def test_format_model_decision_summary_uses_supplied_decision(monkeypatch):
    """A supplied decision is formatted without computing a replacement."""
    def unexpected_recompute():
        raise AssertionError("the supplied decision must not be recomputed")

    seen = []
    monkeypatch.setattr(config.model_selector, "make_full_model_decision", unexpected_recompute)
    monkeypatch.setattr(
        config.model_selector,
        "format_decision_summary",
        lambda decision: seen.append(decision) or "provided decision",
    )

    assert config.format_model_decision_summary(sentinel.decision) == "provided decision"
    assert seen == [sentinel.decision]
