#!/usr/bin/env python3
"""Bounded update checks and fast-forward installs with actionable status."""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = Path.home() / ".claude-usage-update-result.json"
LEGACY = Path.home() / ".claude-usage-update-status"


def atomic_write(path, text):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as f:
        temp = Path(f.name)
        f.write(text)
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def run(args, timeout=60):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                          timeout=timeout, check=True,
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})


def save(state, message, **extra):
    result = {"state": state, "message": message, "checked_at": int(time.time()), **extra}
    atomic_write(STATE, json.dumps(result))
    return result


def check(for_install=False):
    try:
        previous = json.loads(STATE.read_text())
    except (OSError, ValueError):
        previous = {}
    try:
        run(["git", "fetch", "--quiet", "origin"])
    except (subprocess.SubprocessError, OSError):
        return save("offline", "Could not check for updates. Check your connection and retry.")
    try:
        local = run(["git", "rev-parse", "--short", "HEAD"]).stdout.strip()
        upstream = run(["git", "rev-parse", "--abbrev-ref", "@{upstream}"]).stdout.strip()
        behind = int(run(["git", "rev-list", "--count", "HEAD.." + upstream]).stdout)
        ahead = int(run(["git", "rev-list", "--count", upstream + "..HEAD"]).stdout)
        dirty = bool(run(["git", "status", "--porcelain"]).stdout.strip())
        target = run(["git", "rev-parse", upstream]).stdout.strip()
        atomic_write(LEGACY, f"{behind} {local} {int(time.time())}\n")
        if dirty or (ahead and behind):
            return save("blocked", "Local changes need review before updating. Your files have been kept.", version=local)
        if not for_install and not behind and previous.get("state") == "error":
            return save("error", previous["message"], version=local)
        notes = run(["git", "log", "--format=%s", "-5", "HEAD.." + upstream]).stdout.splitlines() if behind else []
        return save("available" if behind else "current",
                    f"{behind} new change(s) ready to install." if behind else "You are up to date.",
                    version=local, target=target, changes=notes, behind=behind)
    except (subprocess.SubprocessError, ValueError, OSError):
        return save("blocked", "This checkout has no usable update branch. Re-run the installer or review the checkout.")


def install():
    result = check(for_install=True)
    if result["state"] not in ("available", "current"):
        return result
    save("installing", "Installing update…")
    phase = "downloaded code"
    try:
        # Fetch and check above, then merge the exact checked commit.
        if result.get("behind", 0):
            run(["git", "merge", "--ff-only", result["target"]])
        phase = "Python dependencies"
        python = ROOT / ".venv/bin/python"
        run([str(python), "-m", "pip", "install", "-q", "-r", "requirements.txt"], timeout=300)
        phase = "background refresh setup"
        run(["bash", str(ROOT / "agents.sh"), str(ROOT)], timeout=60)
        version = run(["git", "rev-parse", "--short", "HEAD"]).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return save("error", f"Update incomplete at {phase}. Choose Retry update; your configuration was kept.")
    atomic_write(LEGACY, f"0 {version} {int(time.time())}\n")
    return save("installed", "Update installed. Refresh the widget to use it.", version=version)


def main():
    action = sys.argv[1] if len(sys.argv) == 2 else ""
    if action not in ("check", "install"):
        return 2
    lock_path = Path.home() / ".claude-usage-update.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        result = install() if action == "install" else check()
        # Auto-update only after a successful check; install never calls main,
        # so it cannot recurse back into automatic updates.
        config = Path.home() / ".claude-usage-widget.conf"
        auto = config.exists() and "AUTO_UPDATE=1" in config.read_text().splitlines()
        if action == "check" and auto and result["state"] == "available":
            result = install()
        print(result["message"])
        return 0 if result["state"] in ("current", "available", "installed") else 1
    finally:
        os.close(fd)


if __name__ == "__main__":
    sys.exit(main())
