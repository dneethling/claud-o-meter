"""Exercise the real menu renderer with isolated files and no live accounts."""
import io
import json
import sys
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import render_menu as menu


def render(monkeypatch, tmp_path, payload, *, offline=False, compact=False, side_effects=False, codex=None):
    menu._out.clear()
    monkeypatch.setattr(menu, "OFFLINE", offline)
    monkeypatch.setattr(menu, "RENDER_ONLY", not side_effects)
    monkeypatch.setattr(menu, "GRAPHICS", "0")
    monkeypatch.setattr(menu, "DETAIL_LEVEL", "compact" if compact else "full")
    monkeypatch.setattr(menu, "THEME", "semantic")
    monkeypatch.setattr(menu, "STATUS_ALERT", "off")
    monkeypatch.setattr(menu, "MENUBAR_MODE", "claude")
    for name in ["RAW", "HISTORY_FILE", "LASTSEEN_FILE", "CC_SUMMARY", "CODEX_SUMMARY", "MUTE_FILE"]:
        monkeypatch.setattr(menu, name, tmp_path / name)
    monkeypatch.setattr(menu, "run_json", lambda args, *rest: codex if args[0] == menu.CODEX_USAGE else None)
    monkeypatch.setattr(menu, "_render_footer", lambda: None)
    alerts = Mock()
    monkeypatch.setattr(menu, "_alerts", alerts)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    assert menu.main() == 0
    return sys.stdout.getvalue(), alerts


def payload(session=20, weekly=95):
    return {"five_hour": {"utilization": session}, "seven_day": {"utilization": weekly}}


def test_weekly_limit_controls_warning_without_changing_title_meaning(monkeypatch, tmp_path):
    text, _ = render(monkeypatch, tmp_path, payload())
    assert text.splitlines()[0].startswith("20% · 95%w |")
    assert "color=#FF3B30" in text.splitlines()[0]
    assert "Nearly at limit · Weekly has 5% left" in text
    assert "20% used · 80% left" in text


def test_model_limit_can_raise_warning(monkeypatch, tmp_path):
    data = payload(10, 20)
    data["seven_day_opus"] = {"utilization": 100}
    text, _ = render(monkeypatch, tmp_path, data)
    assert "Limit reached · Opus has 0% left" in text
    assert "color=#FF3B30" in text.splitlines()[0]


def test_offline_does_not_fire_alerts_or_change_last_good_history(monkeypatch, tmp_path):
    (tmp_path / "RAW").write_text("saved")
    (tmp_path / "HISTORY_FILE").write_text("history")
    text, alerts = render(monkeypatch, tmp_path, payload(), offline=True, side_effects=True)
    alerts.assert_not_called()
    assert (tmp_path / "RAW").read_text() == "saved"
    assert (tmp_path / "HISTORY_FILE").read_text() == "history"
    assert "Saved reading · Weekly has 5% left" in text
    assert "Nearly at limit" not in text


def test_unknown_shape_preserves_cache_and_does_not_fire_alerts(monkeypatch, tmp_path):
    (tmp_path / "RAW").write_text("saved")
    text, alerts = render(monkeypatch, tmp_path, {"renamed": {}}, side_effects=True)
    alerts.assert_not_called()
    assert (tmp_path / "RAW").read_text() == "saved"
    assert "API shape may have changed" in text


def test_valid_reading_updates_cache(monkeypatch, tmp_path):
    data = payload()
    _, alerts = render(monkeypatch, tmp_path, data, side_effects=True)
    alerts.assert_called_once()
    assert json.loads((tmp_path / "RAW").read_text()) == data


def test_compact_keeps_secondary_codex_quota_even_without_primary(monkeypatch, tmp_path):
    codex = {"available": True, "quota": {"secondary": {"window": "5h", "used_percent": 42}}}
    text, _ = render(monkeypatch, tmp_path, payload(), compact=True, codex=codex)
    assert "Quota · 5h · 42% used · 58% left" in text
    assert "Today ·" not in text
    assert "all-time" not in text


def test_broken_percentage_does_not_hide_valid_weekly_limit(monkeypatch, tmp_path):
    text, _ = render(monkeypatch, tmp_path, payload("not-a-number", 95))
    assert text.startswith("?% · 95%w")
    assert "Weekly has 5% left" in text


def test_nonobject_response_is_a_recoverable_shape_error(monkeypatch, tmp_path):
    text, _ = render(monkeypatch, tmp_path, [])
    assert "API shape may have changed" in text


def test_model_label_cannot_add_swiftbar_actions(monkeypatch, tmp_path):
    data = payload()
    data["limits"] = [{"scope": {"model": {"display_name": "Opus|bash=oops\nInjected"}}, "percent": 90}]
    text, _ = render(monkeypatch, tmp_path, data)
    assert "Opus|bash" not in text
    assert "\nInjected" not in text


def test_preference_menu_marks_current_selection(monkeypatch):
    menu._out.clear()
    monkeypatch.setattr(menu, "DETAIL_LEVEL", "compact")
    menu._render_preferences()
    lines = menu._out
    compact = next(line for line in lines if line.startswith("---- Limits only"))
    full = next(line for line in lines if line.startswith("---- Full dashboard"))
    assert "checked=true" in compact
    assert "checked=false" in full
    assert "param2='DETAIL_LEVEL' param3='compact'" in compact
