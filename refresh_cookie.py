#!/usr/bin/env python3
"""Refresh Claude.ai cookies in ~/.claude-usage-widget.conf from the live browser.

Reads cookies directly from the Chromium-family browser you actually use, decrypts
them with the browser's Safe Storage key (fetched from macOS Keychain), and writes
a fresh COOKIE= line into the widget config.

Validates each candidate against the live API before persisting, so an expired
session in (say) Chrome can't drown out a valid session in Arc. Writes atomically
under a file lock to coexist with the SwiftBar plugin's concurrent reads.

Runs silently on success; non-zero exit with stderr on failure. Designed for launchd.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.hashes import SHA1
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

CONFIG_PATH = Path.home() / ".claude-usage-widget.conf"
LOCK_PATH = Path.home() / ".claude-usage-widget.conf.lock"
NEEDED = ["sessionKey", "lastActiveOrg", "anthropic-device-id", "cf_clearance", "__cf_bm"]
# Only sessionKey is truly required to authenticate. cf_clearance / __cf_bm are
# Cloudflare cookies that are often absent (they are issued only after a CF
# challenge and expire quickly) - curl_cffi's chrome TLS impersonation clears
# Cloudflare without them. Verified: sessionKey alone returns HTTP 200 on the
# usage API. Requiring cf_clearance here wrongly rejected logged-in users.
REQUIRED = ["sessionKey"]
HOME = Path.home()
# Generous by default so an interactive install waits for the user to read and
# click the Keychain prompt (5s was too short - the call was killed before a
# human could click, then mislabelled as "no keychain entry"). The launchd plist
# sets a short value via CLAUDE_KEYCHAIN_TIMEOUT_SECONDS so the background refresh
# never hangs on a prompt (which only appears when the key is not yet approved;
# once the user clicks "Always Allow" it is granted silently forever).
KEYCHAIN_TIMEOUT_SECONDS = float(os.environ.get("CLAUDE_KEYCHAIN_TIMEOUT_SECONDS", "90"))
API_VALIDATION_TIMEOUT_SECONDS = 10

BROWSERS = [
    ("Arc",      "Arc Safe Storage",      [HOME / "Library/Application Support/Arc/User Data/Default/Cookies",
                                           *sorted((HOME / "Library/Application Support/Arc/User Data").glob("Profile */Cookies"))]),
    ("Chrome",   "Chrome Safe Storage",   [HOME / "Library/Application Support/Google/Chrome/Default/Cookies",
                                           *sorted((HOME / "Library/Application Support/Google/Chrome").glob("Profile */Cookies"))]),
    ("Brave",    "Brave Safe Storage",    [HOME / "Library/Application Support/BraveSoftware/Brave-Browser/Default/Cookies",
                                           *sorted((HOME / "Library/Application Support/BraveSoftware/Brave-Browser").glob("Profile */Cookies"))]),
    ("Chromium", "Chromium Safe Storage", [HOME / "Library/Application Support/Chromium/Default/Cookies"]),
]


_KEYCHAIN_CACHE: dict = {}


def keychain_password(service: str) -> str | None:
    """Return Safe Storage password from the login keychain, or None on miss/timeout.

    Memoised per service for the life of the process. We probe many browser
    profiles that share a single Safe Storage key (every Chrome profile uses
    "Chrome Safe Storage"), and each `security` call can raise a macOS Keychain
    permission dialog. Caching means at most one prompt per browser per run
    instead of one per profile - which is what made the widget ask for
    permission over and over on machines with several profiles.
    """
    if service in _KEYCHAIN_CACHE:
        return _KEYCHAIN_CACHE[service]
    try:
        pw = subprocess.check_output(
            ["security", "find-generic-password", "-w", "-s", service],
            stderr=subprocess.DEVNULL,
            timeout=KEYCHAIN_TIMEOUT_SECONDS,
        ).decode().strip()
    except subprocess.TimeoutExpired:
        # Prompt shown but not answered within the timeout - transient. Do NOT
        # cache: the key becomes available once the user approves the dialog,
        # and caching None here would mask it for every later profile this run.
        return None
    except subprocess.CalledProcessError:
        # Genuine miss (browser not installed / no such keychain item). Safe to
        # cache so we do not re-shell `security` once per profile.
        _KEYCHAIN_CACHE[service] = None
        return None
    _KEYCHAIN_CACHE[service] = pw
    return pw


def candidates() -> list[tuple[str, str, Path, float]]:
    """Every existing cookie DB across known browsers, newest mtime first.

    mtime ordering is only a tie-breaker — the caller validates each cookie
    against the live API before accepting it.
    """
    found = []
    for label, service, paths in BROWSERS:
        for p in paths:
            if p.exists():
                found.append((label, service, p, p.stat().st_mtime))
    found.sort(key=lambda x: x[3], reverse=True)
    return found


def _derive_key(key_material: str) -> bytes:
    """PBKDF2-HMAC-SHA1 key derivation, matching Chromium's macOS cookie scheme."""
    kdf = PBKDF2HMAC(algorithm=SHA1(), length=16, salt=b"saltysalt", iterations=1003)
    return kdf.derive(key_material.encode("utf8"))


