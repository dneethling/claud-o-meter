#!/usr/bin/env python3
"""Predict when a usage limit will hit 100%, from the rolling history file.

Reads ~/.claude-usage-history ("epoch session weekly" per line), fits a linear
slope to the recent samples for each metric (reset-aware: only samples since the
last reset drop), projects time to 100%, and compares against the reset time to
decide throttle vs headroom.

Usage: predict.py <session_resets_iso> <weekly_resets_iso>
Both args optional; pass "" to skip the reset comparison for that metric.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HISTORY_PATH = Path.home() / ".claude-usage-history"
RESET_DROP = 25            # a fall of this many points between samples = a reset
MIN_SAMPLES = 3
MIN_SPAN_SECONDS = 1800    # need >= 30 min of spread; guards against clustered samples
FLAT_SLOPE_PER_HR = 0.5    # below this climb rate (pct/hour) -> treat as flat


def parse_iso(s: str | None):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def samples_since_reset(points):
    """points: list of (epoch, pct). Trim to only those since the last reset drop."""
    if not points:
        return []
    start = 0
    for i in range(1, len(points)):
        if points[i - 1][1] - points[i][1] >= RESET_DROP:
            start = i  # a reset happened between i-1 and i
    return points[start:]


def linfit(points):
    """Least-squares slope (pct per second) and the latest value."""
    n = len(points)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    den = sum((xs[i] - mx) ** 2 for i in range(n))
    slope = num / den if den else 0.0
    return slope, ys[-1]


def predict_metric(points, reset_dt, now):
    """Return {eta_iso, verdict, slope_per_hr?} for one metric's sample series."""
    pts = samples_since_reset(points)
    if len(pts) < MIN_SAMPLES:
        return {"eta_iso": None, "verdict": "flat"}
    if pts[-1][0] - pts[0][0] < MIN_SPAN_SECONDS:
        # samples too bunched in time to trust a slope (e.g. rapid manual refreshes)
        return {"eta_iso": None, "verdict": "flat"}
    slope, current = linfit(pts)          # pct per second
    slope_per_hr = slope * 3600
    if current >= 100:
        return {"eta_iso": None, "verdict": "throttle", "slope_per_hr": round(slope_per_hr, 2)}
    if slope_per_hr < FLAT_SLOPE_PER_HR:
        return {"eta_iso": None, "verdict": "flat", "slope_per_hr": round(slope_per_hr, 2)}
    secs_to_100 = (100 - current) / slope
    eta = now + timedelta(seconds=secs_to_100)
    if reset_dt and eta >= reset_dt:
        verdict = "headroom"              # the limit resets before you'd hit 100%
    else:
        verdict = "throttle"             # you'll hit 100% first (or no reset known)
    return {"eta_iso": eta.astimezone().isoformat(), "verdict": verdict,
            "slope_per_hr": round(slope_per_hr, 2)}


def load_history():
    """Return (session_points, weekly_points) as lists of (epoch, pct)."""
    session, weekly = [], []
    if not HISTORY_PATH.exists():
        return session, weekly
    for line in HISTORY_PATH.read_text().splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        try:
            ts = float(parts[0]); s = float(parts[1]); w = float(parts[2])
        except ValueError:
            continue
        session.append((ts, s))
        weekly.append((ts, w))
    return session, weekly


def main():
    session_reset = parse_iso(sys.argv[1]) if len(sys.argv) > 1 else None
    weekly_reset = parse_iso(sys.argv[2]) if len(sys.argv) > 2 else None
    now = datetime.now(timezone.utc)
    session_pts, weekly_pts = load_history()
    out = {
        "available": True,
        "session": predict_metric(session_pts, session_reset, now),
        "weekly": predict_metric(weekly_pts, weekly_reset, now),
    }
    sys.stdout.write(json.dumps(out))


if __name__ == "__main__":
    main()
