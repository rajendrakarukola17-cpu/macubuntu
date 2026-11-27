"""Static unit checks; these do not start or validate installed services."""
import configparser
from pathlib import Path

UNITS = Path(__file__).resolve().parents[1] / "system" / "systemd"


def _unit(name):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    with (UNITS / name).open(encoding="utf-8") as stream:
        parser.read_file(stream)
    return parser


def test_ac_power_condition_is_checked_by_watcher_service_at_activation():
    timer = _unit("celina-web-watch.timer")
    service = _unit("celina-web-watch@.service")
    assert not timer.has_option("Timer", "ConditionACPower")
    assert service.get("Unit", "ConditionACPower") == "true"
