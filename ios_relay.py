#!/usr/bin/env python3
"""Cross-platform relay: fetch Claude usage, push it to the iOS Live Activity.

Runs on macOS, Linux, or Windows. Every POLL_SECONDS it fetches your Claude usage
(reusing `fetch_usage.fetch_usage_json`, the exact same call the SwiftBar widget
makes), maps it to the compact Live Activity content-state (`lib/usage_state`),
and pushes it to Apple Push Notification service (`lib/apns`) so the Dynamic
Island pill updates in near-real-time while you code.

The phone registers itself: the iOS app starts a Live Activity, gets a push token
from ActivityKit, and POSTs it to this relay's small HTTP endpoint on your LAN.
No inbound cloud service, no account on our side — just your machine talking to
Apple.

Auth reuse: usage comes from `~/.claude-usage-widget.conf` (USAGE_URL + COOKIE),
the same file the widget uses. On macOS, if a fetch fails, the relay reruns the
widget's `refresh_cookie.py` once (Keychain-based auto-recovery). On Windows/Linux
there is no Keychain, so you keep a valid COOKIE in that file yourself.

Secrets live in `~/.claude-usage-ios.conf` (mode 600) and are NEVER committed.
Run `python ios_relay.py --init` to scaffold it.

Commands:
  python ios_relay.py                 # run the daemon (HTTP receiver + push loop)
  python ios_relay.py --init          # write a blank ~/.claude-usage-ios.conf
  python ios_relay.py --once          # one fetch + push to stored tokens, then exit
  python ios_relay.py --print-state   # fetch once, print the content-state (no push)
  python ios_relay.py --self-test     # build a demo payload, print it (no creds/net)
  python ios_relay.py --register-token HEX [--activity-id ID]
  python ios_relay.py --list-tokens
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "lib"))

import apns  # noqa: E402  (top-level import is cheap; cryptography is lazy)
import usage_state  # noqa: E402

IOS_CONF = Path.home() / ".claude-usage-ios.conf"
TOKENS_PATH = Path.home() / ".claude-usage-ios-tokens.json"
WIDGET_CONF_DEFAULT = Path.home() / ".claude-usage-widget.conf"

DEFAULTS = {
    "APNS_ENV": "sandbox",         # dev builds deliver via sandbox; TestFlight/App Store use production
    "RELAY_PORT": "8787",
    "RELAY_BIND": "0.0.0.0",
    "POLL_SECONDS": "30",          # data-refresh cadence; lower risks Claude rate-limits
    "RELAY_SECRET": "",            # optional shared secret the app must send as X-Relay-Secret
    "WIDGET_CONF": str(WIDGET_CONF_DEFAULT),
}

CONF_TEMPLATE = """\
# claud-o-meter iOS relay config  (mode 600 - never commit this file)
#
# Get these from https://developer.apple.com/account :
#   APNS_TEAM_ID  - Membership details -> Team ID (10 chars)
#   APNS_KEY_ID   - Certificates, IDs & Profiles -> Keys -> your APNs .p8 key's ID
#   APNS_P8       - path to the .p8 auth key you downloaded (downloadable ONCE)
#   APNS_BUNDLE_ID- your app's bundle id, e.g. co.dcai.claudemeter (must match the
#                   Xcode target and the relay, or APNs rejects the push)
#
APNS_TEAM_ID=
APNS_KEY_ID=
APNS_P8=~/.claude-usage-AuthKey.p8
APNS_BUNDLE_ID=
# sandbox for a Debug build run from Xcode; production for TestFlight/App Store.
APNS_ENV=sandbox

# Local push relay the phone connects to (same Wi-Fi). Point the iOS app at
# http://<this-machine-LAN-IP>:8787
RELAY_PORT=8787
RELAY_BIND=0.0.0.0
# Optional: set a random string here and in the app to stop other LAN devices
# registering. Leave blank for none.
RELAY_SECRET=

# How often to refetch usage (seconds). 30 feels live without hammering Claude.
POLL_SECONDS=30

# Where the widget keeps USAGE_URL + COOKIE (usually leave as-is).
WIDGET_CONF=~/.claude-usage-widget.conf
"""

_tokens_lock = threading.Lock()


# --- config / token store ----------------------------------------------------

def read_kv(path) -> dict:
    """Tolerant KEY=VALUE reader (no exit on missing file, unlike the widget's)."""
    cfg: dict[str, str] = {}
    p = Path(os.path.expanduser(str(path)))
    if not p.exists():
        return cfg
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


def load_ios_conf() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(read_kv(IOS_CONF))
    cfg["WIDGET_CONF"] = os.path.expanduser(cfg["WIDGET_CONF"])
    return cfg


def load_tokens() -> dict:
    if not TOKENS_PATH.exists():
        return {}
    try:
        return json.loads(TOKENS_PATH.read_text()).get("tokens", {})
    except (json.JSONDecodeError, OSError):
        return {}


def save_tokens(tokens: dict) -> None:
    tmp = TOKENS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps({"tokens": tokens}, indent=2))
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass  # Windows may not honour chmod; the token is not a secret anyway.
    tmp.replace(TOKENS_PATH)


