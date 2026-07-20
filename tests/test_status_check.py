import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import status_check  # noqa: E402


def _level(body):
    """classify() now returns (level, description); tests care about the level."""
    return status_check.classify(body)[0]


def test_operational_when_all_none():
    body = {"status": {"indicator": "none", "description": "All Systems Operational"}}
    assert _level(body) == "operational"


def test_minor_is_degraded_not_incident():
    # The whole point of the split: minor must NOT read as a full incident, so the
    # menu-bar warning does not fire for routine partial degradation.
    body = {"status": {"indicator": "minor", "description": "Partially Degraded Service"}}
    assert _level(body) == "degraded"


def test_incident_on_major():
    body = {"status": {"indicator": "major", "description": "Major Outage"}}
    assert _level(body) == "incident"


def test_incident_on_critical():
    body = {"status": {"indicator": "critical", "description": "Major Service Outage"}}
    assert _level(body) == "incident"


def test_description_passed_through():
    body = {"status": {"indicator": "minor", "description": "Partially Degraded Service"}}
    assert status_check.classify(body) == ("degraded", "Partially Degraded Service")


def test_unknown_on_garbage():
    assert _level({}) == "unknown"
    assert _level({"status": {}}) == "unknown"
