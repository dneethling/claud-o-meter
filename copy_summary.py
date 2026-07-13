#!/usr/bin/env python3
"""Print a plaintext usage snapshot for the clipboard (menu pipes this to pbcopy)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

RAW = Path("/tmp/claude-usage-raw.json")
CC = Path.home() / ".claude-usage-cc-summary.json"
CODEX = Path.home() / ".claude-usage-codex-summary.json"


def load(p):
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def human(n):
    if n is None:
        return "-"
    n = float(n)
    if n >= 1e9:
        return f"{n/1e9:.1f}B"
    if n >= 1e6:
        return f"{n/1e6:.0f}M"
    if n >= 1e3:
        return f"{n/1e3:.0f}k"
    return f"{n:.0f}"


lines = [f"Claude/Codex usage - {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M')}"]

raw = load(RAW)
if raw:
    s = raw.get("five_hour", {}).get("utilization")
    w = raw.get("seven_day", {}).get("utilization")
    if s is not None:
        lines.append(f"Claude session (5h): {s:.0f}%")
    if w is not None:
        lines.append(f"Claude weekly:       {w:.0f}%")
    spend = raw.get("spend", {})
    if spend.get("enabled") and spend.get("percent") is not None:
        lines.append(f"Credits:             {spend['percent']}%")

cc = load(CC)
if cc:
    lines.append(f"Claude Code today:   {human(cc.get('today', {}).get('total_tokens'))} tokens")
    lines.append(f"Claude Code 7d:      {human(cc.get('week', {}).get('total_tokens'))} tokens")

codex = load(CODEX)
if codex and codex.get("available"):
    lines.append(f"Codex today:         {human(codex.get('today', {}).get('tokens'))} tokens")
    lines.append(f"Codex 7d:            {human(codex.get('week', {}).get('tokens'))} tokens")

print("\n".join(lines))