# --- usage fetch -------------------------------------------------------------

def _error_state(status: str, note: str) -> dict:
    """A content-state that tells the pill something is wrong instead of going
    silently stale."""
    return {
        "session_pct": None, "session_color": "gray", "session_reset": "",
        "weekly_pct": None, "weekly_color": "gray", "weekly_reset": "",
        "models": [], "credits": None, "on_credits": False,
        "status": status, "note": (note or "")[:120],
    }


def _looks_like_auth(err: str | None) -> bool:
    e = (err or "").lower()
    return ("account_session_invalid" in e or "http 401" in e or "http 403" in e
            or "401" in e or "403" in e or "no valid claude session" in e)


def _maybe_refresh_cookie(cfg: dict) -> bool:
    """macOS only: rerun the widget's Keychain-based cookie refresher once."""
    if platform.system() != "Darwin":
        return False
    refr = REPO / "refresh_cookie.py"
    if not refr.exists():
        return False
    venv_py = REPO / ".venv" / "bin" / "python"
    py = str(venv_py) if venv_py.exists() else sys.executable
    try:
        subprocess.run([py, str(refr)], timeout=30, capture_output=True)
        return True
    except (subprocess.SubprocessError, OSError):
        return False


def fetch_state(cfg: dict) -> tuple[dict, str]:
    """Fetch usage and map it to a content-state. Returns (state, status)."""
    widget = read_kv(cfg["WIDGET_CONF"])
    url = widget.get("USAGE_URL")
    cookie = widget.get("COOKIE")
    if not url or not cookie or "PASTE" in (url or ""):
        return _error_state("reauth", "No Claude session in widget config"), "reauth"

    try:
        import fetch_usage  # lazy: needs curl_cffi, only when we actually fetch
    except ImportError as e:
        return _error_state("error", f"fetch module unavailable: {e}"), "error"

    body, err = fetch_usage.fetch_usage_json(url, cookie)
    if body is None and _maybe_refresh_cookie(cfg):
        widget = read_kv(cfg["WIDGET_CONF"])
        body, err = fetch_usage.fetch_usage_json(url, widget.get("COOKIE") or "")

    if body is None:
        status = "reauth" if _looks_like_auth(err) else "error"
        return _error_state(status, err or "fetch failed"), status

    try:
        raw = json.loads(body)
    except json.JSONDecodeError as e:
        return _error_state("error", f"unparseable usage json: {e}"), "error"

    state = usage_state.build_state(raw)
    state["status"] = "ok"
    state["note"] = ""
    return state, "ok"


# --- APNs client -------------------------------------------------------------

def build_client(cfg: dict):
    """Construct an APNsClient from config, or (None, reason) if not configured."""
    need = ["APNS_TEAM_ID", "APNS_KEY_ID", "APNS_P8", "APNS_BUNDLE_ID"]
    missing = [k for k in need if not cfg.get(k)]
    if missing:
        return None, f"APNs not configured (missing: {', '.join(missing)})"
    p8 = Path(os.path.expanduser(cfg["APNS_P8"]))
    if not p8.exists():
        return None, f"APNs auth key not found at {p8}"
    try:
        pem = p8.read_bytes()
    except OSError as e:
        return None, f"cannot read APNs key: {e}"
    production = cfg.get("APNS_ENV", "sandbox").lower() == "production"
    client = apns.APNsClient(cfg["APNS_TEAM_ID"], cfg["APNS_KEY_ID"], pem,
                             cfg["APNS_BUNDLE_ID"], production=production)
    return client, None


def push_to_all(client, state: dict, poll: int) -> dict:
    """Stamp the state with send time and push it to every registered token,
    dropping any Apple reports as dead. Returns the state actually sent."""
    now = int(time.time())
    sent = dict(state)
    sent["updated_epoch"] = now
    stale = now + poll * 4  # let the pill show ~4 missed ticks before 'stale'
    with _tokens_lock:
        tokens = load_tokens()
        dead = []
        for tok, meta in list(tokens.items()):
            try:
                res = client.push(tok, sent, stale_ts=stale)
            except Exception as e:  # transport/credential error — keep the token, log once
                meta["last_status"] = 0
                meta["last_reason"] = str(e)[:120]
                continue
            meta["last_status"] = res.status
            meta["last_reason"] = res.reason
            if res.should_drop:
                dead.append(tok)
        for tok in dead:
            tokens.pop(tok, None)
        save_tokens(tokens)
    if dead:
        log(f"dropped {len(dead)} dead token(s)")
    return sent


