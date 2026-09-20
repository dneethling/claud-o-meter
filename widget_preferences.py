#!/usr/bin/env python3
"""Change only allowlisted display settings, preserving authentication and comments."""
from __future__ import annotations

import fcntl
import os
import sys
import tempfile
from pathlib import Path

OPTIONS = {
    "MENUBAR_MODE": {"claude", "codex", "both"},
    "THEME": {"semantic", "colorblind", "minimal"},
    "DETAIL_LEVEL": {"full", "compact"},
}


def set_preference(key: str, value: str, config: Path | None = None) -> None:
    if key not in OPTIONS or value not in OPTIONS[key]:
        raise ValueError("Unsupported display setting")
    config = config if config is not None else Path.home() / ".claude-usage-widget.conf"
    lock = config.with_name(config.name + ".lock")
    lock_fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    temp = None
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        # A missing config needs the installer, not an empty config with a theme.
        lines = config.read_text().splitlines(keepends=True)
        result = []
        replaced = False
        for line in lines:
            if line.startswith(key + "="):
                if not replaced:
                    result.append(f"{key}={value}\n")
                    replaced = True
            else:
                result.append(line)
        if not replaced:
            if result and not result[-1].endswith("\n"):
                result[-1] += "\n"
            result.append(f"{key}={value}\n")
        with tempfile.NamedTemporaryFile(mode="w", dir=config.parent, delete=False) as handle:
            temp = Path(handle.name)
            handle.write("".join(result))
        os.chmod(temp, 0o600)
        os.replace(temp, config)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
        os.close(lock_fd)


def main() -> int:
    try:
        if len(sys.argv) != 3:
            raise ValueError("Expected setting and value")
        set_preference(sys.argv[1], sys.argv[2])
        return 0
    except (OSError, ValueError):
        print("Could not update display settings. Check the widget configuration.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
