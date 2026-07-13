#!/usr/bin/env python3
"""Export the last 7 days of usage to ~/Downloads as CSV or JSON, then reveal it.

Merges the Claude Code and Codex daily token arrays (from the summary files) with
the per-day last-known session/weekly percentages (from the history file) into a
tidy daily table. Usage: export_usage.py {csv|json}  (default csv).
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

HOME = Path.home()
CC_SUMMARY = HOME / ".claude-usage-cc-summary.json"
CODEX_SUMMARY = HOME / ".claude-usage-codex-summary.json"
HISTORY = HOME / ".claude-usage-history"
DOWNLOADS = HOME / "Downloads"


def load(p):
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def last_dates(n=7):
    now = datetime.now().astimezone()
    return [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n - 1, -1, -1)]


def history_by_day():
    """day -> (session_pct, weekly_pct) from the latest sample on that day."""
    out = {}
    if not HISTORY.exists():
        return out
    for line in HISTORY.read_text().splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        try:
            ts = float(parts[0]); s = int(float(parts[1])); w = int(float(parts[2]))
        except ValueError:
            continue
        day = datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d")
        out[day] = (s, w)   # later lines overwrite -> last sample of the day wins
    return out


def build_rows():
    dates = last_dates(7)
    cc = load(CC_SUMMARY) or {}
    codex = load(CODEX_SUMMARY) or {}
    cc_daily = cc.get("daily", [0] * 7)
    codex_daily = codex.get("daily", [0] * 7)
    hist = history_by_day()

    rows = []
    for i, d in enumerate(dates):
        s, w = hist.get(d, ("", ""))
        rows.append({
            "date": d,
            "cc_tokens": cc_daily[i] if i < len(cc_daily) else 0,
            "codex_tokens": codex_daily[i] if i < len(codex_daily) else 0,
            "session_pct": s,
            "weekly_pct": w,
        })
    return rows


def main():
    fmt = sys.argv[1].lower() if len(sys.argv) > 1 else "csv"
    if fmt not in ("csv", "json"):
        fmt = "csv"
    rows = build_rows()
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d")
    path = DOWNLOADS / f"claude-usage-{stamp}.{fmt}"

    if fmt == "csv":
        with path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["date", "cc_tokens", "codex_tokens",
                                                   "session_pct", "weekly_pct"])
            writer.writeheader()
            writer.writerows(rows)
    else:
        payload = {"generated_at": datetime.now().astimezone().isoformat(), "days": rows}
        path.write_text(json.dumps(payload, indent=2))

    # Reveal in Finder (best effort).
    try:
        subprocess.run(["/usr/bin/open", "-R", str(path)], check=False)
    except Exception:
        pass
    sys.stdout.write(str(path))


if __name__ == "__main__":
    main()
