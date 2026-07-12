#!/usr/bin/env python3
"""Summarise local Codex CLI token usage from ~/.codex/state_5.sqlite.

Codex records one row per conversation thread in the `threads` table with a
cumulative `tokens_used` count, an `updated_at` epoch, and `model_provider`.
We bucket each thread's tokens by the local date it was last touched and report
today / last 7 days / last 30 days, plus thread counts.

Caveat, stated honestly: `tokens_used` is per-thread cumulative and we attribute
it to the day the thread was last active. A thread worked across several days
lands entirely on its final day, so daily figures are an activity proxy, not a
to-the-token daily ledger. All-time and weekly/monthly totals are solid.

Read-only, single indexed query — fast enough to call on every plugin tick.
Output is JSON to stdout, mirrored to a warm summary file for fallback.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

HOME = Path.home()
DB_PATH = HOME / ".codex" / "state_5.sqlite"
SUMMARY_PATH = HOME / ".claude-usage-codex-summary.json"


def local_day(epoch: int) -> str | None:
    if not epoch:
        return None
    # Handle seconds vs milliseconds defensively
    if epoch > 1_000_000_000_000:
        epoch = epoch / 1000
    try:
        return datetime.fromtimestamp(epoch).astimezone().strftime("%Y-%m-%d")
    except Exception:
        return None


def build_summary() -> dict:
    if not DB_PATH.exists():
        return {"available": False, "reason": "no ~/.codex/state_5.sqlite"}

    # Open read-only so we never disturb Codex's own writes
    uri = f"file:{DB_PATH}?mode=ro&immutable=1"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=2)
    except Exception as e:
        return {"available": False, "reason": f"open failed: {e}"}

    try:
        rows = con.execute(
            "SELECT tokens_used, updated_at FROM threads WHERE tokens_used > 0"
        ).fetchall()
    except Exception as e:
        con.close()
        return {"available": False, "reason": f"query failed: {e}"}
    con.close()

    now = datetime.now().astimezone()
    today = now.strftime("%Y-%m-%d")
    d7 = (now - timedelta(days=6)).strftime("%Y-%m-%d")
    d13 = (now - timedelta(days=13)).strftime("%Y-%m-%d")
    d30 = (now - timedelta(days=29)).strftime("%Y-%m-%d")

    def blank():
        return {"tokens": 0, "threads": 0}

    today_b, week_b, prev_week_b, month_b, all_b = blank(), blank(), blank(), blank(), blank()
    daily_tokens: dict[str, int] = {}   # day -> tokens, for the 7-day sparkline

    for tokens_used, updated_at in rows:
        tokens_used = int(tokens_used or 0)
        day = local_day(int(updated_at or 0))
        all_b["tokens"] += tokens_used
        all_b["threads"] += 1
        if not day:
            continue
        if day >= d7:
            daily_tokens[day] = daily_tokens.get(day, 0) + tokens_used
        if day == today:
            today_b["tokens"] += tokens_used; today_b["threads"] += 1
        if day >= d7:
            week_b["tokens"] += tokens_used; week_b["threads"] += 1
        if d13 <= day < d7:
            prev_week_b["tokens"] += tokens_used; prev_week_b["threads"] += 1
        if day >= d30:
            month_b["tokens"] += tokens_used; month_b["threads"] += 1

    daily = []
    for i in range(6, -1, -1):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        daily.append(daily_tokens.get(d, 0))

    return {
        "available": True,
        "generated_at": now.isoformat(),
        "today": today_b,
        "week": week_b,
        "prev_week": prev_week_b,
        "month": month_b,
        "all_time": all_b,
        "daily": daily,
    }


if __name__ == "__main__":
    try:
        summary = build_summary()
    except Exception as e:
        sys.stderr.write(f"codex-usage error: {e}\n")
        if SUMMARY_PATH.exists():
            sys.stdout.write(SUMMARY_PATH.read_text())
            sys.exit(0)
        sys.exit(1)

    out = json.dumps(summary)
    if summary.get("available"):
        try:
            tmp = SUMMARY_PATH.with_suffix(".tmp")
            tmp.write_text(out)
            os.replace(tmp, SUMMARY_PATH)
        except Exception:
            pass
    sys.stdout.write(out)