def _decrypt_value(encrypted: bytes, key: bytes, db_version: int) -> str | None:
    """Decrypt one Chromium cookie value, or None if it is not a decryptable
    v10/v11 AES-CBC blob (e.g. app-bound v20 encryption, or a wrong key)."""
    if encrypted[:3] not in (b"v10", b"v11"):
        return None
    body = encrypted[3:]
    if not body or len(body) % 16:  # AES-CBC needs whole 16-byte blocks; a
        return None                 # malformed cookie is skipped, not fatal
    cipher = Cipher(algorithms.AES(key), modes.CBC(b" " * 16))
    dec = cipher.decryptor()
    raw = dec.update(body) + dec.finalize()
    if db_version >= 24:
        # Chrome cookie DB v24+ prepends a 32-byte SHA256 domain hash to the
        # plaintext (chromium sqlite_persistent_cookie_store.cc). Strip it.
        raw = raw[32:]
    if not raw:
        return None
    pad = raw[-1]  # PKCS7 padding length
    if pad < 1 or pad > 16 or pad > len(raw):
        return None
    try:
        return raw[:-pad].decode("utf8")
    except UnicodeDecodeError:
        return None


def extract_cookies(cookie_file: Path, key_material: str) -> dict:
    """Decrypt this browser's claude.ai cookies using its Safe Storage key.

    Self-contained (mirrors pycookiecheat's macOS scheme: PBKDF2-HMAC-SHA1, 1003
    iterations, salt "saltysalt", AES-128-CBC, 16-space IV) so the widget touches
    the Keychain exactly once - via the memoised `security` fetch, a single
    Keychain subject - instead of pycookiecheat re-reading it as a *second*
    subject (the venv Python binary) on every call. That double lookup, times
    every browser profile, was the source of the repeated permission prompts.
    """
    key = _derive_key(key_material)
    con = sqlite3.connect(f"file:{cookie_file}?mode=ro", uri=True)
    con.text_factory = bytes  # values/blobs come back as bytes
    try:
        db_version = 0
        row = con.execute("select value from meta where key='version'").fetchone()
        if row and row[0] is not None:
            try:
                raw_v = row[0]
                db_version = int(raw_v.decode() if isinstance(raw_v, bytes) else raw_v)
            except (ValueError, TypeError, AttributeError):
                db_version = 0
        out: dict = {}
        # host_key params must be str (TEXT affinity); results are bytes.
        for host in ("claude.ai", ".claude.ai"):
            for name_b, value_b, enc in con.execute(
                "select name, value, encrypted_value from cookies where host_key = ?",
                (host,),
            ):
                name = name_b.decode("utf8", "replace")
                if value_b:  # already plaintext (rare)
                    out[name] = value_b.decode("utf8", "replace")
                elif enc:
                    dv = _decrypt_value(enc, key, db_version)
                    if dv is not None:
                        out[name] = dv
        return out
    finally:
        con.rollback()
        con.close()


def try_extract(service: str, cookie_file: Path):
    """Return (cookies_dict, None) on success or (None, reason_str) on failure.

    Fetches the browser's Safe Storage key once from the Keychain (via the
    memoised `security` helper - one Keychain subject, one prompt per browser)
    and decrypts the cookie DB in-process. No second subject, no per-profile
    prompts.
    """
    pw = keychain_password(service)
    if not pw:
        return None, (f"could not read the {service} key from the Keychain "
                      "(approve the prompt with 'Always Allow', or the key is absent)")
    try:
        cookies = extract_cookies(cookie_file, pw)
    except Exception as e:
        return None, f"decrypt failed: {e}"
    if all(k in cookies for k in REQUIRED):
        return cookies, None
    missing = [k for k in REQUIRED if k not in cookies]
    return None, f"key OK but no Claude session in this profile (missing {missing})"


