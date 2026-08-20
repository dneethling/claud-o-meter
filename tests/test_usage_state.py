import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import usage_state as us  # noqa: E402


def test_round_pct_halves_up_and_rejects_garbage():
    assert us.round_pct(29.4) == 29
    assert us.round_pct(29.5) == 30
    assert us.round_pct("7") == 7
    assert us.round_pct(0) == 0
    assert us.round_pct(None) is None
    assert us.round_pct("") is None
    assert us.round_pct("abc") is None


def test_color_thresholds():
    assert us.color_for_pct(0) == "green"
    assert us.color_for_pct(59) == "green"
    assert us.color_for_pct(60) == "orange"
    assert us.color_for_pct(84) == "orange"
    assert us.color_for_pct(85) == "red"
    assert us.color_for_pct(None) == "gray"


def test_fmt_reset_relative_buckets_are_tz_independent():
    now = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)
    assert us.fmt_reset((now - timedelta(minutes=5)).isoformat(), now) == "now"
    assert us.fmt_reset((now + timedelta(minutes=42)).isoformat(), now) == "in 42m"
    assert us.fmt_reset((now + timedelta(hours=3, minutes=7)).isoformat(), now) == "in 3h 07m"
    assert us.fmt_reset("", now) == ""
    assert us.fmt_reset("not-a-date", now) == ""


def test_fmt_reset_far_out_uses_date_branch():
    now = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)
    out = us.fmt_reset((now + timedelta(days=3)).isoformat(), now)
    # Date branch: three space-separated tokens (weekday day month), no clock time.
    parts = out.split()
    assert len(parts) == 3
    assert ":" not in out
    assert parts[0] in us._WEEKDAYS
    assert parts[2] in us._MONTHS


def test_fmt_reset_handles_z_suffix_and_fractional_seconds():
    now = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)
    assert us.fmt_reset("2026-07-19T12:42:00.123456Z", now) == "in 42m"


def test_fmt_money():
    assert us.fmt_money(120, "USD", 2) == "$1.20"
    assert us.fmt_money(5000, "USD", 2) == "$50.00"
    assert us.fmt_money(120, "ZAR", 2) == "R1.20"
    assert us.fmt_money(120, "XYZ", 2) == "1.20 XYZ"
    assert us.fmt_money(None, "USD", 2) == ""


def _demo_raw():
    return {
        "five_hour": {"utilization": 29, "resets_at": "2026-07-19T18:30:00Z"},
        "seven_day": {"utilization": 7, "resets_at": "2026-07-22T09:00:00Z"},
        "limits": [
            {"scope": {"model": {"display_name": "Sonnet"}}, "percent": 4,
             "resets_at": "2026-07-22T09:00:00Z"},
            {"scope": {"model": {"display_name": "Opus"}}, "percent": 12,
             "resets_at": "2026-07-22T09:00:00Z"},
        ],
        "spend": {"enabled": True,
                  "used": {"amount_minor": 120, "currency": "USD", "exponent": 2},
                  "limit": {"amount_minor": 5000, "currency": "USD", "exponent": 2},
                  "percent": 2},
    }


def test_build_state_core_fields():
    now = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)
    st = us.build_state(_demo_raw(), now)
    assert st["session_pct"] == 29
    assert st["session_color"] == "green"
    assert st["weekly_pct"] == 7
    assert st["session_reset"] == "in 6h 30m"
    assert st["on_credits"] is False
    assert st["credits"]["used"] == "$1.20"
    assert st["credits"]["limit"] == "$50.00"


def test_build_state_models_sorted_desc_and_capped():
    now = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)
    st = us.build_state(_demo_raw(), now)
    names = [m["name"] for m in st["models"]]
    assert names == ["Opus", "Sonnet"]  # sorted by pct desc
    assert st["models"][0]["pct"] == 12
    assert st["models"][0]["color"] == "green"
    assert len(st["models"]) <= us.MAX_MODELS


def test_build_state_on_credits_when_weekly_maxed():
    raw = _demo_raw()
    raw["seven_day"]["utilization"] = 100
    now = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)
    st = us.build_state(raw, now)
    assert st["weekly_pct"] == 100
    assert st["on_credits"] is True


def test_build_state_legacy_model_fallback():
    raw = {
        "five_hour": {"utilization": 10, "resets_at": ""},
        "seven_day": {"utilization": 20, "resets_at": ""},
        "seven_day_sonnet": {"utilization": 33, "resets_at": ""},
        "seven_day_opus": {"utilization": 44, "resets_at": ""},
    }
    now = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)
    st = us.build_state(raw, now)
    names = [m["name"] for m in st["models"]]
    assert names == ["Opus", "Sonnet"]  # 44 before 33
    assert st["credits"] is None


def test_build_state_tolerates_empty_input():
    now = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)
    st = us.build_state({}, now)
    assert st["session_pct"] is None
    assert st["weekly_pct"] is None
    assert st["models"] == []
    assert st["on_credits"] is False
