import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import predict  # noqa: E402


def _series(start_pct, per_hour, n, step_min=5, base_ts=1_000_000):
    """Build (epoch, pct) samples climbing at per_hour, n samples step_min apart."""
    pts = []
    for i in range(n):
        ts = base_ts + i * step_min * 60
        pct = start_pct + per_hour * (i * step_min / 60.0)
        pts.append((ts, pct))
    return pts


def test_flat_when_not_climbing():
    pts = _series(40, 0.0, 12)   # dead flat
    now = datetime.fromtimestamp(pts[-1][0], tz=timezone.utc)
    out = predict.predict_metric(pts, None, now)
    assert out["verdict"] == "flat"
    assert out["eta_iso"] is None


def test_throttle_when_eta_before_reset():
    # climbing 5%/hr from 60% -> hits 100% in ~8h. Reset is 20h away -> throttle.
    pts = _series(60, 5.0, 12)
    now = datetime.fromtimestamp(pts[-1][0], tz=timezone.utc)
    reset = now + timedelta(hours=20)
    out = predict.predict_metric(pts, reset, now)
    assert out["verdict"] == "throttle"
    assert out["eta_iso"] is not None


def test_headroom_when_reset_before_eta():
    # climbing slowly 1%/hr from 50% -> hits 100% in ~50h. Reset in 6h -> headroom.
    pts = _series(50, 1.0, 12)
    now = datetime.fromtimestamp(pts[-1][0], tz=timezone.utc)
    reset = now + timedelta(hours=6)
    out = predict.predict_metric(pts, reset, now)
    assert out["verdict"] == "headroom"


def test_throttle_when_already_maxed():
    pts = _series(100, 0.0, 12)
    now = datetime.fromtimestamp(pts[-1][0], tz=timezone.utc)
    out = predict.predict_metric(pts, now + timedelta(hours=10), now)
    assert out["verdict"] == "throttle"


def test_reset_aware_ignores_old_pre_reset_samples():
    # 6 samples climbing to 90, then a reset drop to 5, then climbing again.
    pre = _series(70, 4.0, 6, base_ts=1_000_000)
    post = _series(5, 4.0, 6, base_ts=1_000_000 + 6 * 5 * 60)
    pts = pre + post
    trimmed = predict.samples_since_reset(pts)
    # Only the post-reset samples (starting at 5%) should remain.
    assert trimmed[0][1] == 5
    assert len(trimmed) == 6


def test_too_few_samples_is_flat():
    pts = _series(60, 5.0, 2)
    now = datetime.fromtimestamp(pts[-1][0], tz=timezone.utc)
    out = predict.predict_metric(pts, None, now)
    assert out["verdict"] == "flat"


def test_clustered_samples_are_flat():
    # 12 samples all within ~2 minutes (10s apart) but climbing fast -> untrustworthy.
    pts = [(1_000_000 + i * 10, 50 + i * 3) for i in range(12)]
    now = datetime.fromtimestamp(pts[-1][0], tz=timezone.utc)
    out = predict.predict_metric(pts, now + timedelta(hours=20), now)
    assert out["verdict"] == "flat"
    assert out["eta_iso"] is None


def test_daily_budget_uses_remaining_allowance_and_exact_time():
    now = datetime.now(timezone.utc)
    budget = predict.daily_budget(68, now + timedelta(days=4), now)
    assert budget["daily_points"] == 8
    assert budget["remaining_pct"] == 32


def test_last_partial_day_does_not_invent_a_larger_budget():
    now = datetime.now(timezone.utc)
    budget = predict.daily_budget(68, now + timedelta(hours=8), now)
    assert budget["daily_points"] == 32
    lines = predict.planning_lines(68, (now + timedelta(hours=8)).isoformat(), {}, now)
    assert "32.0 percentage points available over 8.0h" in lines[0]


def test_invalid_or_expired_budget_is_unavailable():
    now = datetime.now(timezone.utc)
    for used in [None, True, float('nan'), -1, 101]:
        assert predict.daily_budget(used, now + timedelta(days=1), now) is None
    assert predict.daily_budget(40, now, now) is None
    assert predict.parse_iso("2026-09-20T12:00:00") is None


def test_stale_samples_do_not_predict_a_fresh_runout():
    pts = _series(60, 5, 12)
    now = datetime.fromtimestamp(pts[-1][0], timezone.utc) + timedelta(hours=2)
    assert predict.predict_metric(pts, now + timedelta(days=1), now)["verdict"] == "stale"


def test_reset_pending_does_not_claim_headroom():
    pts = _series(60, 5, 12)
    now = datetime.fromtimestamp(pts[-1][0], timezone.utc)
    assert predict.predict_metric(pts, now - timedelta(seconds=1), now)["verdict"] == "reset_pending"


def test_unknown_reset_cannot_claim_throttling_before_reset():
    pts = _series(60, 5, 12)
    now = datetime.fromtimestamp(pts[-1][0], timezone.utc)
    assert predict.predict_metric(pts, None, now)["verdict"] == "unknown_reset"


def test_forecast_explains_both_runout_and_reset():
    now = datetime.now(timezone.utc)
    lines = predict.planning_lines(68, (now + timedelta(days=4)).isoformat(),
        {"verdict": "throttle", "eta_iso": (now + timedelta(days=2)).isoformat()}, now)
    assert "8.0 percentage points/day" in lines[0]
    assert "run out" in lines[1] and "resets" in lines[1]
    assert "Estimate" in lines[2]
