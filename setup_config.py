#!/usr/bin/env python3
"""First-run config: detect the user's Claude org id from their browser and
write ~/.claude-usage-widget.conf with the right USAGE_URL. Idempotent - if a
valid USAGE_URL is already present it leaves the file untouched. The COOKIE line
is filled in afterwards by refresh_cookie.py (validated against the API).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import refresh_cookie as rc  # reuse the browser-probing helpers

CONFIG = Path.home() / ".claude-usage-widget.conf"


def already_configured() -> bool:
    if not CONFIG.exists():
        return False
    for line in CONFIG.read_text().splitlines():
        if line.startswith("USAGE_URL=") and "organizations/" in line and "PASTE" not in line:
            return True
    return False


def _write(usage_url: str) -> None:
    CONFIG.write_text(
        "# Claude Usage Widget config\n"
        f"USAGE_URL={usage_url}\n"
        "COOKIE=\n"
    )
    os.chmod(CONFIG, 0o600)


def main() -> int:
    if already_configured():
        print("Already configured (leaving existing ~/.claude-usage-widget.conf untouched).")
        return 0

    reasons: list[str] = []
    fallback: tuple[str, str] | None = None  # (label, usage_url) best-effort if unverifiable

    for label, service, cookie_file, _mtime in rc.candidates():
        tag = f"{label}/{cookie_file.parent.name}"
        cookies, err = rc.try_extract(service, cookie_file)
        if not cookies:
            reasons.append(f"  [{tag}] {err}")
            continue
        org = cookies.get("lastActiveOrg")
        session = cookies.get("sessionKey")
        if not (org and session):
            miss = "lastActiveOrg" if not org else "sessionKey"
            reasons.append(f"  [{tag}] logged in but missing the {miss} cookie - open claude.ai and reload the page once")
            continue

        # Validate the session against the API so we pick the profile that
        # actually works (a friend may have several profiles / accounts).
        usage_url = f"https://claude.ai/api/organizations/{org}/usage"
        ok, vreason = rc.validate_cookies(cookies, usage_url)
        if ok:
            _write(usage_url)
            print(f"Configured for org {org} (detected from {label}, session verified).")
            return 0
        # Only a definite auth rejection means the session itself is bad. A
        # transient failure (offline, 5xx, 429 rate-limit, Cloudflare HTML
        # interstitial, curl_cffi not importable) should not block setup: keep
        # it as a best-effort config and let refresh_cookie validate on first
        # run rather than send the user round the login loop again.
        definite = (vreason == "account_session_invalid"
                    or vreason.startswith("http 401")
                    or vreason.startswith("http 403"))
        reasons.append(f"  [{tag}] session did not validate: {vreason}")
        if not definite and fallback is None:
            fallback = (label, usage_url)

    # Could not verify any session live, but one looked real and failed only
    # transiently: write it and let the first refresh confirm it.
    if fallback:
        label, usage_url = fallback
        _write(usage_url)
        print(f"Configured from {label} (session not verified yet - "
              "the widget checks it on its first refresh).")
        return 0

    sys.stderr.write("Could not find a logged-in, valid Claude session in Arc/Chrome/Brave.\n")
    for r in reasons:
        sys.stderr.write(r + "\n")
    sys.stderr.write(
        "\nMost common fixes:\n"
        "  - Open https://claude.ai in Chrome/Arc/Brave, sign in, load the page once, then re-run.\n"
        "  - If a line says 'no keychain entry', approve the macOS Keychain prompt for that browser\n"
        "    (it asks permission to read the browser's Safe Storage key - click Always Allow).\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