def read_usage_url() -> str | None:
    if not CONFIG_PATH.exists():
        return None
    for line in CONFIG_PATH.read_text().splitlines():
        line = line.strip()
        if line.startswith("USAGE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def validate_cookies(cookies: dict, usage_url: str) -> tuple[bool, str]:
    """Hit the API with these cookies. Accept only on 200 + JSON + no account_session_invalid.

    Returns (ok, reason). curl_cffi is imported lazily so the rest of the module
    is usable even if curl_cffi takes a while to load on cold start.
    """
    try:
        from curl_cffi import requests
    except Exception as e:
        return False, f"curl_cffi import failed: {e}"

    cookie_dict = {k: cookies[k] for k in NEEDED if k in cookies}
    headers = {
        "accept": "*/*",
        "anthropic-client-platform": "web_claude_ai",
        "content-type": "application/json",
        "referer": "https://claude.ai/settings/usage",
    }
    try:
        resp = requests.get(
            usage_url,
            headers=headers,
            cookies=cookie_dict,
            impersonate="chrome",
            timeout=API_VALIDATION_TIMEOUT_SECONDS,
        )
    except Exception as e:
        return False, f"http error: {e}"

    if resp.status_code != 200:
        snippet = (resp.text or "")[:200].replace("\n", " ")
        return False, f"http {resp.status_code}: {snippet}"

    try:
        body = json.loads(resp.text)
    except Exception as e:
        snippet = (resp.text or "")[:200].replace("\n", " ")
        return False, f"non-json response: {e} :: {snippet}"

    body_str = json.dumps(body)
    if "account_session_invalid" in body_str:
        return False, "account_session_invalid"
    if isinstance(body, dict) and body.get("type") == "error":
        return False, f"api error: {body.get('error', {})}"

    return True, "ok"


def refresh():
    cands = candidates()
    if not cands:
        sys.stderr.write("No Chromium-family cookie DB found (Chrome/Arc/Brave/Chromium).\n")
        sys.exit(2)

    usage_url = read_usage_url()
    if not usage_url:
        sys.stderr.write(f"No USAGE_URL in {CONFIG_PATH}\n")
        sys.exit(2)

    attempts: list[str] = []
    for label, service, cookie_file, _mtime in cands:
        tag = f"{label}/{cookie_file.parent.name}"
        cookies, err = try_extract(service, cookie_file)
        if cookies is None:
            attempts.append(f"  [{tag}] skipped: {err}")
            continue
        ok, reason = validate_cookies(cookies, usage_url)
        if not ok:
            attempts.append(f"  [{tag}] rejected: {reason}")
            continue

        picked = {k: cookies[k] for k in NEEDED if k in cookies}
        write_config(picked)
        return label, cookie_file, picked

    sys.stderr.write("No valid Claude session found across browsers. Tried:\n")
    for line in attempts:
        sys.stderr.write(line + "\n")
    sys.stderr.write("\nFix: open claude.ai in a logged-in browser, then retry.\n")
    sys.exit(2)


def write_config(cookies_dict: dict) -> None:
    """Atomically rewrite the config's COOKIE= line.

    Holds an exclusive flock on a sidecar so concurrent SwiftBar plugin reads
    and other refresher runs never see a half-written file. Writes to a temp
    file in the same directory then os.replace() onto CONFIG_PATH.
    """
    cookie_str = "; ".join(f"{k}={v}" for k, v in cookies_dict.items())
    if not CONFIG_PATH.exists():
        sys.stderr.write(f"Config not found: {CONFIG_PATH}\n")
        sys.exit(2)

    # Acquire exclusive lock; close in finally so the lock fd never leaks
    lock_fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

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
        new_content = "\n".join(new_lines) + "\n"

        # Same-directory temp file so os.replace is atomic on the same volume
        dir_ = CONFIG_PATH.parent
        with tempfile.NamedTemporaryFile(
            mode="w", dir=str(dir_), prefix=".claude-usage-widget.", suffix=".tmp",
            delete=False, encoding="utf-8",
        ) as tmp:
            tmp.write(new_content)
            tmp_path = Path(tmp.name)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, CONFIG_PATH)
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


if __name__ == "__main__":
    try:
        label, cookie_file, picked = refresh()
        if os.environ.get("CLAUDE_USAGE_VERBOSE"):
            print(f"refreshed from {label} ({cookie_file}) — {len(picked)} cookies")
    except SystemExit:
        raise
    except Exception as e:
        sys.stderr.write(f"refresh error: {e}\n")
        sys.exit(1)
