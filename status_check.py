#!/usr/bin/env python3
"""Check Anthropic and OpenAI public status pages; print a compact JSON verdict.

Both vendors run Atlassian Statuspage, which exposes /api/v2/status.json with a
{"status": {"indicator": "none|minor|major|critical", "description": "..."}}.
We map that to operational/incident/unknown. Cached to a summary file with a
short TTL so we do not hammer the endpoints on every 5-min tick.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

SUMMARY_PATH = Path.home() / ".claude-usage-status-summary.json"
CACHE_TTL_SECONDS = 300
TIMEOUT_SECONDS = 6
ENDPOINTS = {
    "anthropic": "https://status.anthropic.com/api/v2/status.json",
    "openai": "https://status.openai.com/api/v2/status.json",
}


def classify(body: dict) -> tuple[str, str]:
    """Map a Statuspage status.json body to (level, description).

    level is operational | degraded | incident | unknown:
      none             -> operational
      minor            -> degraded  (elevated errors / partial degradation - a
                                     state Statuspage sits in a lot; not an outage)
      major | critical -> incident  (a real outage worth interrupting you for)

    Separating minor from major is what stops the menu bar warning firing for
    routine background degradation. The caller decides how each level is shown.
    """
    try:
        indicator = body["status"]["indicator"]
        desc = (body["status"].get("description") or "").strip()
    except (KeyError, TypeError, AttributeError):
        return "unknown", ""
    if indicator == "none":
        return "operational", desc
    if indicator == "minor":
        return "degraded", desc
    if indicator in ("major", "critical"):
        return "incident", desc
    return "unknown", desc


def fetch(url: str) -> tuple[str, str]:
    from curl_cffi import requests
    resp = requests.get(url, impersonate="chrome", timeout=TIMEOUT_SECONDS)
    if resp.status_code != 200:
        return "unknown", ""
    try:
        return classify(json.loads(resp.text))
    except Exception:
        return "unknown", ""


def build() -> dict:
    # Keep the vendor keys as plain level strings (backward compatible), and add
    # a "<vendor>_desc" with Statuspage's own summary for the dropdown line.
    result = {"generated_at": time.time()}
    for name, url in ENDPOINTS.items():
        try:
            level, desc = fetch(url)
        except Exception:
            level, desc = "unknown", ""
        result[name] = level
        result[name + "_desc"] = desc
    return result


def cached_or_fresh() -> dict:
    if SUMMARY_PATH.exists():
        try:
            cached = json.loads(SUMMARY_PATH.read_text())
            if time.time() - cached.get("generated_at", 0) < CACHE_TTL_SECONDS:
                return cached
        except Exception:
            pass
    result = build()
    try:
        tmp = SUMMARY_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(result))
        os.replace(tmp, SUMMARY_PATH)
    except Exception:
        pass
    return result


if __name__ == "__main__":
    sys.stdout.write(json.dumps(cached_or_fresh()))
