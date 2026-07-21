import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import apns  # noqa: E402


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _demo_ec_pem() -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def test_b64url_strips_padding():
    assert apns.b64url(b"") == ""
    assert apns.b64url(b"any carnal pleasure.") == "YW55IGNhcm5hbCBwbGVhc3VyZS4"


def test_liveactivity_topic_suffix():
    assert apns.liveactivity_topic("co.dcai.claudemeter") == \
        "co.dcai.claudemeter.push-type.liveactivity"


def test_make_jwt_structure_and_claims():
    pem = _demo_ec_pem()
    tok = apns.make_jwt("TEAM123456", "KEY7654321", pem, iat=1_700_000_000)
    parts = tok.split(".")
    assert len(parts) == 3
    header = json.loads(_b64url_decode(parts[0]))
    claims = json.loads(_b64url_decode(parts[1]))
    assert header == {"alg": "ES256", "kid": "KEY7654321"}
    assert claims == {"iss": "TEAM123456", "iat": 1_700_000_000}
    assert len(_b64url_decode(parts[2])) == 64  # raw r||s, not DER


def test_make_jwt_signature_verifies():
    """The signature must validate against the public key as real ES256/JWS."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric import utils as au

    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    tok = apns.make_jwt("T", "K", pem, iat=1_700_000_000)
    h, c, sig = tok.split(".")
    raw = _b64url_decode(sig)
    r = int.from_bytes(raw[:32], "big")
    s = int.from_bytes(raw[32:], "big")
    der = au.encode_dss_signature(r, s)
    # Raises InvalidSignature if the JWS signature is wrong — no assert needed.
    key.public_key().verify(der, f"{h}.{c}".encode(), ec.ECDSA(hashes.SHA256()))


def test_build_headers_required_apns_fields():
    h = apns.build_headers("co.dcai.claudemeter", "JWTVALUE", priority=10)
    assert h["apns-push-type"] == "liveactivity"
    assert h["apns-topic"] == "co.dcai.claudemeter.push-type.liveactivity"
    assert h["authorization"] == "bearer JWTVALUE"
    assert h["apns-priority"] == "10"


def test_build_payload_update_shape():
    p = apns.build_payload({"session_pct": 29}, event="update",
                           timestamp=1_700_000_000, stale_ts=1_700_000_120)
    aps = p["aps"]
    assert aps["event"] == "update"
    assert aps["timestamp"] == 1_700_000_000
    assert aps["stale-date"] == 1_700_000_120
    assert aps["content-state"] == {"session_pct": 29}


def test_build_payload_start_includes_attributes():
    p = apns.build_payload({"x": 1}, event="start", timestamp=1,
                           attributes_type="ClaudeUsageAttributes",
                           attributes={"name": "usage"})
    assert p["aps"]["attributes-type"] == "ClaudeUsageAttributes"
    assert p["aps"]["attributes"] == {"name": "usage"}


def test_result_should_drop_logic():
    assert apns.APNsResult(200).ok is True
    assert apns.APNsResult(200).should_drop is False
    assert apns.APNsResult(410).should_drop is True
    assert apns.APNsResult(400, reason="BadDeviceToken").should_drop is True
    assert apns.APNsResult(400, reason="PayloadTooLarge").should_drop is False


def test_client_push_uses_injected_transport():
    calls = {}

    def fake_transport(method, url, headers, body):
        calls["method"] = method
        calls["url"] = url
        calls["headers"] = headers
        calls["body"] = body
        return 200, {"apns-id": "ABC-123"}, ""

    client = apns.APNsClient("TEAM", "KEY", _demo_ec_pem(),
                             "co.dcai.claudemeter", transport=fake_transport)
    res = client.push("DEADBEEFTOKEN", {"session_pct": 42}, stale_ts=1_700_000_120)

    assert res.ok is True
    assert res.apns_id == "ABC-123"
    assert calls["method"] == "POST"
    assert calls["url"].endswith("/3/device/DEADBEEFTOKEN")
    assert "sandbox" in calls["url"]  # default env is sandbox
    assert calls["headers"]["apns-push-type"] == "liveactivity"
    sent = json.loads(calls["body"])
    assert sent["aps"]["content-state"] == {"session_pct": 42}
    assert sent["aps"]["stale-date"] == 1_700_000_120


def test_client_push_maps_drop_reason():
    def fake_transport(method, url, headers, body):
        return 410, {}, json.dumps({"reason": "Unregistered"})

    client = apns.APNsClient("TEAM", "KEY", _demo_ec_pem(),
                             "co.dcai.claudemeter", transport=fake_transport)
    res = client.push("TOKEN", {"x": 1})
    assert res.ok is False
    assert res.reason == "Unregistered"
    assert res.should_drop is True


def test_client_production_env_uses_production_host():
    def fake_transport(method, url, headers, body):
        return 200, {}, ""

    client = apns.APNsClient("TEAM", "KEY", _demo_ec_pem(),
                             "co.dcai.claudemeter", production=True,
                             transport=fake_transport)
    client.push("TOKEN", {"x": 1})
    # host is captured on the client
    assert client.host == apns.HOST_PRODUCTION