# --- HTTP receiver (phone registers its push token here) ---------------------

class RelayHandler(BaseHTTPRequestHandler):
    relay = None  # set to the Relay instance before serving

    def _send(self, code: int, obj: dict):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self) -> bool:
        secret = self.relay.cfg.get("RELAY_SECRET", "")
        if not secret:
            return True
        return self.headers.get("X-Relay-Secret", "") == secret

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length", 0) or 0)
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return {}

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/health"):
            with _tokens_lock:
                count = len(load_tokens())
            self._send(200, {
                "ok": True,
                "tokens": count,
                "status": self.relay.last_status,
                "updated_epoch": self.relay.last_state.get("updated_epoch"),
                "poll_seconds": self.relay.poll,
                "push_enabled": self.relay.client is not None,
            })
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if not self._authed():
            self._send(401, {"error": "bad secret"})
            return
        data = self._read_json()
        if self.path.startswith("/register"):
            token = (data.get("token") or "").strip()
            if not token:
                self._send(400, {"error": "missing token"})
                return
            with _tokens_lock:
                tokens = load_tokens()
                tokens[token] = {
                    "activityId": data.get("activityId", ""),
                    "added": int(time.time()),
                    "last_status": None, "last_reason": "",
                }
                save_tokens(tokens)
            log(f"registered token …{token[-8:]}")
            # Push the latest state immediately so the pill fills in without waiting.
            self.relay.push_now()
            self._send(200, {"ok": True})
        elif self.path.startswith("/unregister"):
            token = (data.get("token") or "").strip()
            with _tokens_lock:
                tokens = load_tokens()
                existed = tokens.pop(token, None) is not None
                save_tokens(tokens)
            self._send(200, {"ok": True, "removed": existed})
        elif self.path.startswith("/push"):
            self.relay.push_now()
            self._send(200, {"ok": True})
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, *args):  # silence default stderr spam
        pass


# --- daemon ------------------------------------------------------------------

def log(msg: str) -> None:
    sys.stderr.write(f"[ios-relay] {msg}\n")
    sys.stderr.flush()


class Relay:
    def __init__(self, cfg: dict, client):
        self.cfg = cfg
        self.client = client
        self.poll = max(5, int(cfg.get("POLL_SECONDS", "30") or "30"))
        self.last_state: dict = {}
        self.last_status = "starting"
        self._last_core = None
        self._last_push = 0.0

    def _core(self, state: dict) -> str:
        """State minus the send timestamp, for change detection."""
        core = {k: v for k, v in state.items() if k != "updated_epoch"}
        return json.dumps(core, sort_keys=True)

    def push_now(self) -> None:
        if self.client is None or not self.last_state:
            return
        self.last_state = push_to_all(self.client, self.last_state, self.poll)
        self._last_core = self._core(self.last_state)
        self._last_push = time.time()

    def tick(self) -> None:
        state, status = fetch_state(self.cfg)
        self.last_state = state
        self.last_status = status
        if self.client is None:
            return
        core = self._core(state)
        keepalive_due = (time.time() - self._last_push) >= self.poll * 2
        if core != self._last_core or keepalive_due:
            self.last_state = push_to_all(self.client, state, self.poll)
            self._last_core = self._core(self.last_state)
            self._last_push = time.time()

    def run(self) -> None:
        while True:
            try:
                self.tick()
            except Exception as e:  # never let one bad tick kill the daemon
                log(f"tick error: {e}")
            time.sleep(self.poll)


