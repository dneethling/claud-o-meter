#!/usr/bin/env python3
"""Render the full SwiftBar menu from a fetched usage payload.

The plugin used to be ~940 lines of bash: 56 jq calls, 15 python spawns, three
copy-pasted image-cache helpers, and all the formatting. That surface broke the
SwiftBar row protocol more than once and could not be tested without a Mac and a
live session. This module owns everything downstream of the fetch - parse,
formatting, in-process image rendering, prediction, provider status, alerts, and
printing every menu line - so the bash plugin is now a thin fetch-and-hand-off
shim and the whole menu is testable from a fixture JSON on stdin.

Contract: the shim pipes the usage JSON (fresh, or last-good while offline) on
stdin and passes context via argv/env. We print the SwiftBar menu to stdout.
Our own mutating side effects - notifications, history, last-seen, RAW cache
persistence, and the background update/pricing spawns - are skipped under
--render-only, so a golden diff against the old bash output is possible. The
read-through data helpers (status, Claude Code, Codex) still run and refresh
their summary caches as in production: render-only suppresses what WE write,
not what they do (their output is exactly what we render).

Inputs (env, set by the shim):
  CLAUDE_WIDGET_DIR   repo root
  CLAUDE_APPEARANCE   "dark" | "light"
Argv:
  --offline           render last-good data with a paused marker
  --render-only       skip our own side effects (for tests / golden diff)
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

HOME = Path.home()
WIDGET_DIR = Path(os.environ.get("CLAUDE_WIDGET_DIR", Path(__file__).resolve().parent))
PYTHON = str(WIDGET_DIR / ".venv/bin/python")
sys.path.insert(0, str(WIDGET_DIR))

OFFLINE = "--offline" in sys.argv[1:]
RENDER_ONLY = "--render-only" in sys.argv[1:]

# --- paths (mirror the bash) -------------------------------------------------
CONFIG = HOME / ".claude-usage-widget.conf"
RAW = Path("/tmp/claude-usage-raw.json")
REJECTED = Path("/tmp/claude-usage-rejected.json")
ERR_LOG = "/tmp/claude-usage-err.log"
CC_SUMMARY = HOME / ".claude-usage-cc-summary.json"
CODEX_SUMMARY = HOME / ".claude-usage-codex-summary.json"
HISTORY_FILE = HOME / ".claude-usage-history"
MUTE_FILE = HOME / ".claude-usage-mute-until"
ALERT_STATE = "/tmp/claude-usage-alert-state"
LASTSEEN_FILE = HOME / ".claude-usage-lastseen"
UPDATE_STATUS = HOME / ".claude-usage-update-status"
UPDATE_RESULT = HOME / ".claude-usage-update-result.json"
PRICING_STATUS = HOME / ".claude-usage-pricing-status.json"
CC_USAGE = str(WIDGET_DIR / "claude_code_usage.py")
CODEX_USAGE = str(WIDGET_DIR / "codex_usage.py")
PREDICT = str(WIDGET_DIR / "predict.py")
STATUS_CHECK = str(WIDGET_DIR / "status_check.py")
CHECK_UPDATE = str(WIDGET_DIR / "check_update.sh")
UPDATE_SCRIPT = str(WIDGET_DIR / "update.sh")
CHECK_PRICING = str(WIDGET_DIR / "check_pricing.py")
COPY_SUMMARY = str(WIDGET_DIR / "copy_summary.py")
EXPORT = str(WIDGET_DIR / "export_usage.py")
REFRESHER = str(WIDGET_DIR / "refresh_cookie.py")
REPO_URL = "https://github.com/dneethling/claud-o-meter"

WARN_PCT, CRIT_PCT = 60, 85
RESET_DROP = 30
HISTORY_CAP = 2016
UPDATE_CHECK_INTERVAL = 21600

DARK = os.environ.get("CLAUDE_APPEARANCE", "light") == "dark"
LBL = "#f2f2f7" if DARK else "#1c1c1e"
DIM = "#8E8E93"  # secondary label, reads as muted on both light and dark
DARKJSON = "true" if DARK else "false"
SEC_CC = "#8F8CFF" if DARK else "#5E5CE6"
SEC_CX = "#3ED9D3" if DARK else "#0E9F9A"

_out: list[str] = []
def emit(line: str) -> None:
    _out.append(line)
def sep() -> None:
    _out.append("---")


# --- config knobs ------------------------------------------------------------
def _conf(key: str, default: str) -> str:
    try:
        for line in CONFIG.read_text().splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return default

MENUBAR_MODE = _conf("MENUBAR_MODE", "claude")
DETAIL_LEVEL = _conf("DETAIL_LEVEL", "full")
THEME = _conf("THEME", "semantic")
GRAPHICS = _conf("GRAPHICS", "0")  # native-only by default: instant menu, no webview/image hover bug
STATUS_ALERT = _conf("STATUS_ALERT", "major")


# --- formatting helpers (ports of lib/format.sh) -----------------------------
def color_for_pct(pct) -> str:
    if THEME == "minimal":
        return ""
    low, warn, crit = ("#0072B2", "#E69F00", "#D55E00") if THEME == "colorblind" \
        else ("#34C759", "#FF9500", "#FF3B30")
    if pct is None or pct == "":
        return low
    p = int(float(pct))
    if p >= CRIT_PCT:
        return crit
    if p >= WARN_PCT:
        return warn
    return low

def colorkey(clr: str) -> str:
    return f"color={clr}" if clr else ""

def percentage(value):
    """An absent or malformed usage value is unknown, never zero."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) and 0 <= number <= 1_000_000 else None
    except (TypeError, ValueError):
        return None


