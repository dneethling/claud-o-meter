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


def main() -> int:
    if already_configured():
        print("Already configured (leaving existing ~/.claude-usage-widget.conf untouched).")
        return 0

    for label, service, cookie_file, _mtime in rc.candidates():
        cookies, _err = rc.try_extract(service, cookie_file)
        if not cookies:
            continue
        org = cookies.get("lastActiveOrg")
        session = cookies.get("sessionKey")
        if org and session:
            usage_url = f"https://claude.ai/api/organizations/{org}/usage"
            CONFIG.write_text(
                "# Claude Usage Widget config\n"
                f"USAGE_URL={usage_url}\n"
                "COOKIE=\n"
            )
            os.chmod(CONFIG, 0o600)
            print(f"Configured for org {org} (detected from {label}).")
            return 0

    sys.stderr.write(
        "Could not find a logged-in Claude session in Arc/Chrome/Brave.\n"
        "Open https://claude.ai in one of those browsers, sign in, then re-run.\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
