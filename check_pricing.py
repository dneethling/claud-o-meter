#!/usr/bin/env python3
"""Keep the API rate table current, automatically, without shipping a bad scrape.

Anthropic changes prices periodically. When Opus dropped from $15/$75 to $5/$25
the hard-coded table silently overstated the "value extracted" figure by more
than 2x for months, which is exactly the kind of number someone repeats out loud.

This fetches the published pricing table and writes a LOCAL override at
~/.claude-usage-pricing.json. Deliberately local: nothing is committed or
pushed, so a misparse can never propagate to anyone else's install, and the
reviewed table in claude_code_usage.py always remains the fallback.

Applying scraped numbers to real output is only safe with real gates, so an
update is written only if EVERY check passes:
  - the table parsed into a sane number of models
  - known anchor models are present
  - every rate is a positive number in a plausible range
  - output costs more than input for every model
  - cache columns match Anthropic's documented multipliers (1.25x write,
    0.1x read) - this is the strong one, because a shifted or renamed column
    breaks it immediately rather than silently mispricing everything
Any failure keeps the last known-good prices. Silence is not success, so the
result of every run is recorded and surfaced in the widget.

Run: check_pricing.py [--force] [--verbose]
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DOCS_URL = "https://platform.claude.com/docs/en/about-claude/pricing.md"
OVERRIDE = Path.home() / ".claude-usage-pricing.json"
STATUS = Path.home() / ".claude-usage-pricing-status.json"
TIMEOUT = 20

# Map a docs display name to the substring key claude_code_usage matches against
# a model id. Rule-based rather than an enumeration: a hard-coded list of every
# version goes stale the moment Anthropic ships a point release, and a stale list
# means false "new model" alerts - the exact noise this is meant to remove.
# Genuinely unknown families still return None and get reported, never guessed.
NAME_RE = re.compile(r"claude\s+(fable|mythos|opus|sonnet|haiku)\s*([0-9]+(?:\.[0-9]+)?)?")


def map_key(name: str) -> str | None:
    m = NAME_RE.match(name)
    if not m:
        return None
    fam, ver = m.group(1), m.group(2)
    if fam in ("fable", "mythos"):
        return fam
    if ver is None:
        return None
    v = float(ver)
    if fam == "opus":
        if abs(v - 4.1) < 1e-9:
            return "opus-4-1"
        if abs(v - 4.0) < 1e-9:
            return "opus-4-202"      # bare "Claude Opus 4", retired, date-stamped id
        return "opus" if v >= 4.5 else None
    if fam == "sonnet":
        return "sonnet-5" if v >= 5 else "sonnet"
    if fam == "haiku":
        if abs(v - 3.5) < 1e-9:
            return "3-5-haiku"
        return "haiku" if v >= 4 else None
    return None
# Keys must be tried most-specific-first when matching a model id.
KEY_ORDER = ["fable", "mythos", "opus-4-1", "opus-4-202", "opus",
             "sonnet-5", "sonnet", "3-5-haiku", "haiku"]
ANCHORS = ("opus", "sonnet", "haiku")     # if these vanish, something is wrong

MONEY = re.compile(r"\$?([0-9]+(?:\.[0-9]+)?)")


def log(msg: str) -> None:
    if "--verbose" in sys.argv:
        print(msg)


def notify(title: str, msg: str) -> None:
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification {json.dumps(msg)} with title {json.dumps(title)}'],
            timeout=10, capture_output=True)
    except Exception:
        pass


def write_status(ok: bool, detail: str, changed: bool = False) -> None:
    try:
        tmp = STATUS.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "ok": ok, "changed": changed, "detail": detail,
        }))
        os.replace(tmp, STATUS)
    except Exception:
        pass


MAX_BYTES = 4 * 1024 * 1024   # the pricing page is ~40 KB; this is pure headroom


def fetch(url: str) -> str:
    """Fetch the pricing document, refusing anything implausibly large.

    Read a bounded number of bytes rather than r.read(): this runs unattended on
    a weekly timer, and a redirected or misbehaving endpoint should not be able
    to pull an arbitrary amount into memory.
    """
    if not url.startswith("https://"):
        raise ValueError("refusing a non-HTTPS pricing source")
    req = urllib.request.Request(url, headers={"User-Agent": "claud-o-meter/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"pricing document larger than {MAX_BYTES} bytes")
    return data.decode("utf-8", "replace")


def _clean_name(cell: str) -> str:
    """'Claude Opus 4.1 ([deprecated](/x))' -> 'claude opus 4.1'"""
    cell = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", cell)   # unwrap md links
    cell = re.sub(r"\(.*?\)", " ", cell)                    # drop parentheticals
    return " ".join(cell.split()).strip().lower()


def _date_qualifier_active(raw_name: str, today: datetime) -> bool:
    """Handle rows like 'Sonnet 5 through August 31, 2026' / 'starting September 1, 2026'.

    Two rows can describe the same model across a price transition; only the one
    whose window covers today should be used. This is what makes the Sonnet 5
    introductory-pricing changeover happen by itself.
    """
    low = raw_name.lower()
    m = re.search(r"(through|starting)\s+([A-Z][a-z]+ \d{1,2},? \d{4})", raw_name)
    if not m:
        return True
    kind, datestr = m.group(1).lower(), m.group(2).replace(",", "")
    try:
        when = datetime.strptime(datestr, "%B %d %Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return True                       # unparseable qualifier: don't exclude
    # Compare calendar dates, not instants. "through August 31" means the whole
    # of the 31st; comparing datetimes would expire it at midnight UTC, which is
    # 02:00 on the 31st in South Africa - a day of wrong prices.
    return today.date() <= when.date() if kind == "through" else today.date() >= when.date()


def parse(md: str) -> tuple[dict, list[str], list[str]]:
    """Return ({key: rates}, [unmapped names], [conflicts]).

    A "conflict" is two models that map to the same matcher key but publish
    different rates - e.g. if Opus 4.9 ever ships at a price Opus 4.8 does not
    share. Both would collapse onto the key "opus" and one would silently win,
    which is precisely the failure that made this script necessary. We detect it
    and refuse the update rather than pick a rate at random.
    """
    today = datetime.now(timezone.utc)
    out: dict[str, dict] = {}
    seen: dict[str, tuple] = {}          # key -> rate tuple of the row we kept
    conflicts: list[str] = []
    unmapped: list[str] = []
    for line in md.splitlines():
        if not line.startswith("| Claude"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 6:
            continue
        raw_name = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", cells[0])
        name = _clean_name(cells[0])
        # columns: input | 5m cache write | 1h cache write | cache read | output
        nums = []
        for c in cells[1:6]:
            m = MONEY.search(c)
            if not m:
                break
            nums.append(float(m.group(1)))
        if len(nums) != 5:
            continue
        base_in, cw5, _cw1h, cread, out_tok = nums

        key = map_key(name)
        if key is None:
            # only report models that look like a real pricing row
            if "batch" not in name and name not in unmapped:
                unmapped.append(name)
            continue
        if not _date_qualifier_active(raw_name, today):
            log(f"  skip (window not current): {raw_name}")
            continue
        rates = {"in": base_in, "out": out_tok,
                 "cache_write": cw5, "cache_read": cread}
        sig = (base_in, out_tok, cw5, cread)
        if key in seen:
            if seen[key] != sig:
                conflicts.append(
                    f"{key}: {raw_name.strip()} is ${base_in}/${out_tok} but an "
                    f"earlier row on the same key is ${seen[key][0]}/${seen[key][1]}")
            continue                     # first row wins; conflict already noted
        seen[key] = sig
        out[key] = rates
    return out, unmapped, conflicts


def validate(table: dict) -> tuple[bool, str]:
    if len(table) < 5:
        return False, f"only parsed {len(table)} models"
    for a in ANCHORS:
        if a not in table:
            return False, f"anchor model '{a}' missing"
    for key, r in table.items():
        for field in ("in", "out", "cache_write", "cache_read"):
            v = r.get(field)
            if not isinstance(v, (int, float)) or not (0 < v <= 1000):
                return False, f"{key}.{field} out of range: {v!r}"
        if r["out"] <= r["in"]:
            return False, f"{key}: output ({r['out']}) not dearer than input ({r['in']})"
        # documented multipliers - catches a shifted or renamed column
        if abs(r["cache_write"] - r["in"] * 1.25) > max(0.02, r["in"] * 0.02):
            return False, f"{key}: cache_write {r['cache_write']} != 1.25x input {r['in']}"
        if abs(r["cache_read"] - r["in"] * 0.10) > max(0.02, r["in"] * 0.02):
            return False, f"{key}: cache_read {r['cache_read']} != 0.1x input {r['in']}"
    return True, "ok"


def current_effective() -> dict:
    if OVERRIDE.exists():
        try:
            d = json.loads(OVERRIDE.read_text())
            if isinstance(d.get("pricing"), dict):
                return d["pricing"]
        except Exception:
            pass
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import claude_code_usage as ccu
        return dict(ccu.PRICING)
    except Exception:
        return {}


def main() -> int:
    try:
        md = fetch(DOCS_URL)
    except Exception as e:
        write_status(False, f"fetch failed: {e}")
        log(f"fetch failed: {e}")
        return 1

    table, unmapped, conflicts = parse(md)
    if conflicts:
        # A family has split onto different rates. Collapsing it would silently
        # misprice one of them, so keep the known-good table and ask for a human.
        write_status(False, "rate conflict: " + "; ".join(conflicts))
        log("rate conflict, keeping existing prices:\n  " + "\n  ".join(conflicts))
        notify("Claude usage: pricing needs attention",
               conflicts[0][:200] + " - a per-version rate key is needed")
        return 1
    ok, why = validate(table)
    if not ok:
        write_status(False, f"validation failed: {why}")
        log(f"validation failed: {why}  (keeping existing prices)")
        return 1

    # order the keys so specific ones match before general ones
    ordered = {k: table[k] for k in KEY_ORDER if k in table}
    for k in table:                       # anything new but mapped, keep at end
        ordered.setdefault(k, table[k])

    before = current_effective()
    diffs = []
    for k, r in ordered.items():
        b = before.get(k)
        if not b or abs(b.get("in", -1) - r["in"]) > 1e-9 or abs(b.get("out", -1) - r["out"]) > 1e-9:
            was = f"${b['in']}/${b['out']}" if b else "(new)"
            diffs.append(f"{k}: {was} -> ${r['in']}/${r['out']}")

    if not diffs and "--force" not in sys.argv:
        write_status(True, "no change", changed=False)
        log("prices unchanged")
        if unmapped:
            log(f"unmapped models seen: {', '.join(unmapped)}")
        return 0

    payload = {
        "source": DOCS_URL,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "pricing": ordered,
        "key_order": [k for k in ordered],
        "unmapped_models": unmapped,
    }
    # Write through the descriptor mkstemp already gave us and close it, rather
    # than leaking that fd and opening the path a second time. os.replace is
    # atomic, so a concurrent reader sees either the old file or the new one.
    fd, tmp_path = tempfile.mkstemp(dir=str(OVERRIDE.parent), prefix=".pricing-")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp_path, OVERRIDE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    detail = "; ".join(diffs) if diffs else "refreshed"
    write_status(True, detail, changed=bool(diffs))
    log("updated:\n  " + "\n  ".join(diffs))
    if diffs:
        notify("Claude usage: API prices updated",
               diffs[0] + (f" (+{len(diffs)-1} more)" if len(diffs) > 1 else ""))
    if unmapped:
        # A brand new model is the one case we refuse to guess at.
        notify("Claude usage: new model in pricing table",
               f"{unmapped[0]} has no rate mapping yet - update NAME_TO_KEY")
    return 0


if __name__ == "__main__":
    sys.exit(main())
