"""Thaw failures retain tracking so recovery can retry without touching a real cgroup."""

from pathlib import Path

from celina_pkg.os_events import AutoAppNapCoordinator


class TransientFreezer:
    def __init__(self):
        self.calls = []

    def set_frozen(self, cgroup, frozen):
        self.calls.append((cgroup, frozen))
        return len(self.calls) > 1


def test_thaw_all_keeps_failed_cgroup_until_later_success():
    freezer = TransientFreezer()
    coordinator = AutoAppNapCoordinator(freezer=freezer)
    fixture = Path("/fake-only/cgroup")
    coordinator._frozen_cgroups[37] = ("fixture", fixture)
    assert coordinator.thaw_all() == 0
    assert coordinator.napped_apps == ["fixture"]
    assert coordinator.thaw_all() == 1
    assert coordinator.napped_apps == []
    assert freezer.calls == [(fixture, False), (fixture, False)]


def test_regained_focus_retains_failed_thaw_for_next_tick():
    freezer = TransientFreezer()
    coordinator = AutoAppNapCoordinator(freezer=freezer)
    fixture = Path("/fake-only/cgroup")
    coordinator._frozen_cgroups[37] = ("fixture", fixture)
    coordinator._occluded_since[37] = ("fixture", 1.0)
    assert coordinator.step(active_app="fixture", active_audio_apps=set(), now=2.0) == ["fixture"]
    assert 37 in coordinator._occluded_since
    assert coordinator.step(active_app="fixture", active_audio_apps=set(), now=3.0) == []
    assert 37 not in coordinator._occluded_since
    assert freezer.calls == [(fixture, False), (fixture, False)]


def test_audio_resumption_retains_failed_thaw_for_next_tick():
    freezer = TransientFreezer()
    coordinator = AutoAppNapCoordinator(freezer=freezer)
    fixture = Path("/fake-only/cgroup")
    coordinator._frozen_cgroups[37] = ("fixture", fixture)
    assert coordinator.step(active_app="other", active_audio_apps={"fixture"}, now=2.0) == ["fixture"]
    assert coordinator.step(active_app="other", active_audio_apps={"fixture"}, now=3.0) == []
    assert freezer.calls == [(fixture, False), (fixture, False)]
