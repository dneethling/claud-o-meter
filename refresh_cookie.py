#!/usr/bin/env python3
"""Refresh Claude.ai cookies in ~/.claude-usage-widget.conf from the live browser.

Reads cookies directly from the Chromium-family browser you actually use, decrypts
them with the browser's Safe Storage key (fetched from macOS Keychain), and writes
a fresh COOKIE= line into the widget config.

Auto-detects which browser has the freshest cookie DB across Chrome (all profiles),
Arc, Brave, and Chromium. No Chrome-quitting required — pycookiecheat copies the DB.

Runs silently on success; exits non-zero with stderr on failure.
Designed for launchd / cron.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pycookiecheat import chrome_cookies

CONFIG_PATH = Path.home() / ".claude-usage-widget.conf"
NEEDED = ["sessionKey", "lastActiveOrg", "anthropic-device-id", "cf_clearance", "__cf_bm"]
REQUIRED = ["sessionKey", "cf_clearance"]

HOME = Path.home()

# (label, keychain_service_name, glob of cookie DB paths to probe)
BROWSERS = [
    ("Arc",      "Arc Safe Storage",      [HOME / "Library/Application Support/Arc/User Data/Default/Cookies",
                                           *sorted((HOME / "Library/Application Support/Arc/User Data").glob("Profile */Cookies"))]),
    ("Chrome",   "Chrome Safe Storage",   [HOME / "Library/Application Support/Google/Chrome/Default/Cookies",
                                           *sorted((HOME / "Library/Application Support/Google/Chrome").glob("Profile */Cookies"))]),
    ("Brave",    "Brave Safe Storage",    [HOME / "Library/Application Support/BraveSoftware/Brave-Browser/Default/Cookies",
                                           *sorted((HOME / "Library/Application Support/BraveSoftware/Brave-Browser").glob("Profile */Cookies"))]),
    ("Chromium", "Chromium Safe Storage", [HOME / "Library/Application Support/Chromium/Default/Cookies"]),
]


def keychain_password(service: str) -> str | None:
    try:
        return subprocess.check_output(
            ["security", "find-generic-password", "-w", "-s", service],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except subprocess.CalledProcessError:
        return None


def candidates():
    """Yield (label, profile_path, cookie_file, mtime) for every existing cookie DB,
    newest first."""
    found = []
    for label, service, paths in BROWSERS:
        for p in paths:
            if p.exists():
                found.append((label, service, p, p.stat().st_mtime))
    found.sort(key=lambda x: x[3], reverse=True)
    return found


def try_extract(label, service, cookie_file):
    pw = keychain_password(service)
    if not pw:
        return None, f"no keychain entry for {service!r}"
    try:
        cookies = chrome_cookies(
            "https://claude.ai/settings/usage",
            cookie_file=str(cookie_file),
            password=pw,
        )
    except Exception as e:
        return None, f"decrypt failed: {e}"
    if not any(k in cookies for k in REQUIRED):
        return None, "decrypted but no Claude cookies present"
    return cookies, None


def refresh():
    cands = candidates()
    if not cands:
        sys.stderr.write("No Chromium-family cookie DB found (Chrome/Arc/Brave/Chromium).\n")
        sys.exit(2)

    last_err = None
    for label, service, cookie_file, _mtime in cands:
        cookies, err = try_extract(label, service, cookie_file)
        if cookies is None:
            last_err = f"[{label}:{cookie_file.parent.name}] {err}"
            continue
        if all(k in cookies for k in REQUIRED):
            picked = {k: cookies[k] for k in NEEDED if k in cookies}
            write_config(picked, source=f"{label}/{cookie_file.parent.name}")
            return label, cookie_file, picked
        last_err = f"[{label}:{cookie_file.parent.name}] missing required: {[k for k in REQUIRED if k not in cookies]}"

    sys.stderr.write(f"No browser had complete Claude cookies. Last error: {last_err}\n")
    sys.stderr.write("Open claude.ai/settings/usage in your browser (logged in), then retry.\n")
    sys.exit(2)


def write_config(cookies_dict, source):
    cookie_str = "; ".join(f"{k}={v}" for k, v in cookies_dict.items())
    if not CONFIG_PATH.exists():
        sys.stderr.write(f"Config not found: {CONFIG_PATH}\n")
        sys.exit(2)

    lines = CONFIG_PATH.read_text().splitlines()
    new_lines, replaced = [], False
    for line in lines:
        if line.strip().startswith("COOKIE="):
            new_lines.append(f"COOKIE={cookie_str}")
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        new_lines.append(f"COOKIE={cookie_str}")

    CONFIG_PATH.write_text("\n".join(new_lines) + "\n")
    CONFIG_PATH.chmod(0o600)


if __name__ == "__main__":
    import os
    try:
        label, cookie_file, picked = refresh()
        if os.environ.get("CLAUDE_USAGE_VERBOSE"):
            print(f"refreshed from {label} ({cookie_file}) — {len(picked)} cookies")
    except SystemExit:
        raise
    except Exception as e:
        sys.stderr.write(f"refresh error: {e}\n")
        sys.exit(1)
