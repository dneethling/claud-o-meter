import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ios_relay  # noqa: E402  (top-level imports are stdlib + lazy; safe without curl_cffi)


def test_render_launchd_has_paths_and_keepalive():
    out = ios_relay.render_service("launchd", "/venv/bin/python",
                                   "/repo/ios_relay.py", "/Users/me")
    assert "com.dcai.claude-usage-ios-relay" in out
    assert "<string>/venv/bin/python</string>" in out
    assert "<string>/repo/ios_relay.py</string>" in out
    assert "<key>KeepAlive</key>" in out           # relaunch on exit
    assert "<string>/Users/me</string>" in out     # HOME for Keychain/config
    assert out.strip().endswith("</plist>")


def test_render_systemd_restart_always():
    out = ios_relay.render_service("systemd", "/venv/bin/python",
                                   "/repo/ios_relay.py", "/home/me")
    assert "ExecStart=/venv/bin/python /repo/ios_relay.py" in out
    assert "Restart=always" in out
    assert "Environment=HOME=/home/me" in out
    assert "WantedBy=default.target" in out


def test_render_service_rejects_unknown_kind():
    try:
        ios_relay.render_service("upstart", "/p", "/s", "/h")
    except ValueError:
        return
    raise AssertionError("expected ValueError for an unknown service kind")
