#!/usr/bin/env python3
"""Minimal APNs client for pushing Live Activity updates, cross-platform.

Apple Push Notification service is just HTTP/2 + a short-lived ES256 JWT signed
with your APNs auth key (.p8). No Apple-only tooling required, so this relay runs
on macOS, Linux, and Windows alike.

Split into two layers:

* Pure builders — `b64url`, `make_jwt`, `build_headers`, `build_payload` — depend
  only on the stdlib plus `cryptography` (for ES256). These carry all the fiddly
  bits (the `.push-type.liveactivity` topic suffix, the DER→raw signature
  conversion, the `aps` envelope shape) and are fully unit-tested.
* `APNsClient` — wraps the above with JWT caching and an HTTP/2 transport. The
  transport is lazily imported (httpx preferred, curl_cffi fallback) so importing
  this module for tests needs neither.

References: Apple "Sending notification requests to APNs" and "Updating and ending
your Live Activities with ActivityKit push notifications".
"""
from __future__ import annotations

import base64
import json
import time

# APNs endpoints. Development builds (Xcode run on device) deliver via sandbox;
# TestFlight / App Store builds use production. Live Activities honour the same split.
HOST_PRODUCTION = "https://api.push.apple.com"
HOST_SANDBOX = "https://api.sandbox.push.apple.com"

# Reuse a signed JWT for well under Apple's 60-minute cap, and never mint a new
# one more than once per 20 minutes (Apple rejects over-eager refresh).
JWT_MAX_AGE_SECONDS = 3000  # 50 min