def rnd(x) -> str:
    value = percentage(x)
    return "" if value is None else f"{value:.0f}"

def humanize_tokens(n) -> str:
    if n is None or n == "" or n == "null":
        return "—"
    n = float(n)
    if n >= 1e9:
        return f"{n/1e9:.1f}B"
    if n >= 1e6:
        return f"{n/1e6:.0f}M"
    if n >= 1e3:
        return f"{n/1e3:.0f}k"
    return f"{int(n)}"

def humanize_usd(n) -> str:
    if n is None or n == "" or n == "null":
        return "—"
    n = float(n)
    return f"${n/1000:.1f}k" if n >= 1000 else f"${n:.0f}"

def format_money(amt, cur, exp) -> str:
    if amt is None or amt == "" or amt == "null":
        return "—"
    exp = int(exp or 0)
    val = float(amt) / (10 ** exp)
    s = f"{val:.{exp}f}"
    sym = {"USD": "$", "GBP": "£", "EUR": "€", "ZAR": "R", "JPY": "¥"}.get(cur)
    return f"{sym}{s}" if sym else f"{s} {cur}"

_GLYPHS = "▁▂▃▄▅▆▇█"
def sparkline(nums) -> str:
    vals = [float(x) for x in str(nums).split()] if nums else []
    if not vals:
        return ""
    mn, mx = min(vals), max(vals)
    out = ""
    for v in vals:
        lvl = 0 if mx == mn else int(((v - mn) / (mx - mn)) * 7 + 0.5)
        out += _GLYPHS[lvl]
    return out

