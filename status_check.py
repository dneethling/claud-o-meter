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


def classify(body: dict) -> str:
    """Map a Statuspage status.json body to operational/incident/unknown."""
    try:
        indicator = body["status"]["indicator"]
    except (KeyError, TypeError):
        return "unknown"
    if indicator == "none":
        return "operational"
    if indicator in ("minor", "major", "critical"):
        return "incident"
    return "unknown"


def fetch(url: str) -> str:
    from curl_cffi import requests
    resp = requests.get(url, impersonate="chrome", timeout=TIMEOUT_SECONDS)
    if resp.status_code != 200:
        return "unknown"
    try:
        return classify(json.loads(resp.text))
    except Exception:
        return "unknown"


def build() -> dict:
    result = {"generated_at": time.time()}
    for name, url in ENDPOINTS.items():
        try:
            result[name] = fetch(url)
        except Exception:
            result[name] = "unknown"
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
