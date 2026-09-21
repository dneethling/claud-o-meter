#!/usr/bin/env python3
"""Map the raw Claude.ai usage JSON into a compact Live Activity content-state.

The iOS Live Activity (Dynamic Island pill + Lock Screen card) renders a tiny,
fixed set of fields. This module turns the same usage JSON the SwiftBar plugin
parses (`five_hour`, `seven_day`, `limits[]`, `spend`, ...) into that small dict,
so the phone never has to understand Anthropic's response shape or do any date
math — the relay pre-formats everything here.

Pure and dependency-free (stdlib only) so it is unit-testable without network,
Apple credentials, or curl_cffi. `ios_relay.py` calls `build_state()` each tick.

Reset strings are formatted WITHOUT strftime's `%-`/`%#` platform flags so the
same code renders identically on macOS, Linux, and Windows (the relay is
cross-platform by design).
"""
from __future__ import annotations

from datetime import datetime, timezone

# Colour thresholds — kept in lockstep with the SwiftBar plugin (WARN_PCT/CRIT_PCT)
# so the phone and the menu bar agree on green/orange/red at a glance.
WARN_PCT = 60
CRIT_PCT = 85
MAX_MODELS = 3  # Dynamic Island / Lock Screen real estate is tiny; cap the list.

_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun",
           "jul", "aug", "sep", "oct", "nov", "dec"]
_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# Minimal currency-symbol map; unknown currencies fall back to the ISO code.
_CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "ZAR": "R",
                     "JPY": "¥", "CAD": "$", "AUD": "$"}


def round_pct(value) -> int | None:
    """Round a utilisation figure to a whole percent, halves up (matches the
    plugin's `printf %.0f`). Returns None for missing/garbage input rather than
    a misleading 0."""
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    # Halves-up (float() then int(x+0.5)); avoids Python's banker's rounding.
    return int(f + 0.5) if f >= 0 else -int(-f + 0.5)


def color_for_pct(pct: int | None) -> str:
    """Semantic bucket for a percentage: 'green' | 'orange' | 'red' | 'gray'.
    The Swift side owns the actual colour values; it just switches on this name."""
    if pct is None:
        return "gray"
    if pct >= CRIT_PCT:
        return "red"
    if pct >= WARN_PCT:
        return "orange"
    return "green"


def fmt_reset(iso: str | None, now: datetime) -> str:
    """Format an ISO reset timestamp as a short, glanceable relative label.

    < 0      -> "now"
    < 60m    -> "in 42m"
    < 24h    -> "in 3h 07m"
    >= 24h   -> "wed 22 jul"   (weekday + date; unambiguous a week out)

    `now` is passed in (never read from the clock here) so the mapping is
    deterministic and testable.
    """
    if not iso:
        return ""
    s = iso.strip().replace("Z", "+00:00")
    # Trim fractional seconds that datetime.fromisoformat can't always parse,
    # while preserving any trailing timezone offset.
    if "." in s:
        head, tail = s.split(".", 1)
        tz = ""
        for c in ("+", "-"):
            if c in tail:
                tz = c + tail.split(c, 1)[1]
                break
        s = head + tz
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Compare and render in the relay host's local timezone.
    dt_local = dt.astimezone()
    now_local = now.astimezone()
    total_min = int((dt_local - now_local).total_seconds() // 60)
    if total_min < 0:
        return "now"
    if total_min < 60:
        return f"in {total_min}m"
    if total_min < 24 * 60:
        h, m = divmod(total_min, 60)
        return f"in {h}h {m:02d}m"
    return f"{_WEEKDAYS[dt_local.weekday()]} {dt_local.day} {_MONTHS[dt_local.month - 1]}"


def fmt_money(amount_minor, currency: str | None, exponent) -> str:
    """Render a minor-unit money amount (e.g. 120 cents) as "$1.20"."""
    if amount_minor is None or amount_minor == "":
        return ""
    try:
        minor = float(amount_minor)
        exp = int(exponent) if exponent not in (None, "") else 2
    except (TypeError, ValueError):
        return ""
    major = minor / (10 ** exp)
    symbol = _CURRENCY_SYMBOLS.get((currency or "").upper(), "")
    body = f"{major:.{max(exp, 0)}f}"
    if symbol:
        return f"{symbol}{body}"
    code = (currency or "").upper()
    return f"{body} {code}".strip()


def _scoped_models(raw: dict, now: datetime) -> list[dict]:
    """Per-model weekly limits from limits[] (Fable / Sonnet / Opus / ...),
    falling back to the legacy seven_day_sonnet/opus fields. Mirrors the jq the
    SwiftBar plugin uses. Returns up to MAX_MODELS entries sorted by percent."""
    models: list[dict] = []
    for lim in raw.get("limits") or []:
        if not isinstance(lim, dict):
            continue
        scope = lim.get("scope") or {}
        model = scope.get("model") or {}
        name = model.get("display_name")
        if not name:
            continue
        pct = round_pct(lim.get("percent"))
        if pct is None:
            continue
        models.append({"name": name, "pct": pct,
                       "color": color_for_pct(pct),
                       "reset": fmt_reset(lim.get("resets_at"), now)})

    if not models:
        for key, label in (("seven_day_sonnet", "Sonnet"), ("seven_day_opus", "Opus")):
            block = raw.get(key) or {}
            pct = round_pct(block.get("utilization"))
            if pct is not None:
                models.append({"name": label, "pct": pct,
                               "color": color_for_pct(pct),
                               "reset": fmt_reset(block.get("resets_at"), now)})

    models.sort(key=lambda m: m["pct"], reverse=True)
    return models[:MAX_MODELS]


def _credits(raw: dict) -> dict | None:
    """Usage-credit / spend block, when the account has extra usage enabled."""
    spend = raw.get("spend") or {}
    used = (spend.get("used") or {})
    limit = (spend.get("limit") or {})
    used_minor = used.get("amount_minor")
    if not spend.get("enabled") or used_minor is None:
        # Fall back to the lighter extra_usage flag for "enabled but unused".
        if raw.get("extra_usage", {}).get("is_enabled"):
            return {"enabled": True, "used": "", "limit": "", "pct": None}
        return None
    currency = used.get("currency")
    exp = used.get("exponent", 2)
    return {
        "enabled": True,
        "used": fmt_money(used_minor, currency, exp),
        "limit": fmt_money(limit.get("amount_minor"), currency, exp),
        "pct": round_pct(spend.get("percent")),
    }


def build_state(raw: dict, now: datetime | None = None) -> dict:
    """Turn a raw Claude usage dict into the compact Live Activity content-state.

    The returned dict is the single source of truth for what the pill renders.
    `updated_epoch` and top-level `status` are stamped by the relay at push time.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    session_pct = round_pct((raw.get("five_hour") or {}).get("utilization"))
    weekly_pct = round_pct((raw.get("seven_day") or {}).get("utilization"))
    credits = _credits(raw)

    on_credits = bool(
        weekly_pct is not None and weekly_pct >= 100
        and credits and credits.get("used")
    )

    return {
        "session_pct": session_pct,
        "session_color": color_for_pct(session_pct),
        "session_reset": fmt_reset((raw.get("five_hour") or {}).get("resets_at"), now),
        "weekly_pct": weekly_pct,
        "weekly_color": color_for_pct(weekly_pct),
        "weekly_reset": fmt_reset((raw.get("seven_day") or {}).get("resets_at"), now),
        "models": _scoped_models(raw, now),
        "credits": credits,
        "on_credits": on_credits,
    }