def progress_bar(pct) -> str:
    if pct is None or pct == "":
        return ""
    p = int(float(pct))
    filled = max(0, min(14, p * 14 // 100))
    return "█" * filled + "░" * (14 - filled)


def _fmt_dt(dt: datetime) -> str:
    now = datetime.now(dt.tzinfo)
    total_min = int((dt - now).total_seconds() // 60)
    if total_min < 0:
        return "now"
    if total_min < 60:
        return f"in {total_min}m"
    if total_min < 24 * 60:
        h, m = divmod(total_min, 60)
        return f"in {h}h {m}m"
    return dt.strftime("%a %-d %b, %-I:%M%p").lower()

def fmt_reset_iso(iso: str) -> str:
    if not iso:
        return ""
    s = iso.replace("Z", "+00:00")
    if "." in s:
        head, tail = s.split(".", 1)
        tz = ""
        for c in ("+", "-"):
            if c in tail:
                tz = c + tail.split(c, 1)[1]
                break
        s = head + tz
    try:
        return _fmt_dt(datetime.fromisoformat(s).astimezone())
    except Exception:
        return ""

def fmt_reset_epoch(epoch) -> str:
    try:
        return _fmt_dt(datetime.fromtimestamp(int(float(epoch))).astimezone())
    except Exception:
        return ""


# --- images (in-process; no more per-image subprocess or disk cache) ---------
_render_ok = True
try:
    import render_assets as _ra
except Exception:
    _render_ok = False

def _b64(png: bytes) -> str:
    import base64
    return base64.b64encode(png).decode("ascii")

# SwiftBar only shows a dropdown image at the size you ask for: without width=
# /height= params it falls back to a tiny natural size (see SwiftBar's
# MenuLineParameters.resizedImageIfRequested). So the *_img helpers return the
# full "image=.. width=.. height=.." token, rendered at 2x for a crisp retina fit.
METER_PT = (120, 13)
SPARK_PT = (200, 34)

def meter_img(pint: str, clr: str) -> str:
    if GRAPHICS != "1" or not _render_ok or not clr:
        return ""
    try:
        w, h = METER_PT
        return f"image={_b64(_ra.meter(int(pint) / 100.0, clr, DARK, w, h))} width={w} height={h}"
    except Exception:
        return ""

def spark_img(vals: str, clr: str) -> str:
    if GRAPHICS != "1" or not _render_ok or not vals.strip():
        return ""
    try:
        nums = []
        for tok in vals.split():
            try:
                v = float(tok)
            except ValueError:
                v = 0.0
            if v != v or v in (float("inf"), float("-inf")):
                v = 0.0
            # Match the old bash path, which passed each value through awk's
            # "%.6g" (6 significant figures) before rendering. This keeps the
            # output byte-identical to the previous plugin; it is visually
            # indistinguishable from full precision.
            nums.append(float(f"{v:.6g}"))
        if not nums:
            return ""
        w, h = SPARK_PT
        return f"image={_b64(_ra.spark(nums, clr, DARK, w, h))} width={w} height={h}"
    except Exception:
        return ""

def ring_img(pint: str, clr: str) -> str:
    if GRAPHICS != "1" or not _render_ok or not clr:
        return ""
    try:
        return _b64(_ra.ring(int(pint) / 100.0, clr, DARK, 16, 3.4))
    except Exception:
        return ""


def print_metric(label: str, pct, reset: str) -> None:
    pint = rnd(pct)
    if not pint:
        return
    clr = color_for_pct(pint)
    img = meter_img(pint, clr)
    if img:
        info = f"{_san(label)} · {pint}%"
        if reset:
            info = f"{info} · resets {reset}"
        emit(f"{info} | size=12 color={LBL} {img}")
    else:
        # Native row: fixed-width unicode meter + right-aligned % first, so the
        # bars and numbers line up into a clean column and the label trails.
        emit(f"{progress_bar(pint)}  {int(pint):>3}%  {_san(label)} | font=Menlo size=13 {colorkey(clr)}")
        if reset:
            emit(f"  resets {reset} | size=11 color={DIM}")


def run_json(args: list[str], timeout: float, summary_path: Path | None = None):
    """Run a helper, parse its JSON stdout, fall back to a warm summary file."""
    try:
        r = subprocess.run([PYTHON] + args, capture_output=True, text=True, timeout=timeout)
        out = r.stdout.strip()
        if out:
            return json.loads(out)
    except Exception:
        pass
    if summary_path and summary_path.exists():
        try:
            return json.loads(summary_path.read_text())
        except Exception:
            pass
    return None


# --- alerts (side effects) ---------------------------------------------------
def _muted() -> bool:
    try:
        until = int(MUTE_FILE.read_text().strip())
        return datetime.now().timestamp() < until
    except Exception:
        return False

def _alerted(key: str) -> bool:
    try:
        return key in Path(ALERT_STATE).read_text().splitlines()
    except Exception:
        return False

def notify(title: str, msg: str, key: str) -> None:
    if RENDER_ONLY or _muted() or _alerted(key):
        return
    try:
        subprocess.run(["osascript", "-e",
                        f'display notification "{msg}" with title "{title}" sound name "Glass"'],
                       capture_output=True, timeout=10)
        with open(ALERT_STATE, "a") as f:
            f.write(key + "\n")
    except Exception:
        pass

def clear_alert(key: str) -> None:
    if RENDER_ONLY:
        return
    try:
        p = Path(ALERT_STATE)
        if not p.exists():
            return
        kept = [ln for ln in p.read_text().splitlines() if ln != key]
        # Atomic rewrite: a crash mid-write must not leave the state truncated.
        tmp = ALERT_STATE + ".tmp"
        with open(tmp, "w") as f:
            f.write("".join(k + "\n" for k in kept))
        os.replace(tmp, ALERT_STATE)
    except Exception:
        pass


def error_tile(icon_title: str, lines: list[str]) -> None:
    """A standalone failure menu (setup/shape-broken)."""
    for ln in ([icon_title] + lines):
        emit(ln)
    flush()
    sys.exit(0)

def flush() -> None:
    sys.stdout.write("\n".join(_out) + "\n")


def main() -> int:
    raw = sys.stdin.read()
    try:
        d = json.loads(raw)
    except Exception:
        d = {}

    if not isinstance(d, dict):
        d = {}

    # --- parse ---------------------------------------------------------------
    five = d.get("five_hour") if isinstance(d.get("five_hour"), dict) else {}
    seven = d.get("seven_day") if isinstance(d.get("seven_day"), dict) else {}
    session = percentage(five.get("utilization"))
    session_reset = five.get("resets_at") or ""
    week = percentage(seven.get("utilization"))
    week_reset = seven.get("resets_at") or ""

    scoped = []  # (name, percent, reset_iso)
    for lim in (d.get("limits") or []):
        name = (((lim or {}).get("scope") or {}).get("model") or {}).get("display_name")
        if name is not None:
            scoped.append((name, lim.get("percent"), lim.get("resets_at") or ""))
    if not scoped:
        for key, disp in (("seven_day_sonnet", "Sonnet"), ("seven_day_opus", "Opus")):
            blk = d.get(key) or {}
            if blk.get("utilization") is not None:
                scoped.append((disp, blk.get("utilization"), blk.get("resets_at") or ""))

    extra_enabled = str((d.get("extra_usage") or {}).get("is_enabled", "false")).lower()
    spend = d.get("spend") or {}
    spend_enabled = str(spend.get("enabled", False)).lower()
    spend_used = (spend.get("used") or {}).get("amount_minor")
    spend_limit = (spend.get("limit") or {}).get("amount_minor")
    spend_cur = (spend.get("used") or {}).get("currency")
    spend_exp = (spend.get("used") or {}).get("exponent", 2)
    spend_pct = spend.get("percent")

    s_i = rnd(session)
    w_i = rnd(week)
    spend_i = rnd(spend_pct)

    # Credit mode when weekly is exhausted and credits are paying, independent
    # of whether session utilization is present (matches the old bash: the title
    # already falls back to "?%"). Gating on s_i here would hide credit spend
    # whenever the API omits five_hour.utilization.
    on_credits = (w_i and w_i.isdigit() and int(w_i) >= 100
                  and spend_enabled == "true" and spend_used not in (None, "null"))
    on_credits = 1 if on_credits else 0

    spend_used_str = format_money(spend_used, spend_cur, spend_exp)
    spend_limit_str = format_money(spend_limit, spend_cur, spend_exp)

    shape_broken = (s_i == "" and w_i == "")

    # Only recognized readings replace the last-good cache. A changed response
    # shape must not destroy the data used during the next network outage.
    if not shape_broken and not RENDER_ONLY and not OFFLINE:
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=RAW.parent, delete=False) as cache:
                cache.write(raw)
                cache_path = Path(cache.name)
            try:
                os.replace(cache_path, RAW)
            finally:
                cache_path.unlink(missing_ok=True)
        except OSError:
            pass

    # An unrecognized response is saved separately, so the shape-error tile can
    # show the ACTUAL rejected payload (RAW still holds the last-good reading).
    if shape_broken and not RENDER_ONLY and raw.strip():
        try:
            # Atomic replace, so a pre-planted /tmp symlink can't redirect the
            # write through to an unrelated file the widget user can clobber.
            with tempfile.NamedTemporaryFile(mode="w", dir=REJECTED.parent, delete=False) as rej:
                rej.write(raw)
                rej_path = Path(rej.name)
            try:
                os.replace(rej_path, REJECTED)
            finally:
                rej_path.unlink(missing_ok=True)
        except OSError:
            pass

    # --- history (side effect) ----------------------------------------------
    if not RENDER_ONLY and s_i.isdigit() and w_i.isdigit() and not OFFLINE:
        try:
            with open(HISTORY_FILE, "a") as f:
                f.write(f"{int(datetime.now().timestamp())} {s_i} {w_i}\n")
            lines = HISTORY_FILE.read_text().splitlines()
            if len(lines) > HISTORY_CAP:
                HISTORY_FILE.write_text("\n".join(lines[-HISTORY_CAP:]) + "\n")
        except Exception:
            pass

    # --- prediction ----------------------------------------------------------
    pred_verdict = pred_eta = ""
    weekly_forecast = {}
    pj = run_json([PREDICT, session_reset, week_reset], 3)
    if isinstance(pj, dict):
        wk = pj.get("weekly") or {}
        weekly_forecast = wk
        pred_verdict = wk.get("verdict") or ""
        pred_eta = wk.get("eta_iso") or ""

    session_reset_txt = fmt_reset_iso(session_reset)
    week_reset_txt = fmt_reset_iso(week_reset)

    # --- alerts (side effects) ----------------------------------------------
    if not RENDER_ONLY and not OFFLINE and not shape_broken:
        _alerts(s_i, w_i, spend_i, on_credits, spend_used_str, spend_limit_str, week_reset_txt)

    # --- shape broken --------------------------------------------------------
    if shape_broken:
        emit("?% | sfimage=questionmark.circle color=#FF9500 size=12")
        sep()
        emit("Claude Usage Dashboard | href=https://claude.ai/settings/usage size=14")
        sep()
        emit("API shape may have changed | color=#FF9500")
        emit("Open the unrecognised response below — if Anthropic renamed a key, paste")
        emit("the snippet to the maintainer so the plugin can be updated.")
        sep()
        emit(f"View unrecognised response | bash='/usr/bin/open' param1='-t' param2='{REJECTED}' terminal=false sfimage=doc.text")
        emit("Refresh now | refresh=true sfimage=arrow.clockwise")
        emit(f"Edit config | bash='/usr/bin/open' param1='-t' param2='{CONFIG}' terminal=false sfimage=pencil")
        emit("Quit (until next interval) | href=https://claude.ai/settings/usage")
        flush()
        return 0

    # --- provider status -----------------------------------------------------
    incident = status_note = ""
    if STATUS_ALERT != "off":
        sj = run_json([STATUS_CHECK], 7)
        if isinstance(sj, dict):
            a_st, o_st = sj.get("anthropic", "unknown"), sj.get("openai", "unknown")
            a_desc = _san(sj.get("anthropic_desc", ""))
            o_desc = _san(sj.get("openai_desc", ""))
            a_take = a_st == "incident" or (STATUS_ALERT == "minor" and a_st == "degraded")
            o_take = o_st == "incident" or (STATUS_ALERT == "minor" and o_st == "degraded")
            parts = []
            if a_take:
                parts.append("Claude")
            if o_take:
                parts.append("OpenAI")
            incident = " + ".join(parts)
            notes = []
            if not a_take and a_st in ("degraded", "incident"):
                notes.append(f"Claude: {a_desc or 'degraded service'}")
            if not o_take and o_st in ("degraded", "incident"):
                notes.append(f"OpenAI: {o_desc or 'degraded service'}")
            status_note = " · ".join(notes)

    # The title keeps session/weekly numbers in their familiar positions, but
    # its warning colour follows the most constrained known Claude allowance.
    limits = [("Session", session, session_reset), ("Weekly", week, week_reset)]
    limits += [(str(name), percentage(pct), reset) for name, pct, reset in scoped]
    known_limits = [item for item in limits if item[1] is not None]
    tightest = max(known_limits, key=lambda item: item[1]) if known_limits else None
    warning_pct = rnd(tightest[1]) if tightest else s_i

    # --- menu bar title ------------------------------------------------------
    if on_credits:
        icon = "sfimage=creditcard.fill"
        title_color = color_for_pct(spend_i)
        title = f"{s_i or '?'}% · {spend_used_str}"
    else:
        title_color = color_for_pct(warning_pct)
        ring = ring_img(s_i, title_color) if s_i.isdigit() else ""
        if ring:
            icon = f"image={ring}"
        elif warning_pct.isdigit() and int(warning_pct) >= CRIT_PCT:
            icon = "sfimage=bolt.trianglebadge.exclamationmark"
        elif warning_pct.isdigit() and int(warning_pct) >= WARN_PCT:
            icon = "sfimage=gauge.with.dots.needle.67percent"
        else:
            icon = "sfimage=gauge.with.dots.needle.33percent"
        title = f"{s_i or '?'}%"
        if w_i.isdigit():
            title = f"{title} · {w_i}%w"
        if MENUBAR_MODE == "both":
            cc_t = _summ(CC_SUMMARY, ["today", "total_tokens"])
            if cc_t is not None:
                title = f"{title} · cc {humanize_tokens(cc_t)}"
        if MENUBAR_MODE in ("codex", "both"):
            cx_q = _summ(CODEX_SUMMARY, ["quota", "primary", "used_percent"])
            if cx_q is not None:
                title = f"{title} · cx {rnd(cx_q)}%"
            else:
                cx_t = _summ(CODEX_SUMMARY, ["today", "tokens"])
                if cx_t is not None:
                    title = f"{title} · cx {humanize_tokens(cx_t)}"

    if incident:
        icon = "sfimage=exclamationmark.triangle.fill"
        title_color = "#FF9500"
    if OFFLINE:
        title = f"⏸ {title}"
    # No href on the title line: a left-click of the menu-bar icon shows the
    # native dropdown instantly (no webview to spin up).
    emit(f"{title} | {icon} {colorkey(title_color)} size=12")

    # --- dropdown ------------------------------------------------------------
    sep()
    emit("Claude Usage Dashboard | href=https://claude.ai/settings/usage size=14")
    sep()

    if OFFLINE:
        try:
            last = datetime.fromtimestamp(RAW.stat().st_mtime).strftime("%a %H:%M")
            suffix = f" ({last})"
        except Exception:
            suffix = ""
        emit(f"⏸ Offline · showing last update{suffix} | size=11 color=#8E8E93 href=https://claude.ai/settings/usage")
        sep()

    if incident:
        emit(f"⚠ {incident} reporting an outage | color=#FF9500 href=https://status.anthropic.com")
        sep()
    elif status_note:
        emit(f"{status_note} | size=11 color={LBL} href=https://status.anthropic.com")
        sep()

    emit(f"CLAUDE · account limits | size=12 color={LBL} sfimage=cloud.fill")

    if tightest and not on_credits:
        name, pct, reset = tightest
        shown = int(rnd(pct))
        remaining = max(0, 100 - shown)
        if shown >= 100:
            state = "Limit reached"
        elif shown >= CRIT_PCT:
            state = "Nearly at limit"
        elif shown >= WARN_PCT:
            state = "Approaching limit"
        else:
            state = "Room available"
        prefix = "Saved reading" if OFFLINE else state
        emit(f"{prefix} · {_san(name)} has {remaining}% left | size=12 {colorkey(color_for_pct(pct))}")
        reset_text = fmt_reset_iso(reset)
        if reset_text:
            emit(f"  Resets {reset_text} | size=11 color={LBL}")
        sep()

    if on_credits:
        spend_clr = color_for_pct(spend_i)
        emit(f"On usage credits · {spend_used_str} of {spend_limit_str} ({spend_i}%) | size=12 {colorkey(spend_clr)}")
        emit(f"{progress_bar(spend_i)} | font=Menlo size=12 {colorkey(spend_clr)}")
        emit(f"  Weekly limit reached — Claude is billing against your credit pool until {week_reset_txt or 'reset'}. | size=11 color={LBL}")
        sep()

    if session is not None and session != "":
        print_metric("Session · 5h", session, session_reset_txt)
        if DETAIL_LEVEL != "compact":
            trend = _session_trend()
            s_img = spark_img(trend, color_for_pct(s_i))
            if s_img:
                emit(f"  trend · last 2h | size=11 color={LBL} {s_img}")
            else:
                spark = sparkline(trend)
                if spark:
                    emit(f"  trend (last ~2h) {spark} | font=Menlo size=13 {colorkey(color_for_pct(s_i))}")
        sep()

    if week is not None and week != "":
        if on_credits:
            emit(f"Weekly · all models · 100% · exhausted, resets {week_reset_txt or '?'} | size=12 color=#FF3B30")
            emit(f"{progress_bar(100)} | font=Menlo size=12 color=#FF3B30")
            sep()
        else:
            print_metric("Weekly · all models", week, week_reset_txt)
            if not OFFLINE:
                # One compact pace line instead of the three-line plan: the
                # verdict is the actionable bit; the bar already shows the rest.
                v = weekly_forecast.get("verdict")
                eta = weekly_forecast.get("eta_iso")
                if v == "throttle" and eta:
                    emit(f"  ⚡ ~100% by {fmt_reset_iso(eta)} | size=11 {colorkey(color_for_pct(90))}")
                elif v == "headroom":
                    emit(f"  ✓ on track to reset | size=11 {colorkey(color_for_pct(30))}")
                elif v == "flat" and weekly_forecast.get("reason") == "steady":
                    # "flat/steady" only means the slope is low, not that you are
                    # safe: at 99% a low slope is still 99%. State the pace
                    # neutrally and leave the all-clear to the "headroom" verdict.
                    emit(f"  · steady pace | size=11 color={DIM}")
            sep()

    for name, pct, reset in scoped:
        if name == "" or name is None:
            continue
        print_metric(f"Weekly · {name}", pct, fmt_reset_iso(reset))
        sep()

    if extra_enabled == "true" and not on_credits:
        if spend_used not in (None, "null") and spend_limit is not None:
            emit(f"Credits available · {spend_used_str} of {spend_limit_str} used this month | size=12 color={LBL}")
        else:
            emit(f"Credits enabled · ready when weekly limit hits | size=12 color={LBL}")
        sep()

    if DETAIL_LEVEL != "compact":
        _render_claude_code()
    _render_codex(compact=DETAIL_LEVEL == "compact")
    _render_footer()

    flush()
    return 0


def _san(s: str) -> str:
    return (s or "").replace("|", " ").replace("\n", " ").replace("\r", " ")

def _paramq(s) -> str:
    """Return a value wrapped in quotes safe for a SwiftBar paramN=... field.

    SwiftBar ends a quoted value at the first matching quote and does NOT join
    shell-style quoted segments, so we pick the quote type the value lacks
    (double quotes for an install path containing an apostrophe). Newline/CR
    break the row protocol and are dropped - neither belongs in a real path. A
    clean path stays single-quoted, so normal installs render exactly as before.
    """
    s = str(s).replace("\r", "").replace("\n", "")
    if "'" not in s:
        return f"'{s}'"
    if '"' not in s:
        return f'"{s}"'
    # Both quote types: no safe quoting exists. Fail closed to an inert value so
    # the action does nothing, rather than silently pointing at a different file.
    return "''"

def _summ(path: Path, keys: list[str]):
    try:
        v = json.loads(path.read_text())
        for k in keys:
            v = v[k]
        return v
    except Exception:
        return None

def _session_trend() -> str:
    try:
        lines = HISTORY_FILE.read_text().splitlines()[-24:]
        return " ".join(ln.split()[1] for ln in lines if len(ln.split()) >= 2) + " "
    except Exception:
        return ""


def _alerts(s_i, w_i, spend_i, on_credits, spend_used_str, spend_limit_str, week_reset_txt):
    # mkdir mutex so two ticks do not double-fire.
    lock = ALERT_STATE + ".d"
    try:
        os.mkdir(lock)
    except FileExistsError:
        # Self-heal a lock orphaned by a killed process (SwiftBar's plugin
        # timeout, or SIGKILL) - a real render releases it in well under a
        # second, so anything older than 60s is stale, not a live peer. Without
        # this the next tick returns here forever and alerts silently die. This
        # covers the kill case that neither finally nor a bash trap can.
        try:
            if time.time() - os.stat(lock).st_mtime > 60:
                os.rmdir(lock)
                os.mkdir(lock)
            else:
                return
        except Exception:
            return
    except Exception:
        return
    try:
        if s_i.isdigit():
            si = int(s_i)
            if si >= CRIT_PCT:
                notify("Claude Usage", f"Session at {si}% — slow down or wait for reset", "session_crit")
            elif si >= WARN_PCT:
                notify("Claude Usage", f"Session at {si}% — approaching limit", "session_warn")
            else:
                clear_alert("session_crit"); clear_alert("session_warn")
        if w_i.isdigit():
            wi = int(w_i)
            if wi >= 100:
                if on_credits:
                    notify("Claude Usage", f"Weekly limit hit — now on usage credits ({spend_used_str} of {spend_limit_str})", "weekly_hit_100")
                else:
                    notify("Claude Usage", "Weekly limit hit — credits disabled, you may be blocked", "weekly_hit_100")
            elif wi >= CRIT_PCT:
                notify("Claude Usage", f"Weekly at {wi}% — limit approaching, resets {week_reset_txt}", "weekly_crit")
                clear_alert("weekly_hit_100")
            else:
                clear_alert("weekly_crit"); clear_alert("weekly_hit_100")
        if on_credits and spend_i.isdigit():
            spi = int(spend_i)
            if spi >= CRIT_PCT:
                notify("Claude Usage", f"Credits at {spi}% ({spend_used_str} of {spend_limit_str}) — top up or wait for reset", "credits_crit")
            elif spi >= WARN_PCT:
                notify("Claude Usage", f"Credits at {spi}% — approaching monthly cap", "credits_warn")
            else:
                clear_alert("credits_crit"); clear_alert("credits_warn")
        else:
            clear_alert("credits_crit"); clear_alert("credits_warn")
        # reset-ready ping
        try:
            last_s, last_w = LASTSEEN_FILE.read_text().split()[:2]
            if s_i.isdigit() and last_s.isdigit() and int(last_s) - int(s_i) >= RESET_DROP:
                notify("Claude Usage", "Session reset - you are clear to go", "session_reset_" + datetime.now().strftime("%Y%m%d%H"))
            if w_i.isdigit() and last_w.isdigit() and int(last_w) - int(w_i) >= RESET_DROP:
                notify("Claude Usage", "Weekly limit reset - fresh week, clear to go", "weekly_reset_" + datetime.now().strftime("%Y%m%d"))
        except Exception:
            pass
        if s_i.isdigit() and w_i.isdigit():
            LASTSEEN_FILE.write_text(f"{s_i} {w_i}\n")
        # New version available: banner once per (version, commits-behind) so it
        # never nags, reusing the usage-alert dedup + mute. Fires within a tick of
        # the 6-hourly background check finding the repo behind, with the menu
        # never opened. UPDATE_STATUS is "<behind> <local_sha> <epoch>"; require all
        # three so a truncated write never fires a bogus banner. (A force-push that
        # kept the same behind count would be missed - but we never force-push the
        # distribution branch, and a normal push always bumps the count.)
        try:
            parts = UPDATE_STATUS.read_text().split()
            if len(parts) >= 3 and parts[0].isdigit() and int(parts[0]) > 0:
                n = int(parts[0])
                notify("Claude Usage",
                       f"Update available - {n} new version{'' if n == 1 else 's'}. "
                       "Open the menu and click Update now.",
                       f"update_{parts[1]}_{parts[0]}")
        except Exception:
            pass
    finally:
        try:
            os.rmdir(lock)
        except Exception:
            pass


def _wow(week_tok, prev_tok) -> str:
    try:
        p = float(prev_tok)
        if p <= 0:
            return ""
        d = (float(week_tok) - p) / p * 100
        return f"▲{d:.0f}%" if d >= 0 else f"▼{-d:.0f}%"
    except Exception:
        return ""


def _render_claude_code():
    cc = run_json([CC_USAGE], 4, CC_SUMMARY)
    if not isinstance(cc, dict):
        return
    today = cc.get("today") or {}
    week = cc.get("week") or {}
    month = cc.get("month") or {}
    prev = cc.get("prev_week") or {}
    by_model = today.get("by_model") or {}
    top3 = sorted([(k, v) for k, v in by_model.items() if v > 0], key=lambda kv: -kv[1])[:3]
    model_parts = " · ".join(f"{k} {humanize_tokens(v)}" for k, v in top3)
    wow = _wow(week.get("total_tokens", 0), prev.get("total_tokens", 0))
    daily = " ".join(str(x) for x in (cc.get("daily") or []))

    emit(f"CLAUDE CODE | size=12 color={SEC_CC} sfimage=chevron.left.forwardslash.chevron.right")
    emit(f"Today · {humanize_tokens(today.get('total_tokens', 0))} tokens · ≈{humanize_usd(today.get('est_cost_usd', 0))} value | size=12 color={LBL}")
    if model_parts:
        emit(f"  by model · {model_parts} | size=11 color={LBL}")
    if wow:
        emit(f"7 days · {humanize_tokens(week.get('total_tokens', 0))} ({wow} vs prev) · 30 days · {humanize_tokens(month.get('total_tokens', 0))} | size=11 color={LBL}")
    else:
        emit(f"7 days · {humanize_tokens(week.get('total_tokens', 0))} · 30 days · {humanize_tokens(month.get('total_tokens', 0))} | size=11 color={LBL}")
    cc_img = spark_img(daily, SEC_CC)
    if cc_img:
        emit(f"  7-day trend | size=11 color={LBL} {cc_img}")
    else:
        s = sparkline(daily)
        if s:
            emit(f"  7-day trend {s} | font=Menlo size=11 color={LBL}")
    emit(f"≈{humanize_usd(month.get('est_cost_usd', 0))} of API value · last 30 days | size=11 color={LBL}")
    sep()


def _render_codex(compact=False):
    cx = run_json([CODEX_USAGE], 3, CODEX_SUMMARY)
    if not isinstance(cx, dict) or cx.get("available") is not True:
        return
    today = cx.get("today") or {}
    week = cx.get("week") or {}
    month = cx.get("month") or {}
    alltime = cx.get("all_time") or {}
    prev = cx.get("prev_week") or {}
    wow = _wow(week.get("tokens", 0), prev.get("tokens", 0))
    daily = " ".join(str(x) for x in (cx.get("daily") or []))
    threads = today.get("threads", 0)
    thr_label = "thread" if str(threads) == "1" else "threads"

    emit(f"CODEX | size=12 color={SEC_CX} sfimage=curlybraces")
    quota = cx.get("quota") or {}
    prim = quota.get("primary") or {}
    if percentage(prim.get("used_percent")) is not None:
        win = prim.get("window", "weekly")
        reset_txt = fmt_reset_epoch(prim["resets_at"]) if prim.get("resets_at") else ""
        print_metric(f"Quota · {win}", prim["used_percent"], reset_txt)
    sec = quota.get("secondary") or {}
    if percentage(sec.get("used_percent")) is not None:
        reset_txt = fmt_reset_epoch(sec["resets_at"]) if sec.get("resets_at") else ""
        print_metric(f"Quota · {sec.get('window', '5h')}", sec["used_percent"], reset_txt)
    if compact:
        if percentage(prim.get("used_percent")) is None and percentage(sec.get("used_percent")) is None:
            emit(f"No quota reading yet · local token totals are not quota percentages | size=11 color={LBL}")
        sep()
        return
    emit(f"Today · {humanize_tokens(today.get('tokens', 0))} tokens · {threads} {thr_label} | size=12 color={LBL}")
    tail = f"7 days · {humanize_tokens(week.get('tokens', 0))}"
    if wow:
        tail += f" ({wow} vs prev)"
    tail += f" · 30 days · {humanize_tokens(month.get('tokens', 0))} · all-time · {humanize_tokens(alltime.get('tokens', 0))}"
    emit(f"{tail} | size=11 color={LBL}")
    cx_img = spark_img(daily, SEC_CX)
    if cx_img:
        emit(f"  7-day trend | size=11 color={LBL} {cx_img}")
    else:
        s = sparkline(daily)
        if s:
            emit(f"  7-day trend {s} | font=Menlo size=11 color={LBL}")
    sep()


def _render_preferences():
    script = str(WIDGET_DIR / "widget_preferences.py")
    emit("Display settings | sfimage=slider.horizontal.3")
    groups = [
        ("Detail", "DETAIL_LEVEL", DETAIL_LEVEL, [("full", "Full dashboard"), ("compact", "Limits only")]),
        ("Menu bar", "MENUBAR_MODE", MENUBAR_MODE, [("claude", "Claude"), ("codex", "Claude + Codex"), ("both", "Claude + Code + Codex")]),
        ("Colours", "THEME", THEME, [("semantic", "Traffic lights"), ("colorblind", "Colour-blind friendly"), ("minimal", "Monochrome")]),
    ]
    for label, key, selected, choices in groups:
        emit(f"-- {label}")
        for value, title in choices:
            emit(f"---- {title} | bash={_paramq(PYTHON)} param1={_paramq(script)} param2='{key}' param3='{value}' terminal=false refresh=true checked={'true' if selected == value else 'false'}")


def _render_footer():
    sep()
    emit("Refresh now | refresh=true sfimage=arrow.clockwise")
    emit(f"Copy status | bash='/bin/bash' param1='-c' param2='\"{PYTHON}\" \"{COPY_SUMMARY}\" | pbcopy' terminal=false sfimage=doc.on.clipboard")
    _render_preferences()
    emit("Export usage | sfimage=square.and.arrow.up")
    emit(f"-- as CSV | bash={_paramq(PYTHON)} param1={_paramq(EXPORT)} param2='csv' terminal=false")
    emit(f"-- as JSON | bash={_paramq(PYTHON)} param1={_paramq(EXPORT)} param2='json' terminal=false")

    if _muted_display():
        mt = fmt_reset_epoch(MUTE_FILE.read_text().strip())
        emit(f"Alerts muted {('(unmutes ' + mt + ')') if mt else ''} | sfimage=bell.slash")
    else:
        emit("Alerts | sfimage=bell")
    emit(f'-- Mute 1 hour | bash=\'/bin/bash\' param1=\'-c\' param2=\'echo $(( $(date +%s)+3600 )) > "{MUTE_FILE}"\' terminal=false refresh=true')
    emit(f'-- Mute until tomorrow 9am | bash=\'/bin/bash\' param1=\'-c\' param2=\'echo $(date -j -f %Y-%m-%d-%H:%M:%S "$(date -v+1d +%Y-%m-%d)-09:00:00" +%s) > "{MUTE_FILE}"\' terminal=false refresh=true')
    emit(f"-- Unmute | bash='/bin/rm' param1='-f' param2='{MUTE_FILE}' terminal=false refresh=true")

    upd_behind, upd_sha, upd_ts = 0, "?", 0
    try:
        parts = UPDATE_STATUS.read_text().split()
        upd_behind, upd_sha, upd_ts = int(parts[0]), parts[1], int(parts[2])
    except Exception:
        pass
    update_result = {}
    try:
        update_result = json.loads(UPDATE_RESULT.read_text())
    except (OSError, ValueError):
        pass
    checked_at = update_result.get("checked_at", upd_ts)
    if not RENDER_ONLY:
        if not checked_at or datetime.now().timestamp() - checked_at > UPDATE_CHECK_INTERVAL:
            subprocess.Popen(["bash", CHECK_UPDATE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    emit("More | sfimage=ellipsis.circle")
    emit("-- Open settings | href=https://claude.ai/settings/usage")
    emit(f"-- Edit config | bash='/usr/bin/open' param1='-t' param2='{CONFIG}' terminal=false")
    emit(f"-- Force cookie refresh | bash={_paramq(PYTHON)} param1={_paramq(REFRESHER)} terminal=false refresh=true")
    emit("-----")
    emit(f"-- View raw JSON | bash='/usr/bin/open' param1='-t' param2='{RAW}' terminal=false")
    emit(f"-- View error log | bash='/usr/bin/open' param1='-t' param2='{ERR_LOG}' terminal=false")
    emit("-----")
    emit(f"-- Check for updates | bash='/bin/bash' param1='{CHECK_UPDATE}' terminal=false refresh=true")
    emit(f"-- View on GitHub | href={REPO_URL}")
    emit(f"-- Build {upd_sha} | color={LBL}")

    pr_age = 999
    pr_ok, pr_day = "false", ""
    try:
        ps = json.loads(PRICING_STATUS.read_text())
        pr_ok = str(ps.get("ok", False)).lower()
        ts = ps.get("checked_at", "")
        pr_day = ts.split("T")[0]
        try:
            dt = datetime.fromisoformat(ts.split(".")[0])
            pr_age = (datetime.now() - dt).days
        except Exception:
            pass
    except Exception:
        ps = None
    if PRICING_STATUS.exists():
        if pr_ok != "true":
            why = (ps or {}).get("detail", "unknown")[:60]
            emit(f"-- ⚠ Rate check failing: {why} | color=#FF9500")
        elif pr_age >= 8:
            emit(f"-- ⚠ Rates last checked {pr_day} ({pr_age}d ago) | color=#FF9500")
        else:
            emit(f"-- Rates checked {pr_day} | color={LBL}")
    if not RENDER_ONLY and Path(CHECK_PRICING).exists() and (not PRICING_STATUS.exists() or pr_age >= 7):
        subprocess.Popen([PYTHON, CHECK_PRICING], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    if Path(CHECK_PRICING).exists():
        emit(f"-- Check rates now | bash={_paramq(PYTHON)} param1={_paramq(CHECK_PRICING)} terminal=false refresh=true")

    state = update_result.get("state")
    if state:
        sep()
        emit(f"{_san(update_result.get('message', ''))} | size=11 color={LBL}")
        for change in update_result.get("changes", [])[:5]:
            emit(f"  • {_san(change)} | size=11 color={LBL}")
        if state in ("error", "offline"):
            action = UPDATE_SCRIPT if state == "error" else CHECK_UPDATE
            emit(f"Retry {'update' if state == 'error' else 'check'} | bash='/bin/bash' param1='{action}' terminal=false refresh=true")
    if (state == "available") or (not state and upd_behind > 0):
        sep()
        emit(f"⬆ Update available ({upd_behind} new) | color=#FF9500 sfimage=arrow.down.circle.fill")
        emit(f"Update now | bash='/bin/bash' param1='{UPDATE_SCRIPT}' terminal=false refresh=true sfimage=arrow.down.circle")


def _muted_display() -> bool:
    try:
        return int(MUTE_FILE.read_text().strip()) > int(datetime.now().timestamp())
    except Exception:
        return False


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        # Never blank the menu bar. main() buffers its lines and flushes once at
        # the end, so a crash before that point has printed nothing - emit a
        # minimal, clickable fallback and log the traceback.
        import traceback
        try:
            with open(ERR_LOG, "a") as _f:
                _f.write("render_menu error:\n" + traceback.format_exc())
        except Exception:
            pass
        sys.stdout.write(
            "⚠ Claude | sfimage=exclamationmark.circle color=#FF9500 size=12\n"
            "---\n"
            "The widget hit a render error. It usually clears on the next tick. | size=12\n"
            f"View error log | bash='/usr/bin/open' param1='-t' param2='{ERR_LOG}' terminal=false\n"
            "Refresh now | refresh=true sfimage=arrow.clockwise\n"
        )
        sys.exit(0)
