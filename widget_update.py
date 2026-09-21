#!/usr/bin/env python3
"""Bounded update checks and fast-forward installs with actionable status."""
from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = Path.home() / ".claude-usage-update-result.json"
LEGACY = Path.home() / ".claude-usage-update-status"
# Set once code has been fetched/merged but before dependencies and the launch
# agent are back in place. It outlives an offline check, a behind==0 check and a
# process restart, so a half-finished update keeps offering Retry until setup
# actually completes - it is never a transient "you are up to date".
PENDING = Path.home() / ".claude-usage-update-pending"
PENDING_MESSAGE = "Update downloaded but setup did not finish. Choose Retry update."


def atomic_write(path, text):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as f:
        temp = Path(f.name)
        f.write(text)
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _mark_pending(on):
    try:
        if on:
            PENDING.write_text("setup")
        else:
            PENDING.unlink(missing_ok=True)
    except OSError:
        pass


def _is_pending():
    return PENDING.exists()


def run(args, timeout=60):
    """Run a command in its own session so a timeout can kill the whole tree.

    subprocess.run(timeout=) only signals the direct child, leaving a hung git
    transport or pip build subprocess alive to overlap a later retry. Starting a
    new session makes the child a group leader, so on timeout we kill the group.
    """
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    with subprocess.Popen(args, cwd=ROOT, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True,
                          start_new_session=True, env=env) as proc:
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            proc.communicate()
            raise
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, args, out, err)
        return subprocess.CompletedProcess(args, proc.returncode, out, err)


def save(state, message, **extra):
    result = {"state": state, "message": message, "checked_at": int(time.time()), **extra}
    atomic_write(STATE, json.dumps(result))
    return result


def check(for_install=False):
    try:
        previous = json.loads(STATE.read_text())
    except (OSError, ValueError):
        previous = {}
    pending = _is_pending()
    try:
        run(["git", "fetch", "--quiet", "origin"])
    except (subprocess.SubprocessError, OSError):
        # An unfinished setup keeps its "Retry update" even while offline - and
        # even for an install() call, so an offline auto-retry does not downgrade
        # the pending error to a plain "offline / Retry check". Only a clean
        # checkout is allowed to report "offline" and move on.
        if pending:
            return save("error", PENDING_MESSAGE, version=previous.get("version", "?"))
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
        # A pending setup wins over "current"/"error"/new commits: finish it first.
        if pending and not for_install:
            return save("error", PENDING_MESSAGE, version=local)
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
    # Mark setup incomplete before any code changes; only a full success clears it.
    _mark_pending(True)
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
    _mark_pending(False)
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
            print("An update check or installation is already running.")
            return 3
        result = install() if action == "install" else check()
        # Auto-update only after a successful check; install never calls main,
        # so it cannot recurse back into automatic updates. A pending setup is
        # also finished automatically here, so it self-heals without a click.
        config = Path.home() / ".claude-usage-widget.conf"
        auto = config.exists() and "AUTO_UPDATE=1" in config.read_text().splitlines()
        if action == "check" and auto and (result["state"] == "available" or _is_pending()):
            result = install()
        print(result["message"])
        return 0 if result["state"] in ("current", "available", "installed") else 1
    finally:
        os.close(fd)


if __name__ == "__main__":
    sys.exit(main())