def b64url(data: bytes) -> str:
    """Base64url without padding, per JWS."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_jwt(team_id: str, key_id: str, private_key_pem: bytes, iat: int | None = None) -> str:
    """Build an ES256 JWT for APNs token auth from a .p8 key (PEM bytes).

    JWS ES256 requires the raw 64-byte (r||s) signature, but `cryptography`
    signs to DER — so we decode the DER pair and re-pack as two 32-byte ints.
    """
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric import utils as asym_utils

    if iat is None:
        iat = int(time.time())
    header = {"alg": "ES256", "kid": key_id}
    claims = {"iss": team_id, "iat": int(iat)}
    signing_input = (
        b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + b64url(json.dumps(claims, separators=(",", ":")).encode())
    )

    key = serialization.load_pem_private_key(private_key_pem, password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise ValueError("APNs auth key must be an EC (ES256) private key")
    der = key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))
    r, s = asym_utils.decode_dss_signature(der)
    raw_sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return signing_input + "." + b64url(raw_sig)


def liveactivity_topic(bundle_id: str) -> str:
    """The apns-topic for Live Activity pushes is the app bundle id plus a suffix."""
    return f"{bundle_id}.push-type.liveactivity"


def build_headers(bundle_id: str, jwt: str, *, priority: int = 10,
                  expiration: int = 0, apns_id: str | None = None) -> dict[str, str]:
    """HTTP/2 headers for a Live Activity push. `apns-push-type` MUST be
    'liveactivity' and the topic MUST carry the '.push-type.liveactivity' suffix,
    or APNs rejects the request outright."""
    headers = {
        "authorization": f"bearer {jwt}",
        "apns-topic": liveactivity_topic(bundle_id),
        "apns-push-type": "liveactivity",
        "apns-priority": str(priority),
        "apns-expiration": str(expiration),
    }
    if apns_id:
        headers["apns-id"] = apns_id
    return headers


def build_payload(content_state: dict, *, event: str = "update",
                  timestamp: int | None = None, stale_ts: int | None = None,
                  dismiss_ts: int | None = None, relevance: int | None = None,
                  alert: dict | None = None,
                  attributes_type: str | None = None,
                  attributes: dict | None = None) -> dict:
    """Assemble the APNs `aps` envelope for a Live Activity update/end/start.

    `content_state` must match the Swift `ContentState` exactly (same keys/types)
    — ActivityKit decodes it straight into that struct. For event='start'
    (push-to-start), pass `attributes_type` and `attributes` too.
    """
    if timestamp is None:
        timestamp = int(time.time())
    aps: dict = {
        "timestamp": int(timestamp),
        "event": event,
        "content-state": content_state,
    }
    if stale_ts is not None:
        aps["stale-date"] = int(stale_ts)
    if dismiss_ts is not None:
        aps["dismissal-date"] = int(dismiss_ts)
    if relevance is not None:
        aps["relevance-score"] = relevance
    if alert is not None:
        aps["alert"] = alert
    if event == "start":
        if attributes_type:
            aps["attributes-type"] = attributes_type
        if attributes is not None:
            aps["attributes"] = attributes
    return {"aps": aps}


class APNsResult:
    """Outcome of one push. `ok` is True on HTTP 200. `should_drop` is True when
    Apple says the token is dead (410, or 400 BadDeviceToken) — the relay then
    forgets it."""

    __slots__ = ("status", "apns_id", "reason", "body")

    def __init__(self, status: int, apns_id: str = "", reason: str = "", body: str = ""):
        self.status = status
        self.apns_id = apns_id
        self.reason = reason
        self.body = body

    @property
    def ok(self) -> bool:
        return self.status == 200

    @property
    def should_drop(self) -> bool:
        return self.status == 410 or self.reason in {
            "BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic",
        }

    def __repr__(self) -> str:
        return f"APNsResult(status={self.status}, reason={self.reason!r})"


class APNsClient:
    """Signs, caches the JWT, and posts Live Activity pushes over HTTP/2."""

    def __init__(self, team_id: str, key_id: str, private_key_pem: bytes,
                 bundle_id: str, *, production: bool = False, transport=None):
        self.team_id = team_id
        self.key_id = key_id
        self.private_key_pem = private_key_pem
        self.bundle_id = bundle_id
        self.host = HOST_PRODUCTION if production else HOST_SANDBOX
        self._transport = transport  # injectable for tests: (method,url,headers,body)->(status,hdrs,text)
        self._jwt = None
        self._jwt_iat = 0

    def _jwt_token(self) -> str:
        now = int(time.time())
        if self._jwt is None or (now - self._jwt_iat) >= JWT_MAX_AGE_SECONDS:
            self._jwt = make_jwt(self.team_id, self.key_id, self.private_key_pem, now)
            self._jwt_iat = now
        return self._jwt

    def push(self, device_token: str, content_state: dict, *, event: str = "update",
             stale_ts: int | None = None, dismiss_ts: int | None = None,
             priority: int = 10, alert: dict | None = None,
             relevance: int | None = None) -> APNsResult:
        payload = build_payload(content_state, event=event, stale_ts=stale_ts,
                                dismiss_ts=dismiss_ts, alert=alert, relevance=relevance)
        headers = build_headers(self.bundle_id, self._jwt_token(),
                                priority=priority, expiration=0)
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        url = f"{self.host}/3/device/{device_token}"
        transport = self._transport or _default_transport()
        status, resp_headers, text = transport("POST", url, headers, body)
        apns_id = ""
        reason = ""
        if resp_headers:
            apns_id = resp_headers.get("apns-id", "") or resp_headers.get("apns-id".title(), "")
        if status != 200 and text:
            try:
                reason = (json.loads(text) or {}).get("reason", "")
            except (json.JSONDecodeError, TypeError):
                reason = text[:120]
        return APNsResult(status, apns_id, reason, text or "")


# --- HTTP/2 transport --------------------------------------------------------
# Lazily selected so the pure builders above import with only `cryptography`.
# httpx (explicit http2=True) is preferred for reliable HTTP/2; curl_cffi (already
# a base dependency for fetch_usage) is the fallback. Either must speak HTTP/2 —
# APNs refuses HTTP/1.1.

def _default_transport():
    try:
        import httpx  # noqa: F401
        return _httpx_transport
    except ImportError:
        return _curl_transport


def _httpx_transport(method: str, url: str, headers: dict, body: bytes):
    import httpx
    with httpx.Client(http2=True, timeout=15) as client:
        resp = client.request(method, url, headers=headers, content=body)
        return resp.status_code, dict(resp.headers), resp.text


def _curl_transport(method: str, url: str, headers: dict, body: bytes):
    # curl_cffi is libcurl-backed; modern libcurl negotiates HTTP/2 over TLS via
    # ALPN. If APNs ever reports an HTTP/1.1 error, install httpx[http2].
    from curl_cffi import requests as creq
    resp = creq.request(method, url, headers=headers, data=body, timeout=15, http_version=2)
    return resp.status_code, dict(resp.headers), resp.text
