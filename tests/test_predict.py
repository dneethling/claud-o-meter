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
