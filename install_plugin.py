#!/usr/bin/env python3
"""Add a launcher to an existing SwiftBar folder without replacing other widgets."""
import os
import shlex
import sys
import tempfile
from pathlib import Path

MARKER = "# Managed by Claud-o-meter installer"


def install_launcher(directory, root):
    directory, root = Path(directory), Path(root).resolve()
    if not directory.is_dir():
        raise ValueError("SwiftBar's plugin folder is missing. Choose an existing folder in SwiftBar first.")
    # Reuse our known filename to avoid duplicate meters. Do not overwrite a
    # manually installed plugin or an unrelated file, including a symlink.
    target = directory / "claude-usage.5m.sh"
    if target.is_symlink() or (target.exists() and MARKER not in target.read_text().splitlines()):
        raise ValueError("A Claude widget already exists in SwiftBar's folder. Keep or move it before installing this copy.")
    content = ("#!/bin/bash\n" + MARKER + "\n# <bitbar.title>Claude Usage</bitbar.title>\n"
               + "export CLAUDE_WIDGET_DIR=" + shlex.quote(str(root)) + "\n"
               + "exec /bin/bash " + shlex.quote(str(root / "plugins/claude-usage.5m.sh")) + "\n")
    with tempfile.NamedTemporaryFile(mode="w", dir=directory, delete=False, prefix=".claudometer-") as f:
        temp = Path(f.name)
        f.write(content)
    try:
        os.chmod(temp, 0o755)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        install_launcher(sys.argv[1], sys.argv[2])
    except (OSError, ValueError, IndexError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
