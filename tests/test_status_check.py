import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import status_check  # noqa: E402


def test_operational_when_all_none():
    body = {"status": {"indicator": "none", "description": "All Systems Operational"}}
    assert status_check.classify(body) == "operational"


def test_incident_on_minor():
    body = {"status": {"indicator": "minor", "description": "Partial Outage"}}
    assert status_check.classify(body) == "incident"


def test_incident_on_major():
    body = {"status": {"indicator": "major", "description": "Major Outage"}}
    assert status_check.classify(body) == "incident"


def test_unknown_on_garbage():
    assert status_check.classify({}) == "unknown"
    assert status_check.classify({"status": {}}) == "unknown"
