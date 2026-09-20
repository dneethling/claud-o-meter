import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import widget_update as update


def isolate(monkeypatch, tmp_path, *, behind=0, ahead=0, dirty=False, fetch_fails=False, dependency_fails=False):
    monkeypatch.setattr(update, "ROOT", tmp_path)
    monkeypatch.setattr(update, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(update, "LEGACY", tmp_path / "legacy")
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if (args[0:2] == ["git", "fetch"] and fetch_fails) or ("pip" in args and dependency_fails):
            raise subprocess.CalledProcessError(1, args)
        result = ""
        if args[0:3] == ["git", "rev-parse", "--short"]: result = "abc123\n"
        elif args[0:3] == ["git", "rev-parse", "--abbrev-ref"]: result = "origin/master\n"
        elif args[0:2] == ["git", "rev-list"]: result = str(behind if args[-1].startswith("HEAD..") else ahead)
        elif args[0:2] == ["git", "status"]: result = " M edited.py" if dirty else ""
        elif args[0:2] == ["git", "log"]: result = "Clearer forecasts\nBetter setup\n"
        elif args[0:2] == ["git", "rev-parse"]: result = "a" * 40
        return SimpleNamespace(stdout=result)
    monkeypatch.setattr(update, "run", run)
    return calls


def test_failed_fetch_does_not_claim_up_to_date(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path, fetch_fails=True)
    update.LEGACY.write_text("3 old 123\n")
    assert update.check()["state"] == "offline"
    assert update.LEGACY.read_text() == "3 old 123\n"


def test_local_changes_block_install_without_merging(monkeypatch, tmp_path):
    calls = isolate(monkeypatch, tmp_path, behind=3, dirty=True)
    assert update.install()["state"] == "blocked"
    assert not any("merge" in args for args in calls)


def test_diverged_branch_is_never_merged(monkeypatch, tmp_path):
    calls = isolate(monkeypatch, tmp_path, behind=2, ahead=1)
    assert update.install()["state"] == "blocked"
    assert not any("merge" in args for args in calls)


def test_failed_dependencies_leave_retryable_error(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path, behind=2, dependency_fails=True)
    result = update.install()
    assert result["state"] == "error"
    assert "Python dependencies" in result["message"]
    assert json.loads(update.STATE.read_text())["state"] != "installed"


def test_regular_check_preserves_incomplete_install(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    update.save("error", "Dependency install failed")
    assert update.check()["state"] == "error"
    assert update.install()["state"] == "installed"


def test_success_runs_dependencies_and_agents_before_claiming_install(monkeypatch, tmp_path):
    calls = isolate(monkeypatch, tmp_path, behind=2)
    assert update.install()["state"] == "installed"
    assert ["git", "merge", "--ff-only", "a" * 40] in calls
    assert any("pip" in args for args in calls)
    assert any(str(tmp_path / "agents.sh") in args for args in calls)
    assert update.LEGACY.read_text().startswith("0 abc123")


def test_release_notes_come_from_incoming_changes(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path, behind=2)
    result = update.check()
    assert result["state"] == "available"
    assert result["changes"] == ["Clearer forecasts", "Better setup"]
