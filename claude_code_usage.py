#!/usr/bin/env python3
"""Summarise local Claude Code token usage from ~/.claude/projects/**/*.jsonl.

Claude Code writes one JSONL per session; assistant records carry
message.usage.{input_tokens,output_tokens,cache_creation_input_tokens,
cache_read_input_tokens} and message.model, with a top-level ISO timestamp.

We sum tokens by local calendar day and model, then report today / last 7 days /
last 30 days. Records are DE-DUPLICATED on (message.id, requestId) - the same key
ccusage uses - because Claude Code writes the same assistant message into several
files (resumed sessions, subagent sidechains); counting every line over-counts
roughly 3x. The dollar figure is an "API-equivalent value" estimate - on a
flat-rate Max plan you don't pay per token, so it's value extracted, not spent.

Fast on repeat runs: an incremental cache keyed on (path, mtime, size) means only
new or changed session files are re-parsed. Output goes to stdout as JSON and is
also mirrored to a warm summary file for the SwiftBar plugin to fall back on.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOME = Path.home()
PROJECTS_DIR = HOME / ".claude" / "projects"
CACHE_PATH = HOME / ".claude-usage-cc-cache.json"
SUMMARY_PATH = HOME / ".claude-usage-cc-summary.json"

# Only scan files touched within this many days — bounds the work and matches
# the widest window we report (30 days) plus a margin.
SCAN_WINDOW_DAYS = 35
CACHE_VERSION = 4

# API-equivalent pricing, USD per 1M tokens, for the "value from your
# subscription" figure. Verified 2026-07-20 against Anthropic's published rates:
# https://platform.claude.com/docs/en/about-claude/pricing
#
# Keys are matched as substrings against message.model (lowercased) IN ORDER, so
# more specific keys must come first: "opus-4-1" before "opus", "sonnet-5"
# before "sonnet". This matters because rates are not uniform across a family -
# Opus dropped from $15/$75 to $5/$25 at 4.5, and pricing "opus" as a single
# rate overstated the figure roughly threefold.
#
# Cache columns follow Anthropic's documented multipliers: 5-minute cache write
# is 1.25x base input, cache read is 0.1x base input. (A 1-hour cache write is
# 2x, which we do not model - Claude Code uses the 5-minute cache.)
PRICING = {
    "fable":      {"in": 10.00, "out": 50.00, "cache_write": 12.50, "cache_read": 1.00},
    "mythos":     {"in": 10.00, "out": 50.00, "cache_write": 12.50, "cache_read": 1.00},
    # Opus 4.1 and Opus 4 are deprecated/retired and kept the older, higher rates.
    # Their real ids are date-stamped ("claude-opus-4-1-20250805",
    # "claude-opus-4-20250514"), so match the date run - "opus-4-202" cannot
    # collide with "opus-4-5" through "opus-4-8", which are on the newer rate.
    "opus-4-1":   {"in": 15.00, "out": 75.00, "cache_write": 18.75, "cache_read": 1.50},
    "opus-4-202": {"in": 15.00, "out": 75.00, "cache_write": 18.75, "cache_read": 1.50},
    "opus":       {"in":  5.00, "out": 25.00, "cache_write":  6.25, "cache_read": 0.50},
    # Sonnet 5 introductory pricing runs to 31 Aug 2026, after which it becomes
    # $3/$15 like the other Sonnets - move this line then.
    "sonnet-5":   {"in":  2.00, "out": 10.00, "cache_write":  2.50, "cache_read": 0.20},
    "sonnet":     {"in":  3.00, "out": 15.00, "cache_write":  3.75, "cache_read": 0.30},
    # Haiku 3.5 is retired on a lower rate than Haiku 4.5; its id leads with the
    # version ("claude-3-5-haiku-20241022") so it must be matched before "haiku".
    "3-5-haiku":  {"in":  0.80, "out":  4.00, "cache_write":  1.00, "cache_read": 0.08},
    "haiku":      {"in":  1.00, "out":  5.00, "cache_write":  1.25, "cache_read": 0.10},
}
# check_pricing.py refreshes rates from Anthropic's published table into a local
# override. Prefer it when present and structurally sound, so a price change is
# picked up without a code edit; fall back to the reviewed table above whenever
# the override is missing, malformed, or fails the same sanity checks the
# updater applies. A stale-but-correct table beats a fresh-but-broken one.
_OVERRIDE_PATH = Path.home() / ".claude-usage-pricing.json"


def _load_override() -> dict | None:
    try:
        d = json.loads(_OVERRIDE_PATH.read_text())
    except Exception:
        return None
    table = d.get("pricing")
    if not isinstance(table, dict) or len(table) < 5:
        return None
    for r in table.values():
        if not isinstance(r, dict):
            return None
        for f in ("in", "out", "cache_write", "cache_read"):
            v = r.get(f)
            if not isinstance(v, (int, float)) or not (0 < v <= 1000):
                return None
        if r["out"] <= r["in"]:
            return None
    order = d.get("key_order")
    if isinstance(order, list) and all(k in table for k in order):
        return {k: table[k] for k in order}     # matching is order-sensitive
    return table


_OV = _load_override()
if _OV:
    PRICING = _OV
DEFAULT_PRICE = PRICING.get("sonnet", {"in": 3.0, "out": 15.0,
                                       "cache_write": 3.75, "cache_read": 0.30})


def price_for(model: str) -> dict:
    """Rate table for a model string. Order-sensitive: see PRICING."""
    m = (model or "").lower()
    for key, tbl in PRICING.items():
        if key in m:
            return tbl
    return DEFAULT_PRICE


def local_date(ts: str) -> str | None:
    """ISO timestamp -> local YYYY-MM-DD, or None if unparseable."""
    if not ts:
        return None
    try:
        s = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d")
    except Exception:
        return None


def parse_file(path: Path) -> dict:
    """Return {dedup_key: [day, model, in, out, cr, cc]} for one session file.

    dedup_key is "message.id\x1frequestId" when both are present, else a
    per-occurrence synthetic key "path\x1flineno" so records that cannot be
    de-duplicated are still counted once each. Claude Code writes the SAME
    assistant message into several files (resumed sessions, subagent
    sidechains), so counting every line over-counts ~3x; de-duplicating on
    (id, requestId) - the same key ccusage uses - fixes it. De-dup is applied
    globally at aggregation time (across all files), not per file.
    """
    records: dict[str, list] = {}
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for lineno, line in enumerate(f):
                if '"usage"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                msg = o.get("message")
                if not isinstance(msg, dict):
                    continue
                u = msg.get("usage")
                if not isinstance(u, dict):
                    continue
                day = local_date(o.get("timestamp", ""))
                if not day:
                    continue
                model = msg.get("model", "unknown")
                mid = msg.get("id")
                rid = o.get("requestId")
                if mid and rid:
                    key = f"{mid}\x1f{rid}"
                else:
                    key = f"{path}\x1f{lineno}"   # cannot dedupe -> unique per occurrence
                records[key] = [
                    day, model,
                    int(u.get("input_tokens", 0) or 0),
                    int(u.get("output_tokens", 0) or 0),
                    int(u.get("cache_read_input_tokens", 0) or 0),
                    int(u.get("cache_creation_input_tokens", 0) or 0),
                ]
    except Exception:
        pass
    return records


def load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            c = json.loads(CACHE_PATH.read_text())
            if c.get("v") == CACHE_VERSION:
                return c
        except Exception:
            pass
    return {"v": CACHE_VERSION, "files": {}}


def save_cache(cache: dict) -> None:
    try:
        tmp = CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache))
        os.replace(tmp, CACHE_PATH)
    except Exception:
        pass


def build_summary() -> dict:
    cache = load_cache()
    files = cache["files"]
    cutoff = time.time() - SCAN_WINDOW_DAYS * 86400

    seen = set()
    if PROJECTS_DIR.is_dir():
        for path in PROJECTS_DIR.rglob("*.jsonl"):
            try:
                st = path.stat()
            except Exception:
                continue
            if st.st_mtime < cutoff:
                continue
            key = str(path)
            seen.add(key)
            cached = files.get(key)
            if cached and cached.get("mtime") == st.st_mtime and cached.get("size") == st.st_size:
                continue  # unchanged — reuse cached buckets
            files[key] = {"mtime": st.st_mtime, "size": st.st_size, "records": parse_file(path)}

    # Drop cache entries for files that no longer exist / aged out
    for key in list(files.keys()):
        if key not in seen:
            del files[key]

    save_cache(cache)

    # Aggregate across every file's buckets into day/model totals
    now = datetime.now().astimezone()
    today = now.strftime("%Y-%m-%d")
    d7 = (now - timedelta(days=6)).strftime("%Y-%m-%d")     # this-week start (7 days incl today)
    d13 = (now - timedelta(days=13)).strftime("%Y-%m-%d")   # prev-week start
    d30 = (now - timedelta(days=29)).strftime("%Y-%m-%d")

    windows = {"today": {}, "week": {}, "prev_week": {}, "month": {}}  # each: model -> {in,out,cr,cc}
    daily_tokens: dict[str, int] = {}   # day -> total tokens, for the 7-day sparkline

    def add(win: dict, model: str, b: dict):
        t = win.setdefault(model, {"in": 0, "out": 0, "cr": 0, "cc": 0})
        for k in ("in", "out", "cr", "cc"):
            t[k] += b[k]

    # Merge every file's records into one global dict keyed by dedup_key, so a
    # message logged in several files is counted ONCE (true duplicates have
    # identical values; last write wins harmlessly).
    global_records: dict[str, list] = {}
    for fentry in files.values():
        global_records.update(fentry.get("records", {}))

    for rec in global_records.values():
        day, model, tin, tout, tcr, tcc = rec
        b = {"in": tin, "out": tout, "cr": tcr, "cc": tcc}
        day_total = tin + tout + tcr + tcc
        if day >= d7:
            daily_tokens[day] = daily_tokens.get(day, 0) + day_total
        if day == today:
            add(windows["today"], model, b)
        if day >= d7:
            add(windows["week"], model, b)
        if d13 <= day < d7:
            add(windows["prev_week"], model, b)
        if day >= d30:
            add(windows["month"], model, b)

    def short_model(m: str) -> str:
        ml = m.lower()
        if "opus" in ml: return "Opus"
        if "sonnet" in ml: return "Sonnet"
        if "haiku" in ml: return "Haiku"
        if "fable" in ml: return "Fable"
        return m

    def totals(win: dict) -> dict:
        tin = tout = tcr = tcc = 0
        cost = 0.0
        by_model: dict[str, int] = {}
        for model, b in win.items():
            tin += b["in"]; tout += b["out"]; tcr += b["cr"]; tcc += b["cc"]
            p = price_for(model)
            cost += (b["in"] / 1e6) * p["in"]
            cost += (b["out"] / 1e6) * p["out"]
            cost += (b["cc"] / 1e6) * p["cache_write"]
            cost += (b["cr"] / 1e6) * p["cache_read"]
            sm = short_model(model)
            by_model[sm] = by_model.get(sm, 0) + b["in"] + b["out"] + b["cr"] + b["cc"]
        total_tokens = tin + tout + tcr + tcc
        return {
            "input": tin, "output": tout,
            "cache_read": tcr, "cache_write": tcc,
            "total_tokens": total_tokens,
            "est_cost_usd": round(cost, 2),
            "by_model": by_model,
        }

    # Last 7 days as an ordered array oldest -> newest, for the sparkline
    daily = []
    for i in range(6, -1, -1):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        daily.append(daily_tokens.get(d, 0))

    summary = {
        "generated_at": now.isoformat(),
        "today": totals(windows["today"]),
        "week": totals(windows["week"]),
        "prev_week": totals(windows["prev_week"]),
        "month": totals(windows["month"]),
        "daily": daily,
    }
    return summary


if __name__ == "__main__":
    try:
        summary = build_summary()
    except Exception as e:
        sys.stderr.write(f"cc-usage error: {e}\n")
        # Fall back to last good summary if present
        if SUMMARY_PATH.exists():
            sys.stdout.write(SUMMARY_PATH.read_text())
            sys.exit(0)
        sys.exit(1)

    out = json.dumps(summary)
    try:
        tmp = SUMMARY_PATH.with_suffix(".tmp")
        tmp.write_text(out)
        os.replace(tmp, SUMMARY_PATH)
    except Exception:
        pass
    sys.stdout.write(out)