def serve(cfg: dict, client) -> None:
    relay = Relay(cfg, client)
    RelayHandler.relay = relay

    port = int(cfg.get("RELAY_PORT", "8787") or "8787")
    bind = cfg.get("RELAY_BIND", "0.0.0.0") or "0.0.0.0"
    httpd = ThreadingHTTPServer((bind, port), RelayHandler)

    t = threading.Thread(target=relay.run, daemon=True)
    t.start()

    push_note = "push ON" if client else "push OFF (APNs not configured)"
    log(f"listening on http://{bind}:{port}  ({push_note}, poll {relay.poll}s)")
    log(f"point the iOS app at  http://<this-machine-LAN-IP>:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("shutting down")
        httpd.shutdown()


# --- one-shot / helper commands ----------------------------------------------

def cmd_init(force: bool) -> int:
    if IOS_CONF.exists() and not force:
        print(f"{IOS_CONF} already exists (use --force to overwrite).")
        return 0
    IOS_CONF.write_text(CONF_TEMPLATE)
    try:
        os.chmod(IOS_CONF, 0o600)
    except OSError:
        pass
    print(f"Wrote {IOS_CONF} (mode 600). Fill in APNS_* from developer.apple.com, "
          "then run: python ios_relay.py")
    return 0


def _demo_raw() -> dict:
    """A representative usage payload for --self-test (no network needed)."""
    return {
        "five_hour": {"utilization": 29, "resets_at": "2026-07-19T18:30:00Z"},
        "seven_day": {"utilization": 7, "resets_at": "2026-07-22T09:00:00Z"},
        "limits": [
            {"scope": {"model": {"display_name": "Opus"}}, "percent": 12,
             "resets_at": "2026-07-22T09:00:00Z"},
            {"scope": {"model": {"display_name": "Sonnet"}}, "percent": 4,
             "resets_at": "2026-07-22T09:00:00Z"},
        ],
        "spend": {"enabled": True,
                  "used": {"amount_minor": 120, "currency": "USD", "exponent": 2},
                  "limit": {"amount_minor": 5000, "currency": "USD", "exponent": 2},
                  "percent": 2},
    }


def cmd_self_test() -> int:
    state = usage_state.build_state(_demo_raw())
    state["status"] = "ok"
    state["note"] = ""
    state["updated_epoch"] = int(time.time())
    payload = apns.build_payload(state, stale_ts=state["updated_epoch"] + 120)
    print(json.dumps(payload, indent=2))
    return 0


def cmd_print_state(cfg: dict) -> int:
    state, status = fetch_state(cfg)
    state["updated_epoch"] = int(time.time())
    print(json.dumps(state, indent=2))
    return 0 if status == "ok" else 1


def cmd_once(cfg: dict) -> int:
    client, reason = build_client(cfg)
    if client is None:
        log(reason)
        return 2
    state, status = fetch_state(cfg)
    sent = push_to_all(client, state, max(5, int(cfg.get("POLL_SECONDS", "30"))))
    print(json.dumps({"status": status, "sent": sent}, indent=2))
    return 0 if status == "ok" else 1


def cmd_register_token(token: str, activity_id: str) -> int:
    token = token.strip()
    if not token:
        log("empty token")
        return 2
    with _tokens_lock:
        tokens = load_tokens()
        tokens[token] = {"activityId": activity_id, "added": int(time.time()),
                         "last_status": None, "last_reason": ""}
        save_tokens(tokens)
    print(f"stored token …{token[-8:]} ({len(tokens)} total)")
    return 0


def cmd_list_tokens() -> int:
    tokens = load_tokens()
    if not tokens:
        print("no tokens registered")
        return 0
    for tok, meta in tokens.items():
        print(f"…{tok[-12:]}  status={meta.get('last_status')}  "
              f"reason={meta.get('last_reason','')}  activity={meta.get('activityId','')}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Claude usage -> iOS Live Activity relay")
    ap.add_argument("--init", action="store_true", help="write a blank ~/.claude-usage-ios.conf")
    ap.add_argument("--force", action="store_true", help="with --init, overwrite existing config")
    ap.add_argument("--once", action="store_true", help="one fetch + push, then exit")
    ap.add_argument("--print-state", action="store_true", help="fetch once, print content-state")
    ap.add_argument("--self-test", action="store_true", help="print a demo payload (no creds/net)")
    ap.add_argument("--register-token", metavar="HEX", help="store a Live Activity push token")
    ap.add_argument("--activity-id", default="", help="optional id to tag a registered token")
    ap.add_argument("--list-tokens", action="store_true", help="show registered tokens")
    args = ap.parse_args(argv)

    if args.init:
        return cmd_init(args.force)
    if args.self_test:
        return cmd_self_test()
    if args.list_tokens:
        return cmd_list_tokens()
    if args.register_token:
        return cmd_register_token(args.register_token, args.activity_id)

    cfg = load_ios_conf()
    if args.print_state:
        return cmd_print_state(cfg)
    if args.once:
        return cmd_once(cfg)

    # Default: run the daemon.
    client, reason = build_client(cfg)
    if client is None:
        log(f"WARNING: {reason}")
        log("running receiver-only (tokens will be stored but nothing is pushed).")
    return serve(cfg, client) or 0


if __name__ == "__main__":
    sys.exit(main())
