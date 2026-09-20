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
import math
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
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo is not None else None
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
    # Fit recent, ordered samples only. An old reading is not today's pace;
    # duplicate refreshes at one timestamp must not overweight that moment.
    cutoff = now.timestamp() - 24 * 3600
    by_time = {float(ts): float(pct) for ts, pct in points
               if math.isfinite(ts) and math.isfinite(pct)
               and cutoff <= ts <= now.timestamp() and 0 <= pct <= 100}
    pts = samples_since_reset(sorted(by_time.items()))
    if reset_dt and reset_dt <= now:
        return {"eta_iso": None, "verdict": "reset_pending"}
    if not pts or now.timestamp() - pts[-1][0] > 15 * 60:
        return {"eta_iso": None, "verdict": "stale"}
    if len(pts) < MIN_SAMPLES:
        return {"eta_iso": None, "verdict": "flat", "reason": "insufficient_history"}
    if pts[-1][0] - pts[0][0] < MIN_SPAN_SECONDS:
        # samples too bunched in time to trust a slope (e.g. rapid manual refreshes)
        return {"eta_iso": None, "verdict": "flat", "reason": "insufficient_history"}
    slope, current = linfit(pts)          # pct per second
    slope_per_hr = slope * 3600
    if current >= 100:
        return {"eta_iso": None, "verdict": "throttle", "slope_per_hr": round(slope_per_hr, 2)}
    if slope_per_hr < FLAT_SLOPE_PER_HR:
        return {"eta_iso": None, "verdict": "flat", "reason": "steady", "slope_per_hr": round(slope_per_hr, 2)}
    secs_to_100 = (100 - current) / slope
    eta = now + timedelta(seconds=secs_to_100)
    if not reset_dt:
        verdict = "unknown_reset"
    elif eta >= reset_dt:
        verdict = "headroom"              # the limit resets before you'd hit 100%
    else:
        verdict = "throttle"             # you'll hit 100% first
    return {"eta_iso": eta.astimezone().isoformat(), "verdict": verdict,
            "slope_per_hr": round(slope_per_hr, 2)}


def daily_budget(used_pct, reset_dt, now):
    """Even-spend guide in percentage points, never a vendor token allowance."""
    if isinstance(used_pct, bool) or not isinstance(used_pct, (int, float)):
        return None
    if not math.isfinite(used_pct) or not 0 <= used_pct <= 100 or not reset_dt:
        return None
    hours = (reset_dt - now).total_seconds() / 3600
    if hours <= 0:
        return None
    remaining = max(0.0, 100 - used_pct)
    return {"remaining_pct": remaining, "hours_left": hours,
            "daily_points": remaining / max(1, hours / 24)}


def planning_lines(used_pct, reset_iso, forecast, now=None):
    """Plain-language weekly plan. Called only for a fresh API reading."""
    now = now or datetime.now(timezone.utc)
    reset = parse_iso(reset_iso)
    budget = daily_budget(used_pct, reset, now)
    if reset and reset <= now:
        return ["Reset is due · waiting for a fresh allowance"]
    if not budget:
        return ["Weekly plan unavailable · waiting for usage and a reset time"]
    left, hours = budget["remaining_pct"], budget["hours_left"]
    if left <= 0:
        return ["Weekly allowance used up · waiting for reset"]
    if hours >= 24:
        lines = [f"Budget ≈{budget['daily_points']:.1f} percentage points/day · {left:.1f}% left for {hours / 24:.1f} days"]
    else:
        lines = [f"Until reset: {left:.1f} percentage points available over {hours:.1f}h"]
    forecast = forecast or {}
    verdict = forecast.get("verdict")
    eta = parse_iso(forecast.get("eta_iso"))
    clock = lambda dt: dt.astimezone().strftime("%a %d %b, %H:%M")
    if verdict == "throttle" and eta and eta > now and eta < reset:
        lines.append(f"At this pace, run out {clock(eta)} · resets {clock(reset)}")
    elif verdict == "headroom":
        lines.append("At this pace, your allowance should last until reset")
    elif verdict == "flat" and forecast.get("reason") == "steady":
        lines.append("Recent use is steady · not enough growth to estimate a run-out time")
    else:
        lines.append("Learning your pace · needs 30+ minutes of recent readings")
    lines.append("Estimate from recent use; daily budget assumes even spending")
    return lines


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
