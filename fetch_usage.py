#!/usr/bin/env python3
"""Fetch Claude.ai usage JSON, bypassing Cloudflare via curl_cffi (Chrome TLS fingerprint).

Writes the JSON body to stdout only on a 200 response with parseable JSON.
On any other outcome (non-200, parse error, Cloudflare challenge HTML, account-session-invalid),
writes a one-line diagnostic to stderr and exits 1 with empty stdout — so callers
never write garbage into /tmp/claude-usage-raw.json.
"""
import json
import sys
from pathlib import Path

from curl_cffi import requests

CONFIG_PATH = Path.home() / ".claude-usage-widget.conf"
FETCH_TIMEOUT_SECONDS = 15
SNIPPET_MAX = 200


def load_config():
    if not CONFIG_PATH.exists():
        sys.stderr.write(f"Missing config at {CONFIG_PATH}\n")
        sys.exit(2)
    cfg = {}
    for line in CONFIG_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


def cookie_dict(cookie_str):
    out = {}
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = v
    return out


def snippet(text: str) -> str:
    """Compact one-line preview of a response body for stderr."""
    if not text:
        return "(empty)"
    return text.replace("\n", " ").replace("\r", " ")[:SNIPPET_MAX]


def fetch_usage_json(url: str, cookie: str):
    """Fetch and validate the usage JSON. Returns (body_str, None) on success or
    (None, error_str) on any failure — never raises, never exits. Shared by the
    CLI `main()` (menu-bar widget) and `ios_relay.py` (the iOS push relay) so both
    hit the endpoint exactly the same way (Chrome TLS fingerprint, same headers)."""
    headers = {
        "accept": "*/*",
        "anthropic-client-platform": "web_claude_ai",
        "content-type": "application/json",
        "referer": "https://claude.ai/settings/usage",
    }

    try:
        resp = requests.get(
            url,
            headers=headers,
            cookies=cookie_dict(cookie),
            impersonate="chrome",
            timeout=FETCH_TIMEOUT_SECONDS,
        )
    except Exception as e:
        return None, f"fetch error: {e}"

    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}: {snippet(resp.text)}"

    # Validate JSON before letting the body reach a caller. A 200 with Cloudflare
    # HTML or an Anthropic error envelope still needs to be treated as a failure.
    try:
        body = json.loads(resp.text)
    except json.JSONDecodeError as e:
        return None, f"Non-JSON 200 response ({e}): {snippet(resp.text)}"

    if isinstance(body, dict) and body.get("type") == "error":
        return None, f"API error envelope: {snippet(resp.text)}"

    return resp.text, None


def main():
    cfg = load_config()
    url = cfg.get("USAGE_URL")
    cookie = cfg.get("COOKIE")
    if not url or not cookie or "PASTE" in url:
        sys.stderr.write("Fill in USAGE_URL and COOKIE in the config.\n")
        sys.exit(2)

    body, err = fetch_usage_json(url, cookie)
    if body is None:
        sys.stderr.write(f"{err}\n")
        sys.exit(1)

    sys.stdout.write(body)


if __name__ == "__main__":
    main()
