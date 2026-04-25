#!/usr/bin/env python3
"""Fetch Claude.ai usage JSON, bypassing Cloudflare via curl_cffi (Chrome TLS fingerprint)."""
import sys
from pathlib import Path

from curl_cffi import requests

CONFIG_PATH = Path.home() / ".claude-usage-widget.conf"


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


def main():
    cfg = load_config()
    url = cfg.get("USAGE_URL")
    cookie = cfg.get("COOKIE")
    if not url or not cookie or "PASTE" in url:
        sys.stderr.write("Fill in USAGE_URL and COOKIE in the config.\n")
        sys.exit(2)

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
            timeout=15,
        )
        sys.stdout.write(resp.text)
        sys.exit(0 if resp.status_code == 200 else 1)
    except Exception as e:
        sys.stderr.write(f"fetch error: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
